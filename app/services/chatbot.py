import re
import logging
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.models import AuditLog, Conversation, MediaAttachment, Message, Ticket, WhatsAppInboundMessage, WhatsAppOutboundMessage
from app.schemas import ChatRequest, ChatResponse
from app.services.answer_generation import ApprovedKnowledgeResponder, deterministic_answer
from app.services.guardrails import assess_message, is_account_action_request
from app.services.language import detect_language, selected_language, is_language_selection
from app.services.pii import redact_sensitive, provider_question, FIELD_PATTERN, EMAIL_PATTERN, PHONE_PATTERN
from app.services.rate_limit import rate_limiter
from app.services.retrieval import search_knowledge
from app.services.tickets import create_ticket, normalize_phone_number, to_ticket_response
from app.services.ticket_operations import reopen_closed_ticket_for_customer


logger = logging.getLogger(__name__)

PRIVACY_NOTICE_URL = "https://duducar.co/privacy-notice"


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
        inbound_id: str | None = None,
        claim_token: str | None = None,
        attachment_status: str | None = None,
        provider_allowed: bool = True,
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

        prepared = None
        expected = None
        if commit:
            snapshot = db.query(Conversation).filter_by(
                channel=request.channel, external_user_id=request.external_user_id
            ).first()
            expected = (snapshot.id, snapshot.updated_at) if snapshot else None
            preview = snapshot or Conversation(intake_state="idle", intake_data={}, preferred_language=request.preferred_language or detect_language(request.text))
            request.preferred_language = self._select_language(preview, request)
            if not self._identity_question(request.text) and not is_language_selection(request.text) and not self._is_cancel(request.text, request.preferred_language):
                prepared = self._prepare_turn(db, preview, request, redact_sensitive(request.text).text, release_transaction=True, provider_allowed=provider_allowed)
            else:
                db.commit()
        try:
            conversation, is_new = self._get_or_create_conversation(db, request)
            stale = commit and (
                (expected is None and not is_new) or
                (expected is not None and expected != (conversation.id, conversation.updated_at))
            )
            language = self._select_language(conversation, request)
            request.preferred_language = language
            conversation.preferred_language = language
            if request.user_role != "unknown" or not conversation.user_role:
                conversation.user_role = request.user_role

            current_prompt = (conversation.intake_data or {}).get("prompt_id")
            if request.prompt_id != current_prompt or not current_prompt:
                request.create_ticket = False
                request.consent_to_ticket = False
            if request.prompt_id and request.prompt_id != current_prompt:
                stale = True
            redaction = redact_sensitive(request.text)
            assessment = assess_message(redaction.text, request.attachments)
            db.add(
                Message(
                    conversation_id=conversation.id,
                    direction="inbound",
                    content=redaction.text,
                    language=language,
                    safety_flags=assessment.flags + redaction.findings,
                    payload={
                        "channel": request.channel,
                        "inbound_event_id": inbound_id,
                        "attachments": [item.model_dump() for item in request.attachments],
                    },
                )
            )

            if inbound_id:
                inbound = db.query(WhatsAppInboundMessage).filter_by(id=inbound_id).with_for_update().one()
                if inbound.status != "processing" or inbound.claim_token != claim_token:
                    db.rollback()
                    raise ValueError("inbound lease lost")
                metadata = dict(conversation.intake_data or {})
                timestamp = inbound.payload.get("timestamp", "")
                previous_time = metadata.get("last_inbound_timestamp", "")
                quoted = inbound.payload.get("context_message_id", "")
                latest = db.query(WhatsAppOutboundMessage).filter_by(recipient=request.external_user_id).order_by(WhatsAppOutboundMessage.created_at.desc()).first()
                stale = stale or bool(timestamp and previous_time and int(timestamp) < int(previous_time)) or bool(quoted and (not latest or latest.provider_message_id != quoted))
            response = ChatResponse(answer=self._local(language, "clarify"), language=language) if stale else self._respond(
                db, conversation, request, redaction.text, assessment, redaction.findings, prepared=prepared
            )
            if is_new and not self._identity_question(request.text):
                response.answer = f"{self._bot_identity(language)}\n\n{response.answer}"
            if attachment_status:
                response.answer = attachment_status + "\n\n" + response.answer
            response.prompt_id = str(uuid.uuid4())
            conversation.intake_data = {**(conversation.intake_data or {}), "prompt_id": response.prompt_id}
            if inbound_id:
                metadata = dict(conversation.intake_data or {})
                if not stale and timestamp:
                    metadata["last_inbound_timestamp"] = timestamp
                conversation.intake_data = metadata
                db.add(WhatsAppOutboundMessage(inbound_message_id=inbound_id, recipient=request.external_user_id, body=response.answer[:4096]))
                inbound.status = "done"
                inbound.processed_at = datetime.utcnow()
            self._store_outbound(db, conversation.id, response)
            outcome = "ticket_created" if response.ticket else "ticket_offered" if (conversation.intake_data or {}).get("pending_offer") else "ticket_started" if response.needs_ticket_consent else "answered" if response.sources else "deterministic_fallback" if not prepared or prepared[-1] is None else "conversational"
            if commit:
                db.commit()
            else:
                db.flush()
            logger.info("chat_outcome %s", json.dumps({"outcome": outcome, "language": language}))
            return response
        except Exception:
            db.rollback()
            raise

    def _prepare_turn(self, db, conversation, request, text, *, release_transaction=False, provider_allowed=True):
        language = request.preferred_language or "en"
        data = dict(conversation.intake_data or {})
        if data.get("started_at") and datetime.utcnow() - datetime.fromisoformat(data["started_at"]) > timedelta(minutes=get_settings().intake_expiry_minutes):
            data = {}
        context = dict(data.get("context", {}))
        fields = self._extract_fields(request, text, conversation.intake_state)
        local_values = {**{k: data.get(k) for k in ("name", "email", "phone_number", "account_id", "trip_id")}, **fields,
                        "external_user_id": request.external_user_id}
        question = None if text == "[attachment]" else provider_question(text, local_values)
        if conversation.intake_state == "awaiting_name" and fields.get("name") == text.strip():
            question = "[FIELD_NAME]"
        sanitized_context = {
            "topic": context.get("topic", ""),
            "previous_question": context.get("previous_question", ""),
            "previous_answer": context.get("previous_answer", ""),
            "sources": context.get("sources", []),
            "stage": conversation.intake_state,
            "current_prompt": self._state_prompt(language, conversation.intake_state) if conversation.intake_state not in {"idle", "paused"} else "",
            "pending_offer": bool(data.get("pending_offer")),
            "fields_present": [k for k in ("name", "email", "phone_number", "trip_id", "account_id") if data.get(k)],
            "supplied_fields": list(fields),
            "has_previous_case": bool(data.get("last_ticket_id")),
        }
        retrieval = search_knowledge(db, question or "", language)
        if context.get("topic") and retrieval.confidence == 0:
            retrieval = search_knowledge(db, context["topic"], language)
        immediate = assess_message(text).is_safety_critical
        if release_transaction:
            db.commit()
        result = None if immediate or question is None or not release_transaction or not provider_allowed else self.answer_generator.generate(
            language, retrieval.chunks, question, sanitized_context
        )
        return fields, question, local_values, retrieval, result

    def _respond(
        self, db, conversation, request, text, assessment, redaction_findings, prepared=None,
    ) -> ChatResponse:
        language = request.preferred_language or "en"
        flags = assessment.flags + redaction_findings
        data = dict(conversation.intake_data or {})
        context = dict(data.get("context", {}))
        started = data.get("started_at")
        if started and datetime.utcnow() - datetime.fromisoformat(started) > timedelta(
            minutes=get_settings().intake_expiry_minutes
        ):
            conversation.intake_state = "idle"
            data = {}
            context = {}
            conversation.intake_data = data
        if "sensitive_attachment_rejected" in flags:
            return ChatResponse(answer=self._sensitive_information_refusal(language), language=language)
        if text == "[attachment]":
            return ChatResponse(answer={
                "en": "Please describe your support question in a text message. I cannot view the attachment.",
                "ms": "Sila terangkan soalan sokongan dalam mesej teks. Saya tidak boleh melihat lampiran.",
                "zh": "请用文字描述客服问题。我无法查看附件内容。",
            }[language], language=language)
        if self._identity_question(text):
            return ChatResponse(answer=self._bot_identity(language), language=language)
        if is_language_selection(text):
            answer = self._state_prompt(language, conversation.intake_state) if conversation.intake_state not in {"idle", "paused"} else self._bot_identity(language)
            return ChatResponse(answer=answer, language=language)

        fields, question, local_values, retrieval, result = prepared or self._prepare_turn(
            db, conversation, request, text
        )
        immediate = assessment.is_safety_critical
        if result:
            context = {
                "topic": provider_question(result.topic, local_values) or "",
                "previous_question": (question or "")[:400],
                "previous_answer": (provider_question(result.answer, local_values) or "")[:600],
                "sources": [c.document_key for c in retrieval.chunks][:4],
            }
            data["context"] = context
            conversation.intake_data = data
        if conversation.intake_state != "idle" and len(set(EMAIL_PATTERN.findall(text))) > 1:
            return ChatResponse(answer={
                "en": "Which email address should I use for this ticket? Please provide one address.",
                "ms": "Alamat e-mel manakah patut digunakan untuk tiket ini? Sila berikan satu alamat.",
                "zh": "这个工单应该使用哪个邮箱？请提供一个地址。",
            }[language], language=language)
        local_control = self._consent_answer(text, language) is not None or self._is_done(text, language) or self._is_skip(text, language) or text.strip().lower() in {"submit", "hantar", "提交"}
        if result and (local_control or (conversation.intake_state == "idle" and assessment.should_create_ticket)):
            result = result.model_copy(update={"intake_action": "none"})
        action = result.intake_action if result else "none"
        question_form = bool(re.search(
            r'(?i)[?？“”"]|^(?:how|what|why|where|when|can |could |explain|if |suppose|bagaimana|apakah|mengapa|boleh|jika|如何|怎么|什么|为什么|如果)', text.strip()
        ))
        action_intent = {
            "pause": r"(?i)\b(?:pause|later|break|hold on|pick this up|jeda|nanti|kemudian|tangguh)\b|暂停|稍后|等一下|晚点",
            "resume": r"(?i)\b(?:resume|continue|carry on|pick up|sambung|teruskan)\b|继续|恢复",
            "cancel": r"(?i)\b(?:cancel|stop|never mind|forget it|do not want|don't want|no ticket|batal|berhenti|tak mahu|tidak mahu)\b|取消|停止|不想|不要|算了",
        }
        # A model's state proposal needs explicit local intent; supplied fields and
        # replies to local prompts must never be mistaken for pausing/cancelling.
        if action in action_intent and (question_form or not re.search(action_intent[action], text)):
            action = "none"
            result = result.model_copy(update={"intake_action": action})
        if self._is_cancel(text, language) or action == "cancel":
            conversation.intake_state = "idle"
            conversation.intake_data = {"context": context}
            return ChatResponse(answer=self._ticket_declined(language), language=language)
        if action == "pause":
            if conversation.intake_state != "idle":
                data["resume_state"] = conversation.intake_state
                conversation.intake_state = "paused"
                conversation.intake_data = data
            return ChatResponse(answer=self._local(language, "paused"), language=language)
        if conversation.intake_state == "paused" and action == "resume":
            conversation.intake_state = data.get("resume_state", "awaiting_consent")
        if conversation.intake_state == "awaiting_case_confirmation":
            if self._consent_answer(text, language) is True:
                ticket = db.query(Ticket).filter_by(
                    id=data.get("case_id"), channel=request.channel,
                    external_user_id=request.external_user_id,
                ).with_for_update().first()
                if ticket:
                    updates = list((ticket.extra or {}).get("customer_updates", []))
                    if len(updates) < 20:
                        if ticket.status == "closed":
                            reopen_closed_ticket_for_customer(db, channel=request.channel, external_user_id=request.external_user_id, public_id=ticket.public_id)
                        updates.append(data["case_update"])
                        ticket.extra = {**(ticket.extra or {}), "customer_updates": updates}
                        if data.get("evidence_group"):
                            for attachment in db.query(MediaAttachment).filter(
                                MediaAttachment.conversation_id == conversation.id,
                                MediaAttachment.evidence_group == data["evidence_group"],
                                MediaAttachment.ticket_id.is_(None),
                                MediaAttachment.status.in_(("queued", "approved")),
                            ).all():
                                attachment.ticket_id = ticket.id
                                ticket.attachment_count += int(attachment.status == "approved")
                        for key, value in data.get("case_fields", {}).items():
                            setattr(ticket, key, value)
                        db.add(AuditLog(actor="customer", event_type="ticket_updated_by_customer", subject_type="ticket", subject_id=ticket.id))
                        conversation.intake_state = "idle"
                        conversation.intake_data = {"last_ticket_id": ticket.id}
                        return ChatResponse(answer={"en": "Your case update has been recorded.", "ms": "Kemas kini kes anda telah direkodkan.", "zh": "你的工单补充已记录。"}[language], language=language)
            return ChatResponse(answer=self._local(language, "clarify"), language=language)
        if (action == "continue_case" or (action == "correct" and fields and data.get("last_ticket_id"))) and conversation.intake_state == "idle":
            reference = re.search(r"DUDU-\d{8}-[A-Z0-9]{5}", text, re.I)
            query = db.query(Ticket).filter_by(channel=request.channel, external_user_id=request.external_user_id)
            ticket = query.filter_by(public_id=reference.group().upper()).first() if reference else query.filter_by(id=data.get("last_ticket_id")).first()
            if ticket and len(text) <= 2000 and len((ticket.extra or {}).get("customer_updates", [])) < 20:
                conversation.intake_state = "awaiting_case_confirmation"
                conversation.intake_data = {**data, "case_id": ticket.id, "case_update": text, "case_fields": fields}
                return ChatResponse(answer={
                    "en": f"Add this update to {ticket.public_id}" + (" and reopen it" if ticket.status == "closed" else "") + "? Reply Yes to confirm, or Stop to cancel.",
                    "ms": f"Tambah kemas kini ini pada {ticket.public_id}" + (" dan buka semula kes" if ticket.status == "closed" else "") + "? Balas Ya untuk mengesahkan, atau Berhenti untuk membatalkan.",
                    "zh": f"将这次补充加入 {ticket.public_id}" + ("并重新开启工单" if ticket.status == "closed" else "") + "？回复“同意”确认，或“停止”取消。",
                }[language], language=language)
            return ChatResponse(answer=self._local(language, "clarify"), language=language)
        if conversation.intake_state not in {"idle", "paused"}:
            issue_reply = conversation.intake_state == "awaiting_issue" and not self._acknowledgement(text) and not question_form and result and result.disposition in {"answer", "troubleshoot"}
            # Side answers never become names, consent, ride details or submission commands.
            if result and result.disposition in {"answer", "troubleshoot", "scope", "smalltalk", "clarify"} and action == "none" and not local_control and not issue_reply and (question_form or not any(str(value) in text for value in fields.values())):
                return ChatResponse(answer=result.answer, language=language, sources=[retrieval.chunks[i-1].source_title for i in result.citations])
            if not result and ("?" in text or "？" in text):
                return ChatResponse(answer=self._local(language, "clarify"), language=language)
            return self._continue_intake(db, conversation, request, text, assessment, flags, fields=fields, result=result)
        if conversation.intake_state == "paused":
            return ChatResponse(answer=result.answer if result else self._local(language, "paused"), language=language)

        pending = data.get("pending_offer")
        accepted = bool(pending) and self._consent_answer(text, language) is True
        if accepted or request.create_ticket or immediate or assessment.should_create_ticket:
            issue_type = assessment.issue_type if assessment.should_create_ticket else result.issue_type if result and result.issue_type != "general_faq" else assessment.issue_type
            if accepted:
                issue_type = pending.get("issue_type", "unconfirmed_question")
            response = self._start_intake(
                db, conversation, request, pending.get("description", text) if accepted else text,
                issue_type, "urgent" if immediate else "high" if issue_type in {"fraud", "payment_or_fare", "account_support"} or (is_account_action_request(text) and assessment.urgency == "high") else "normal",
                flags, issue_type == "prohibited_action_request" or is_account_action_request(text),
                lead=result.answer if result and result.disposition in {"answer", "troubleshoot"} and not immediate and not is_account_action_request(text) else None,
            )
            logger.info("chat_disposition", extra={"outcome": "ticket_started", "language": language})
            return response
        if result:
            if result.disposition in {"offer_ticket", "explicit_handoff"}:
                conversation.intake_data = {**data, "context": context, "pending_offer": {"description": text, "issue_type": result.issue_type}, "started_at": datetime.utcnow().isoformat()}
            else:
                data.pop("pending_offer", None)
                conversation.intake_data = {**data, "context": context}
            logger.info("chat_disposition", extra={"outcome": "ticket_offered" if result.disposition == "offer_ticket" else "answered", "language": language})
            answer = result.answer
            if result.disposition == "explicit_handoff":
                answer = {
                    "en": "Would you like a support ticket for human follow-up?",
                    "ms": "Adakah anda mahu tiket sokongan untuk susulan oleh pegawai?",
                    "zh": "你希望创建客服工单，由人工客服跟进吗？",
                }[language]
                if result.issue_type == "safety_incident":
                    answer = self._immediate_safety(language) + "\n\n" + answer
            return ChatResponse(answer=answer, language=language, confidence=retrieval.confidence, sources=[retrieval.chunks[i-1].source_title for i in result.citations])
        # Outage fallback uses conservative local controls; a retrieval score is not answerability.
        if question is None:
            return ChatResponse(answer=self._local(language, "private_rephrase"), language=language)
        if self._acknowledgement(text):
            return ChatResponse(answer=self._bot_identity(language), language=language)
        if retrieval.confidence >= get_settings().retrieval_min_confidence:
            return ChatResponse(answer=deterministic_answer(language, retrieval.chunks), language=language, confidence=retrieval.confidence, sources=[retrieval.chunks[0].source_title])
        return ChatResponse(answer=self._local(language, "clarify"), language=language, safety_flags=flags)

    @staticmethod
    def _identity_question(text):
        return bool(re.fullmatch(
            r"\s*(?:hi[,! ]+)?(?:are you (?:a human|human|an? ai|ai|a bot|a robot)|你是(?:人工|真人|机器人|AI)(?:吗)?|adakah (?:anda|awak) (?:manusia|AI|bot))\s*[?？.!。]*\s*", text, re.I
        ))

    @staticmethod
    def _acknowledgement(text):
        return text.strip().lower().rstrip(".!。") in {"hi", "hello", "hey", "thanks", "thank you", "yes", "no", "ok", "okay", "👍", "🙏", "你好", "谢谢", "好的", "terima kasih", "hai", "ya"}

    @staticmethod
    def _local(language, key):
        return {
            "clarify": {"en": "Could you clarify which DUDU Car service or question you mean?", "ms": "Boleh jelaskan perkhidmatan atau soalan DUDU Car yang anda maksudkan?", "zh": "请说明你指的是哪项 DUDU Car 服务或问题？"},
            "private_rephrase": {"en": "Please restate a short support question without names, contact details, account identifiers or locations.", "ms": "Sila nyatakan semula soalan sokongan tanpa nama, butiran hubungan, pengecam akaun atau lokasi.", "zh": "请重新描述客服问题，不要包含姓名、联系方式、账号或地点。"},
            "paused": {"en": "Your ticket draft is paused. You can resume it or ask another question.", "ms": "Draf tiket dijeda. Anda boleh menyambungnya atau bertanya soalan lain.", "zh": "工单草稿已暂停。你可以继续填写或询问其他问题。"},
            "review": {"en": "Please review these ticket details. Reply Submit to send, or tell me what to correct.", "ms": "Sila semak butiran tiket ini. Balas Hantar untuk menghantar, atau nyatakan pembetulan.", "zh": "请检查工单资料。回复“提交”发送，或告诉我需要更正的内容。"},
            "issue": {"en": "What happened, and what would you like our support team to help with?", "ms": "Apakah yang berlaku, dan apakah bantuan yang anda perlukan daripada pasukan sokongan?", "zh": "发生了什么？你希望客服团队帮助处理什么问题？"},
        }[key][language]

    def _extract_fields(self, request, text, state):
        fields = {k: str(getattr(request, k)) for k in ("name", "email", "phone_number", "account_id", "trip_id") if getattr(request, k)}
        for role, label, maximum in (("trip_id", r"trip(?: id)?|id perjalanan|行程编号", 120), ("account_id", r"account(?: id)?|id akaun|账号", 255)):
            match = re.search(rf"(?i)(?:^|[;；\n])\s*(?:{label})\s*[:：=]\s*([A-Za-z0-9_-]{{1,{maximum}}})(?=\s|$|[,;，；])", text)
            if match:
                fields.setdefault(role, match.group(1))
        email = self._extract_email(text)
        if email:
            fields.setdefault("email", email)
        phone_role = state == "awaiting_phone" or bool(re.search(r"(?i)\b(?:phone|whatsapp|contact|telefon|nombor)\b|电话|联系", text))
        phones = {
            normalize_phone_number(m.group()) for m in PHONE_PATTERN.finditer(text)
            if phone_role or m.group().startswith("+")
        } - {None}
        if len(phones) == 1:
            fields.setdefault("phone_number", phones.pop())
        name_match = FIELD_PATTERN.search(text)
        if name_match:
            name = re.split(r"(?i)\s+(?:and|email|e-mail|phone|emel|dan)\b", (name_match.group(1) or name_match.group(2)))[0].strip()
            if not EMAIL_PATTERN.search(name) and not any(c.isdigit() for c in name) and len(name) <= 255:
                fields.setdefault("name", name)
        elif state == "awaiting_name" and not fields.get("name") and not EMAIL_PATTERN.search(text) and not self._acknowledgement(text) and not self._is_done(text, request.preferred_language or "en") and not self._is_skip(text, request.preferred_language or "en") and text.strip().lower() not in {"submit", "hantar", "提交"} and not self._consent_answer(text, request.preferred_language or "en") and re.fullmatch(r"(?:[A-Z][a-zA-Z'-]*|[\u4e00-\u9fff]+)(?:[ -](?:[A-Z][a-zA-Z'-]*|[\u4e00-\u9fff]+)){0,4}", text.strip()) and len(text.split()) <= 5:
            fields["name"] = text.strip()
        return fields

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
        if prohibited_action:
            flags = sorted(set(flags + ["account_action_blocked"]))
        evidence_group = (conversation.intake_data or {}).get("evidence_group") or str(uuid.uuid4())
        data = {
            "evidence_group": evidence_group,
            "started_at": datetime.utcnow().isoformat(),
            "description": description,
            "issue_collected": issue_type != "human_escalation" or bool(request.ride_details),
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
        self._capture_supplied(data, request, include_attachments=False)
        data.update(self._extract_fields(request, description, "idle"))
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
        self, db, conversation, request, text, assessment, flags, *, fields=None, result=None,
    ):
        language = request.preferred_language or "en"
        data = dict(conversation.intake_data or {})
        state = conversation.intake_state
        fields = fields or {}
        declined = self._consent_answer(text, language) is False
        if self._is_cancel(text, language) or (declined and (
            state == "awaiting_consent" or text.strip().lower() in {"i decline", "decline", "tidak setuju", "不同意", "拒绝"}
        )):
            conversation.intake_state = "idle"
            conversation.intake_data = {}
            return ChatResponse(answer=self._ticket_declined(language), language=language)
        if assessment.is_safety_critical:
            data.update(issue_type="safety_incident", urgency="urgent")
            data["safety_notes"] = [*data.get("safety_notes", []), text]
            conversation.risk_level = "urgent"
            conversation.intake_data = data
            return ChatResponse(
                answer=self._immediate_safety(language) + "\n\n" + self._state_prompt(language, state),
                language=language, safety_flags=flags, needs_ticket_consent=state == "awaiting_consent",
            )
        if state == "awaiting_consent":
            if not (request.consent_to_ticket or self._consent_answer(text, language) is True):
                return ChatResponse(answer=self._state_prompt(language, state), language=language, needs_ticket_consent=True)
            data["consent"] = True
        if not data.get("consent"):
            conversation.intake_state = "awaiting_consent"
            return ChatResponse(answer=self._state_prompt(language, "awaiting_consent"), language=language, needs_ticket_consent=True)
        if declined and state in {"awaiting_name", "awaiting_email", "awaiting_phone"}:
            return ChatResponse(answer={
                "en": "A name, valid email and phone number are required to submit a ticket. You can provide them later, or reply Stop to cancel.",
                "ms": "Nama, e-mel sah dan nombor telefon diperlukan untuk menghantar tiket. Anda boleh memberikannya kemudian, atau balas Berhenti untuk membatalkan.",
                "zh": "提交工单需要姓名、有效邮箱和电话号码。你可以稍后提供，或回复“停止”取消。",
            }[language], language=language)
        if result and result.intake_action == "correct" and not fields:
            return ChatResponse(answer=self._local(language, "clarify"), language=language)
        self._capture_supplied(data, request)
        for key, value in fields.items():
            if key == "phone_number":
                value = normalize_phone_number(value)
            if value:
                if data.get(key) and data.get(key) != value:
                    data["review_required"] = True
                data[key] = value
        action = result.intake_action if result else "none"
        done = self._is_done(text, language) or self._is_skip(text, language) or (
            declined and state == "awaiting_additional_details"
        )
        submit = text.strip().lower().rstrip(".!。") in {"submit", "hantar", "提交"}
        if state == "awaiting_review" and submit:
            return self._create_ticket(db, conversation, request, data)
        text_has_fields = any(str(value) in text for value in fields.values())
        if state == "awaiting_issue" and not text_has_fields and not done and not submit:
            if len(text.strip()) >= 8:
                data["description"] = text.strip()
                data["issue_collected"] = True
        elif state in {"awaiting_ride_details", "awaiting_additional_details"} and not text_has_fields:
            if done:
                data["ride_details_collected"] = True
                data["details_complete"] = True
            elif not request.ride_details and text.strip():
                addition = "\n".join(part for part in (data.get("ride_details"), text.strip()) if part)
                if len(addition) > 2000:
                    return ChatResponse(answer={
                        "en": "Ticket details are limited to 2,000 characters in total. Your earlier details are kept; this addition was not added to the ticket. Please send a shorter version.",
                        "ms": "Butiran tiket terhad kepada 2,000 aksara keseluruhan. Butiran terdahulu disimpan; tambahan ini belum dimasukkan ke dalam tiket. Sila hantar versi lebih ringkas.",
                        "zh": "工单详情总共最多可包含 2,000 个字符。之前的资料已保留；本次补充未加入工单。请发送较短的版本。",
                    }[language], language=language)
                data["ride_details"] = addition
                data["ride_details_collected"] = True
        if result and result.issue_type in {"fraud", "payment_or_fare"} and action in {"continue", "correct"}:
            # New classifications are reviewed locally with the case before submission.
            data["proposed_issue_type"] = result.issue_type
            data["review_required"] = True
        next_state = self._next_intake_state(data)
        if next_state is None:
            if data.get("review_required") or action == "submit":
                next_state = "awaiting_review"
            elif done or request.ride_details:
                return self._create_ticket(db, conversation, request, data)
            else:
                next_state = "awaiting_review"
        conversation.intake_state = next_state
        conversation.intake_data = data
        if next_state == "awaiting_review":
            labels = {
                "en": ("Name", "Email", "Contact phone", "Issue", "Trip ID", "Ride details"),
                "ms": ("Nama", "E-mel", "Telefon hubungan", "Isu", "ID perjalanan", "Butiran perjalanan"),
                "zh": ("姓名", "邮箱", "联系电话", "问题", "行程编号", "行程详情"),
            }[language]
            answer = self._local(language, "review") + "\n" + "\n".join(
                f"{label}: {data[key]}" for label, key in zip(labels, ("name", "email", "phone_number", "description", "trip_id", "ride_details")) if data.get(key)
            )
            if data.get("proposed_issue_type") and data.get("urgency") != "urgent":
                answer += "\n" + {"en": "Proposed priority: high", "ms": "Keutamaan dicadangkan: tinggi", "zh": "拟定优先级：高"}[language]

        else:
            answer = self._state_prompt(language, next_state)
        if result and result.disposition in {"answer", "troubleshoot"}:
            answer = result.answer + "\n\n" + answer
        return ChatResponse(answer=answer, language=language)

    def _create_ticket(
        self,
        db: Session,
        conversation: Conversation,
        request: ChatRequest,
        data: dict,
    ) -> ChatResponse:
        if data.get("proposed_issue_type"):
            data["issue_type"] = data["proposed_issue_type"]
            if data.get("urgency") != "urgent":
                data["urgency"] = "high"
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
        bound_media = db.query(MediaAttachment).filter(
            MediaAttachment.conversation_id == conversation.id,
            MediaAttachment.evidence_group == data.get("evidence_group"),
            MediaAttachment.ticket_id.is_(None),
            MediaAttachment.status.in_(("queued", "approved")),
        ).all() if data.get("evidence_group") else []
        for attachment in bound_media:
            attachment.ticket_id = ticket.id
        ticket.attachment_count = data.get("attachment_count", 0) + sum(a.status == "approved" for a in bound_media)
        ticket.extra = {
            **(ticket.extra or {}),
            "supporting_evidence": data.get("evidence", []),
            "customer_updates": data.get("safety_notes", []),
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
        conversation.intake_data = {"last_ticket_id": ticket.id}
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
            .populate_existing()
            .with_for_update()
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
        try:
            with db.begin_nested():
                db.add(conversation)
                db.flush()
        except IntegrityError:
            conversation = db.query(Conversation).filter_by(
                channel=request.channel, external_user_id=request.external_user_id
            ).with_for_update().one()
            return conversation, False
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
        candidates = set(EMAIL_PATTERN.findall(text))
        if len(candidates) != 1:
            return None
        try:
            return validate_email(candidates.pop(), check_deliverability=False).normalized
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
        if not phone and not data.get("phone_number") and request.channel == "whatsapp":
            phone = normalize_phone_number(request.external_user_id)
        if phone:
            data["phone_number"] = phone
        if request.trip_id:
            data["trip_id"] = request.trip_id
            data["ride_details_collected"] = True
        if request.ride_details:
            data["ride_details"] = request.ride_details.strip()
            data["ride_details_collected"] = True
            data["details_complete"] = True
            data["issue_collected"] = True
        if include_attachments and request.attachments:
            data["evidence"] = list(data.get("evidence", []))
            data["evidence"].extend(
                item.model_dump(mode="json") for item in request.attachments
            )
            data["attachment_count"] = len(data["evidence"])

    def _next_intake_state(self, data: dict) -> str | None:
        for key, state in (
            ("consent", "awaiting_consent"),
            ("name", "awaiting_name"),
            ("email", "awaiting_email"),
            ("phone_number", "awaiting_phone"),
            ("issue_collected", "awaiting_issue"),
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
            "en": {"cancel ticket", "stop", "never mind", "stop bro", "i don't need a human follow-up", "i do not want a ticket"},
            "ms": {"batalkan tiket", "berhenti", "tak jadi", "saya tak perlukan pegawai"},
            "zh": {"取消工单", "停止", "算了", "我不需要人工跟进"},
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

    def _bot_identity(self, language: str) -> str:
        return {
            "en": "Hi! I’m DUDU Car’s automated AI assistant. I’m happy to help.",
            "ms": "Hai! Saya pembantu AI automatik DUDU Car. Saya gembira dapat membantu.",
            "zh": "你好！我是 DUDU Car 的 AI 自动客服助手。很高兴为你服务。",
        }[language]

    def _state_prompt(self, language: str, state: str) -> str:
        if state == "awaiting_issue":
            return self._local(language, "issue")
        if state == "awaiting_review":
            return self._local(language, "review")
        if state == "awaiting_case_confirmation":
            return {"en": "Reply Yes to confirm the proposed case update, or Stop to cancel.", "ms": "Balas Ya untuk mengesahkan kemas kini kes, atau Berhenti untuk membatalkan.", "zh": "回复“同意”确认工单补充，或“停止”取消。"}[language]
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

    def _sensitive_information_refusal(self, language: str) -> str:
        return {
            "en": "To help keep your information safe, please do not send payment-card details, passwords, OTPs, identity documents, or other unnecessary sensitive information.",
            "ms": "Untuk membantu melindungi maklumat anda, jangan hantar butiran kad pembayaran, kata laluan, OTP, dokumen identiti, atau maklumat sensitif lain yang tidak diperlukan.",
            "zh": "为了帮助保护你的资料，请勿发送银行卡资料、密码、一次性验证码、身份证件或其他不必要的敏感信息。",
        }[language]


chatbot_service = ChatbotService()
