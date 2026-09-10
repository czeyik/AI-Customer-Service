import re
from datetime import datetime
from zoneinfo import ZoneInfo

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditLog, Conversation, MediaAttachment, Message
from app.schemas import ChatRequest, ChatResponse
from app.services.answer_generation import ApprovedKnowledgeResponder
from app.services.guardrails import SafetyAssessment, assess_message, is_account_action_request
from app.services.language import detect_language, selected_language
from app.services.pii import redact_sensitive
from app.services.rate_limit import rate_limiter
from app.services.retrieval import search_knowledge
from app.services.tickets import create_ticket, normalize_phone_number, to_ticket_response
from app.services.ticket_operations import reopen_closed_ticket_for_customer


PRIVACY_NOTICE_URL = "https://duducar.co/privacy-notice"
EMAIL_RE = re.compile(r"[^\s<>]+@[^\s<>]+")


def human_support_is_open(now: datetime | None = None) -> bool:
    local_now = now or datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
    return 9 <= local_now.hour < 18


class ChatbotService:
    def __init__(self) -> None:
        self.answer_generator = ApprovedKnowledgeResponder()

    def handle(
        self,
        db: Session,
        request: ChatRequest,
        ip_address: str | None = None,
        *,
        commit: bool = True,
    ) -> ChatResponse:
        settings = get_settings()
        limiter_keys = []
        if ip_address and request.channel != "whatsapp":
            limiter_keys.append(f"chat-ip:{ip_address}")
        if request.channel != "whatsapp":
            limiter_keys.append(f"chat-user:{request.channel}:{request.external_user_id}")
        if not all(
            rate_limiter.allow(
                db,
                key,
                settings.rate_limit_messages_per_minute,
                max_keys=settings.rate_limit_max_keys,
            )
            for key in limiter_keys
        ):
            if commit:
                db.commit()
            return ChatResponse(
                answer="Too many messages in a short time. Please wait a moment and try again.",
                language=request.preferred_language or "en",
                safety_flags=["rate_limited"],
            )

        try:
            conversation, is_new = self._get_or_create_conversation(db, request)
            reopen_closed_ticket_for_customer(
                db, channel=request.channel, external_user_id=request.external_user_id
            )
            language = self._select_language(conversation, request)
            request.preferred_language = language
            conversation.preferred_language = language
            if request.user_role != "unknown" or not conversation.user_role:
                conversation.user_role = request.user_role

            redaction = redact_sensitive(request.text)
            assessment = assess_message(redaction.text, request.attachments)
            if conversation.intake_state == "idle":
                conversation.risk_level = assessment.urgency
            db.add(
                Message(
                    conversation_id=conversation.id,
                    direction="inbound",
                    content=redaction.text,
                    language=language,
                    safety_flags=assessment.flags + redaction.findings,
                    payload={
                        "channel": request.channel,
                        "attachments": [item.model_dump() for item in request.attachments],
                    },
                )
            )

            response = self._respond(
                db, conversation, request, redaction.text, assessment, redaction.findings
            )
            if is_new:
                response.answer = f"{self._bot_identity(language)}\n\n{response.answer}"
            self._store_outbound(db, conversation.id, response)
            if commit:
                db.commit()
            else:
                db.flush()
            return response
        except Exception:
            db.rollback()
            raise

    def _respond(
        self,
        db: Session,
        conversation: Conversation,
        request: ChatRequest,
        text: str,
        assessment: SafetyAssessment,
        redaction_findings: list[str],
    ) -> ChatResponse:
        language = request.preferred_language or "en"
        flags = assessment.flags + redaction_findings

        if "prompt_injection_attempt" in assessment.flags:
            return ChatResponse(
                answer=self._security_refusal(language), language=language, safety_flags=flags
            )
        if "sensitive_attachment_rejected" in assessment.flags:
            return ChatResponse(
                answer=self._sensitive_information_refusal(language),
                language=language,
                safety_flags=flags,
            )

        if conversation.intake_state != "idle":
            return self._continue_intake(db, conversation, request, text, assessment, flags)

        prohibited_action = is_account_action_request(text)
        wants_ticket = request.create_ticket or assessment.should_create_ticket or prohibited_action
        if wants_ticket:
            issue_type = assessment.issue_type
            if prohibited_action and issue_type == "general_faq":
                issue_type = "prohibited_action_request"
            return self._start_intake(
                db,
                conversation,
                request,
                text,
                issue_type,
                assessment.urgency,
                flags + (["account_action_blocked"] if prohibited_action else []),
                prohibited_action,
            )

        retrieval = search_knowledge(db, text, language)
        if retrieval.confidence < get_settings().retrieval_min_confidence:
            return self._start_intake(
                db,
                conversation,
                request,
                text,
                "unconfirmed_question",
                "normal",
                flags,
                False,
                lead=self._unconfirmed(language),
            )

        return ChatResponse(
            answer=self.answer_generator.generate(language, retrieval.chunks),
            language=language,
            confidence=retrieval.confidence,
            safety_flags=flags,
            sources=[chunk.source_title for chunk in retrieval.chunks],
        )

    def _start_intake(
        self,
        db: Session,
        conversation: Conversation,
        request: ChatRequest,
        description: str,
        issue_type: str,
        urgency: str,
        flags: list[str],
        prohibited_action: bool,
        lead: str | None = None,
    ) -> ChatResponse:
        data = {
            "started_at": datetime.utcnow().isoformat(),
            "description": description,
            "issue_type": issue_type,
            "urgency": urgency,
            "safety_flags": flags,
            "account_id": request.account_id,
            "trip_id": request.trip_id,
            "ride_details": None,
            "ride_details_collected": issue_type == "partnership",
            "details_complete": issue_type == "partnership",
            "evidence": [item.model_dump(mode="json") for item in request.attachments],
            "attachment_count": len(request.attachments),
        }
        if issue_type == "partnership" and conversation.user_role == "unknown":
            conversation.user_role = "business_partner"
        conversation.risk_level = urgency
        if request.consent_to_ticket:
            data["consent"] = True
            self._capture_supplied(data, request, include_attachments=False)
            next_state = self._next_intake_state(data)
            if next_state is None:
                return self._create_ticket(db, conversation, request, data)
            conversation.intake_state = next_state
        else:
            conversation.intake_state = "awaiting_consent"
        data["lead_shown"] = True
        conversation.intake_data = data

        prefix = lead or self._intake_lead(
            request.preferred_language or "en", issue_type, prohibited_action
        )
        prompt = self._state_prompt(request.preferred_language or "en", conversation.intake_state)
        return ChatResponse(
            answer=f"{prefix}\n\n{prompt}",
            language=request.preferred_language or "en",
            safety_flags=flags,
            needs_ticket_consent=conversation.intake_state == "awaiting_consent",
        )

    def _continue_intake(
        self,
        db: Session,
        conversation: Conversation,
        request: ChatRequest,
        text: str,
        assessment: SafetyAssessment,
        flags: list[str],
    ) -> ChatResponse:
        language = request.preferred_language or "en"
        data = dict(conversation.intake_data or {})
        if self._is_cancel(text, language) or self._consent_answer(text, language) is False:
            conversation.intake_state = "idle"
            conversation.intake_data = {}
            return ChatResponse(answer=self._ticket_declined(language), language=language)

        if assessment.is_safety_critical and data.get("urgency") != "urgent":
            data.update(
                description=f"{data.get('description', '')}\n{text}".strip(),
                issue_type="safety_incident",
                urgency="urgent",
                safety_flags=sorted(set(data.get("safety_flags", []) + flags)),
            )
            conversation.intake_data = data
            conversation.risk_level = "urgent"
            return ChatResponse(
                answer=(
                    f"{self._immediate_safety(language)}\n\n"
                    f"{self._state_prompt(language, conversation.intake_state)}"
                ),
                language=language,
                safety_flags=data["safety_flags"],
                needs_ticket_consent=conversation.intake_state == "awaiting_consent",
            )

        if conversation.intake_state == "awaiting_consent":
            consent = request.consent_to_ticket or self._consent_answer(text, language)
            if consent is False:
                conversation.intake_state = "idle"
                conversation.intake_data = {}
                return ChatResponse(answer=self._ticket_declined(language), language=language)
            if consent is not True:
                return ChatResponse(
                    answer=self._state_prompt(language, "awaiting_consent"),
                    language=language,
                    needs_ticket_consent=True,
                )
            data["consent"] = True
            self._capture_supplied(data, request)

        elif conversation.intake_state == "awaiting_name":
            name = (request.name or text).strip()
            if not name or "@" in name or len(name) > 255:
                return ChatResponse(
                    answer=self._state_prompt(language, "awaiting_name"), language=language
                )
            data["name"] = name
            self._capture_supplied(data, request)

        elif conversation.intake_state == "awaiting_email":
            candidate = str(request.email) if request.email else self._extract_email(text)
            if not candidate:
                conversation.intake_data = data
                return ChatResponse(
                    answer=self._state_prompt(language, "awaiting_email"), language=language
                )
            data["email"] = candidate
            self._capture_supplied(data, request)

        elif conversation.intake_state == "awaiting_phone":
            candidate = normalize_phone_number(request.phone_number or text)
            if not candidate:
                conversation.intake_data = data
                return ChatResponse(
                    answer=self._state_prompt(language, "awaiting_phone"), language=language
                )
            data["phone_number"] = candidate
            self._capture_supplied(data, request)

        elif conversation.intake_state == "awaiting_ride_details":
            self._capture_supplied(data, request)
            if not data.get("ride_details_collected"):
                finished = self._is_skip(text, language) or self._is_done(text, language)
                data["ride_details"] = None if finished else text.strip()
                data["ride_details_collected"] = True
                data["details_complete"] = finished

        elif conversation.intake_state == "awaiting_additional_details":
            self._capture_supplied(data, request)
            if self._is_skip(text, language) or self._is_done(text, language):
                data["details_complete"] = True
            elif text.strip():
                data["ride_details"] = "\n".join(
                    part for part in (data.get("ride_details"), text.strip()) if part
                )

        next_state = self._next_intake_state(data)
        if next_state is None:
            conversation.intake_data = data
            return self._create_ticket(db, conversation, request, data)

        conversation.intake_state = next_state
        conversation.intake_data = data
        return ChatResponse(
            answer=self._state_prompt(language, next_state), language=language
        )

    def _create_ticket(
        self,
        db: Session,
        conversation: Conversation,
        request: ChatRequest,
        data: dict,
    ) -> ChatResponse:
        ticket_request = ChatRequest(
            channel=request.channel,
            external_user_id=request.external_user_id,
            text=data["description"],
            user_role=conversation.user_role or "unknown",
            preferred_language=conversation.preferred_language,
            name=data["name"],
            email=data["email"],
            phone_number=data["phone_number"],
            account_id=data.get("account_id"),
            trip_id=data.get("trip_id"),
            ride_details=data.get("ride_details"),
            consent_to_ticket=True,
        )
        ticket = create_ticket(
            db,
            ticket_request,
            data["description"],
            data["issue_type"],
            data["urgency"],
            data.get("safety_flags", []),
        )
        ticket.conversation_id = conversation.id
        approved_media = (
            db.query(MediaAttachment)
            .filter(
                MediaAttachment.conversation_id == conversation.id,
                MediaAttachment.ticket_id.is_(None),
                MediaAttachment.status == "approved",
                MediaAttachment.created_at
                >= datetime.fromisoformat(data.get("started_at", "1970-01-01T00:00:00")),
            )
            .all()
        )
        for attachment in approved_media:
            attachment.ticket_id = ticket.id
        ticket.attachment_count = data.get("attachment_count", 0) + len(approved_media)
        ticket.extra = {
            **(ticket.extra or {}),
            "supporting_evidence": data.get("evidence", []),
        }
        db.add(
            AuditLog(
                actor="customer",
                event_type="ticket_created",
                subject_type="ticket",
                subject_id=ticket.id,
                details={
                    "ticket": ticket.public_id,
                    "urgency": ticket.urgency,
                    "issue_type": ticket.issue_type,
                },
            )
        )
        conversation.intake_state = "idle"
        conversation.intake_data = {}
        answer = self._ticket_created(
            conversation.preferred_language, ticket.public_id, ticket.urgency
        )
        if not data.get("lead_shown"):
            answer = (
                f"{self._intake_lead(conversation.preferred_language, ticket.issue_type, 'account_action_blocked' in data.get('safety_flags', []))}"
                f"\n\n{answer}"
            )
        return ChatResponse(
            answer=answer,
            language=conversation.preferred_language,
            safety_flags=data.get("safety_flags", []),
            ticket=to_ticket_response(ticket),
        )

    def _get_or_create_conversation(
        self, db: Session, request: ChatRequest
    ) -> tuple[Conversation, bool]:
        conversation = (
            db.query(Conversation)
            .filter(
                Conversation.channel == request.channel,
                Conversation.external_user_id == request.external_user_id,
            )
            .order_by(Conversation.created_at.desc())
            .first()
        )
        if conversation:
            return conversation, False
        conversation = Conversation(
            channel=request.channel,
            external_user_id=request.external_user_id,
            preferred_language=request.preferred_language or detect_language(request.text),
            user_role=request.user_role,
        )
        db.add(conversation)
        db.flush()
        return conversation, True

    def _select_language(self, conversation: Conversation, request: ChatRequest) -> str:
        if request.preferred_language:
            return request.preferred_language
        selected = selected_language(request.text)
        if selected:
            return selected
        detected = detect_language(request.text)
        if conversation.intake_state != "idle" and detected == "en":
            words = request.text.split()
            if conversation.preferred_language != "en" and len(words) <= 3:
                return conversation.preferred_language
        return detected

    def _store_outbound(self, db: Session, conversation_id: str, response: ChatResponse) -> None:
        db.add(
            Message(
                conversation_id=conversation_id,
                direction="outbound",
                content=response.answer,
                language=response.language,
                safety_flags=response.safety_flags,
                payload={"ticket": response.ticket.model_dump() if response.ticket else None},
            )
        )

    def _extract_email(self, text: str) -> str | None:
        match = EMAIL_RE.search(text)
        if not match:
            return None
        try:
            return validate_email(match.group(0).rstrip(".,"), check_deliverability=False).normalized
        except EmailNotValidError:
            return None

    def _capture_supplied(
        self, data: dict, request: ChatRequest, include_attachments: bool = True
    ) -> None:
        if request.name:
            data["name"] = request.name.strip()
        if request.email:
            data["email"] = str(request.email)
        phone = normalize_phone_number(request.phone_number)
        if not phone and request.channel == "whatsapp":
            phone = normalize_phone_number(request.external_user_id)
        if phone:
            data["phone_number"] = phone
        if request.trip_id:
            data["trip_id"] = request.trip_id
            data["ride_details_collected"] = True
            data["details_complete"] = True
        if request.ride_details:
            data["ride_details"] = request.ride_details.strip()
            data["ride_details_collected"] = True
            data["details_complete"] = True
        if include_attachments and request.attachments:
            data.setdefault("evidence", []).extend(
                item.model_dump(mode="json") for item in request.attachments
            )
            data["attachment_count"] = len(data["evidence"])

    def _next_intake_state(self, data: dict) -> str | None:
        for key, state in (
            ("consent", "awaiting_consent"),
            ("name", "awaiting_name"),
            ("email", "awaiting_email"),
            ("phone_number", "awaiting_phone"),
            ("ride_details_collected", "awaiting_ride_details"),
            ("details_complete", "awaiting_additional_details"),
        ):
            if not data.get(key):
                return state
        return None

    def _consent_answer(self, text: str, language: str) -> bool | None:
        normalized = text.strip().lower().rstrip(".!。")
        yes = {
            "en": {"yes", "i agree", "i consent", "agree", "consent"},
            "ms": {"ya", "saya setuju", "setuju", "yes", "i agree"},
            "zh": {"同意", "我同意", "是", "yes", "i agree"},
        }[language]
        no = {
            "en": {"no", "no thanks", "i decline", "decline"},
            "ms": {"tidak", "tidak setuju", "tak mahu", "no", "no thanks"},
            "zh": {"不同意", "拒绝", "不要", "否", "no", "no thanks"},
        }[language]
        if normalized in yes:
            return True
        if normalized in no:
            return False
        return None

    def _is_cancel(self, text: str, language: str) -> bool:
        return text.strip().lower().rstrip(".!。") in {
            "en": {"cancel ticket", "stop", "never mind"},
            "ms": {"batalkan tiket", "berhenti", "tak jadi"},
            "zh": {"取消工单", "停止", "算了"},
        }[language]

    def _is_skip(self, text: str, language: str) -> bool:
        return text.strip().lower().rstrip(".!。") in {
            "en": {"skip", "not applicable", "none", "no evidence"},
            "ms": {"langkau", "tidak berkenaan", "tiada", "tiada bukti", "skip"},
            "zh": {"跳过", "不适用", "没有", "没有证据", "skip"},
        }[language]

    def _is_done(self, text: str, language: str) -> bool:
        return text.strip().lower().rstrip(".!。") in {
            "en": {"done", "finished", "that's all", "that is all"},
            "ms": {"selesai", "dah selesai", "itu sahaja", "done"},
            "zh": {"完成", "好了", "就这些", "done"},
        }[language]

    def attachment_follow_up(self, conversation: Conversation) -> str:
        language = conversation.preferred_language
        if conversation.intake_state != "idle":
            return self._state_prompt(language, conversation.intake_state)
        return {
            "en": "Please describe what happened in a text message. I’ll then collect the contact and ride details needed for a support ticket.",
            "ms": "Sila terangkan perkara yang berlaku dalam mesej teks. Saya kemudian akan mengumpulkan butiran hubungan dan perjalanan yang diperlukan untuk tiket sokongan.",
            "zh": "请用文字说明发生的情况。随后我会收集建立客服工单所需的联系方式和行程资料。",
        }[language]

    def _bot_identity(self, language: str) -> str:
        return {
            "en": "Hi! I’m DUDU Car’s automated assistant. I’m happy to help.",
            "ms": "Hai! Saya pembantu automatik DUDU Car. Saya gembira dapat membantu.",
            "zh": "你好！我是 DUDU Car 的自动客服助手。很高兴为你服务。",
        }[language]

    def _state_prompt(self, language: str, state: str) -> str:
        prompts = {
            "en": {
                "awaiting_consent": f"To arrange human follow-up, may we store your issue details in a support ticket? Reply Yes or No. Privacy Notice: {PRIVACY_NOTICE_URL}",
                "awaiting_name": "Great, thank you! May I have your name for the support ticket?",
                "awaiting_email": "Thanks! What valid email address should our support team use for this ticket?",
                "awaiting_phone": "Thank you! Please share the WhatsApp phone number you would like us to use for follow-up.",
                "awaiting_ride_details": "We’re almost done! Please share the available trip ID, date/time, pickup location and destination. You may send the details across multiple messages and attach supporting pictures or videos. Reply Done after submitting everything, or Skip if no ride details apply.",
                "awaiting_additional_details": "Do you have any other relevant details or supporting pictures or videos? Send them now, or reply Done if you have submitted everything needed.",
            },
            "ms": {
                "awaiting_consent": f"Untuk mengatur susulan oleh pegawai, bolehkah kami menyimpan butiran isu anda dalam tiket sokongan? Balas Ya atau Tidak. Notis Privasi: {PRIVACY_NOTICE_URL}",
                "awaiting_name": "Baik, terima kasih! Boleh saya dapatkan nama anda untuk tiket sokongan?",
                "awaiting_email": "Terima kasih! Apakah alamat e-mel sah yang patut digunakan oleh pasukan sokongan kami untuk tiket ini?",
                "awaiting_phone": "Terima kasih! Sila berikan nombor telefon WhatsApp yang anda mahu kami gunakan untuk susulan.",
                "awaiting_ride_details": "Kita hampir selesai! Sila kongsikan ID perjalanan, tarikh/masa, lokasi pengambilan dan destinasi yang tersedia. Anda boleh menghantar butiran dalam beberapa mesej dan melampirkan gambar atau video sokongan. Balas Selesai selepas menghantar semuanya, atau Langkau jika tiada butiran perjalanan berkaitan.",
                "awaiting_additional_details": "Adakah anda mempunyai butiran lain atau gambar atau video sokongan? Hantar sekarang, atau balas Selesai jika semua maklumat yang diperlukan telah dihantar.",
            },
            "zh": {
                "awaiting_consent": f"为了安排人工客服跟进，你是否同意我们将问题资料保存至客服工单？请回复同意或不同意。隐私声明：{PRIVACY_NOTICE_URL}",
                "awaiting_name": "好的，谢谢！可以告诉我用于客服工单的姓名吗？",
                "awaiting_email": "谢谢！我们的客服团队应使用哪个有效电子邮箱地址跟进此工单？",
                "awaiting_phone": "谢谢！请提供你希望我们用于后续联系的 WhatsApp 电话号码。",
                "awaiting_ride_details": "我们快完成了！请提供现有的行程编号、日期/时间、上车地点和目的地。你可以分多条消息发送资料，并附上相关图片或视频。全部提交后请回复“完成”；如无相关行程资料，请回复“跳过”。",
                "awaiting_additional_details": "你还有其他相关资料或支持图片、视频吗？请现在发送；如果所需资料已全部提交，请回复“完成”。",
            },
        }
        return prompts[language][state]

    def _intake_lead(self, language: str, issue_type: str, prohibited: bool) -> str:
        if prohibited:
            return {
                "en": "I understand what you would like to do. I can’t perform refunds, cancellations, bookings, payments, account changes, approvals, suspensions, or bans, but I can help create a support ticket for review.",
                "ms": "Saya faham perkara yang anda mahu lakukan. Saya tidak boleh membuat bayaran balik, pembatalan, tempahan, pembayaran, perubahan akaun, kelulusan, penggantungan, atau sekatan, tetapi saya boleh membantu membuat tiket untuk semakan.",
                "zh": "我明白你希望处理这件事。我不能执行退款、取消、预订、付款、账户更改、批准、暂停或封禁操作，但可以协助创建工单供团队审核。",
            }[language]
        if issue_type == "safety_incident":
            return self._immediate_safety(language)
        if issue_type == "complaint":
            return {
                "en": "I’m sorry you had this experience. I understand how upsetting that can be, and I can help create a ticket for our support team to review.",
                "ms": "Saya kesal anda mengalami perkara ini. Saya faham keadaan ini boleh mengecewakan, dan saya boleh membantu membuat tiket untuk semakan pasukan sokongan kami.",
                "zh": "很抱歉你遇到这样的情况。我理解这可能令人难过，我可以协助创建工单，让客服团队为你审核。",
            }[language]
        if issue_type == "partnership":
            return {
                "en": "It’s great to hear that you’re interested in working with DUDU Car! I can collect your inquiry for our team, but I cannot approve a partnership, negotiate terms, or make commitments for DUDU Car.",
                "ms": "Kami gembira mengetahui anda berminat untuk bekerjasama dengan DUDU Car! Saya boleh merekodkan pertanyaan anda untuk pasukan kami, tetapi saya tidak boleh meluluskan kerjasama, merundingkan syarat, atau membuat komitmen bagi pihak DUDU Car.",
                "zh": "很高兴得知你有兴趣与 DUDU Car 合作！我可以为团队记录你的咨询，但不能代表 DUDU Car 批准合作、协商条款或作出承诺。",
            }[language]
        return {
            "en": "Absolutely—I’d be happy to help create a ticket for human follow-up. Human support is available 9:00 AM–6:00 PM every day, Malaysia time.",
            "ms": "Sudah tentu—saya berbesar hati membantu membuat tiket untuk susulan oleh pegawai. Sokongan manusia tersedia setiap hari, 9:00 pagi–6:00 petang waktu Malaysia.",
            "zh": "当然可以—我很乐意协助创建工单，由人工客服跟进。人工客服时间为马来西亚时间每天上午 9:00 至下午 6:00。",
        }[language]

    def _immediate_safety(self, language: str) -> str:
        return {
            "en": "Your safety comes first. If anyone is in immediate danger, contact Malaysian emergency services at 999 first. Once you are safe, I can help create an urgent ticket.",
            "ms": "Keselamatan anda adalah keutamaan. Jika sesiapa berada dalam bahaya segera, hubungi perkhidmatan kecemasan Malaysia di 999 terlebih dahulu. Setelah anda selamat, saya boleh membantu membuat tiket segera.",
            "zh": "你的安全最重要。如果任何人正面临紧急危险，请先拨打马来西亚紧急求助电话 999。确认安全后，我可以协助创建紧急工单。",
        }[language]

    def _unconfirmed(self, language: str) -> str:
        return {
            "en": "I’m sorry, but I can’t confirm that from DUDU Car’s approved information. I can help create a ticket so our team can check it for you.",
            "ms": "Maaf, saya tidak dapat mengesahkannya daripada maklumat DUDU Car yang diluluskan. Saya boleh membantu membuat tiket supaya pasukan kami dapat menyemaknya untuk anda.",
            "zh": "很抱歉，我无法从 DUDU Car 已批准的信息中确认这一点。我可以协助创建工单，请团队为你核实。",
        }[language]

    def _ticket_declined(self, language: str) -> str:
        return {
            "en": "No problem. I have not created a ticket, and I’m still here if you would like general support information.",
            "ms": "Tiada masalah. Saya tidak membuat tiket, dan saya masih sedia membantu jika anda memerlukan maklumat sokongan umum.",
            "zh": "没问题，我没有创建工单。如果你需要一般客服信息，我仍然很乐意协助。",
        }[language]

    def _ticket_created(self, language: str, public_id: str, urgency: str) -> str:
        priority = {
            "en": {"normal": "normal", "high": "high", "urgent": "urgent"},
            "ms": {"normal": "biasa", "high": "tinggi", "urgent": "segera"},
            "zh": {"normal": "普通", "high": "高", "urgent": "紧急"},
        }[language][urgency]
        target = {
            "en": {"normal": "3–5 days", "high": "1–3 days", "urgent": "within 24 hours"},
            "ms": {"normal": "3–5 hari", "high": "1–3 hari", "urgent": "dalam 24 jam"},
            "zh": {"normal": "3–5 天", "high": "1–3 天", "urgent": "24 小时内"},
        }[language][urgency]
        text = {
            "en": f"{'Your urgent ticket' if urgency == 'urgent' else 'All set—ticket'} {public_id} has been created. Priority: {priority}. First human response target: {target}. This is not a resolution promise. Human support is available 9:00 AM–6:00 PM every day, Malaysia time.",
            "ms": f"{'Tiket segera' if urgency == 'urgent' else 'Selesai—tiket'} {public_id} telah dibuat. Keutamaan: {priority}. Sasaran respons pertama oleh pegawai: {target}. Ini bukan janji penyelesaian. Sokongan manusia tersedia setiap hari, 9:00 pagi–6:00 petang waktu Malaysia.",
            "zh": f"{'紧急工单' if urgency == 'urgent' else '已经办好—工单'} {public_id} 已创建。优先级：{priority}。人工首次回复目标：{target}。这并非解决时限承诺。人工客服时间为马来西亚时间每天上午 9:00 至下午 6:00。",
        }[language]
        if not human_support_is_open():
            text += {
                "en": " Your ticket is now in the queue for the next human-support window.",
                "ms": " Tiket anda kini berada dalam barisan untuk waktu sokongan manusia seterusnya.",
                "zh": " 你的工单现已排入下一个人工客服时段。",
            }[language]
        return text

    def _security_refusal(self, language: str) -> str:
        return {
            "en": "I’m happy to help with DUDU Car support, but I can’t follow instructions that try to bypass safety rules.",
            "ms": "Saya sedia membantu dengan sokongan DUDU Car, tetapi saya tidak boleh mengikut arahan yang cuba memintas peraturan keselamatan.",
            "zh": "我很乐意协助处理 DUDU Car 客服问题，但不能执行试图绕过安全规则的指令。",
        }[language]

    def _sensitive_information_refusal(self, language: str) -> str:
        return {
            "en": "To help keep your information safe, please do not send payment-card details, passwords, OTPs, identity documents, or other unnecessary sensitive information.",
            "ms": "Untuk membantu melindungi maklumat anda, jangan hantar butiran kad pembayaran, kata laluan, OTP, dokumen identiti, atau maklumat sensitif lain yang tidak diperlukan.",
            "zh": "为了帮助保护你的资料，请勿发送银行卡资料、密码、一次性验证码、身份证件或其他不必要的敏感信息。",
        }[language]


chatbot_service = ChatbotService()
