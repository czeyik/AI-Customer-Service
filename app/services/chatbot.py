from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditLog, Conversation, Message
from app.schemas import ChatRequest, ChatResponse
from app.services.guardrails import assess_message, is_account_action_request
from app.services.language import detect_language
from app.services.answer_generation import ApprovedKnowledgeResponder, localized_unsure
from app.services.pii import redact_sensitive
from app.services.rate_limit import rate_limiter
from app.services.retrieval import search_knowledge
from app.services.tickets import create_ticket, to_ticket_response


class ChatbotService:
    def __init__(self) -> None:
        self.answer_generator = ApprovedKnowledgeResponder()

    def handle(self, db: Session, request: ChatRequest, ip_address: str | None = None) -> ChatResponse:
        settings = get_settings()
        limiter_key = f"{request.channel}:{request.external_user_id}"
        if not rate_limiter.allow(limiter_key, settings.rate_limit_messages_per_minute):
            return ChatResponse(
                answer="Too many messages in a short time. Please wait a moment and try again.",
                language=request.preferred_language or "en",
                safety_flags=["rate_limited"],
            )

        language = request.preferred_language or detect_language(request.text)
        request.preferred_language = language
        redaction = redact_sensitive(request.text)
        assessment = assess_message(redaction.text, request.attachments)

        conversation = self._get_or_create_conversation(db, request, language)
        db.add(
            Message(
                conversation_id=conversation.id,
                direction="inbound",
                content=redaction.text,
                language=language,
                safety_flags=assessment.flags + redaction.findings,
                payload={
                    "channel": request.channel,
                    "ip_address": ip_address,
                    "attachments": [attachment.model_dump() for attachment in request.attachments],
                },
            )
        )

        if "prompt_injection_attempt" in assessment.flags:
            response = ChatResponse(
                answer=self._localized_security_refusal(language),
                language=language,
                safety_flags=assessment.flags,
            )
            self._store_outbound(db, conversation.id, response)
            db.commit()
            return response

        if "sensitive_attachment_rejected" in assessment.flags:
            response = ChatResponse(
                answer=self._localized_sensitive_attachment_refusal(language),
                language=language,
                safety_flags=assessment.flags,
            )
            self._store_outbound(db, conversation.id, response)
            db.commit()
            return response

        if is_account_action_request(redaction.text):
            response = ChatResponse(
                answer=self._localized_no_account_actions(language),
                language=language,
                safety_flags=assessment.flags + ["account_action_blocked"],
            )
            self._store_outbound(db, conversation.id, response)
            db.commit()
            return response

        wants_ticket = request.create_ticket or assessment.should_create_ticket
        if wants_ticket:
            response = self._handle_ticket_request(
                db=db,
                request=request,
                description=redaction.text,
                language=language,
                issue_type=assessment.issue_type,
                urgency=assessment.urgency,
                safety_flags=assessment.flags + redaction.findings,
            )
            self._store_outbound(db, conversation.id, response)
            db.commit()
            return response

        retrieval = search_knowledge(db, redaction.text, language)
        if retrieval.confidence < settings.retrieval_min_confidence:
            response = ChatResponse(
                answer=localized_unsure(language),
                language=language,
                confidence=retrieval.confidence,
                safety_flags=assessment.flags,
                needs_ticket_consent=True,
                sources=[chunk.source_title for chunk in retrieval.chunks],
            )
            self._store_outbound(db, conversation.id, response)
            db.commit()
            return response

        answer = self.answer_generator.generate(language, retrieval.chunks)
        response = ChatResponse(
            answer=answer,
            language=language,
            confidence=retrieval.confidence,
            safety_flags=assessment.flags,
            sources=[chunk.source_title for chunk in retrieval.chunks],
        )
        self._store_outbound(db, conversation.id, response)
        db.commit()
        return response

    def _handle_ticket_request(
        self,
        db: Session,
        request: ChatRequest,
        description: str,
        language: str,
        issue_type: str,
        urgency: str,
        safety_flags: list[str],
    ) -> ChatResponse:
        if not request.consent_to_ticket:
            return ChatResponse(
                answer=self._localized_consent_request(language, urgency),
                language=language,
                safety_flags=safety_flags,
                needs_ticket_consent=True,
            )
        ticket = create_ticket(db, request, description, issue_type, urgency, safety_flags)
        db.add(
            AuditLog(
                actor=f"{request.channel}:{request.external_user_id}",
                event_type="ticket_created",
                details={"ticket": ticket.public_id, "urgency": urgency, "issue_type": issue_type},
            )
        )
        return ChatResponse(
            answer=self._localized_ticket_created(language, ticket.public_id, urgency),
            language=language,
            safety_flags=safety_flags,
            ticket=to_ticket_response(ticket),
        )

    def _get_or_create_conversation(
        self, db: Session, request: ChatRequest, language: str
    ) -> Conversation:
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
            conversation.preferred_language = language
            conversation.user_role = request.user_role
            return conversation
        conversation = Conversation(
            channel=request.channel,
            external_user_id=request.external_user_id,
            preferred_language=language,
            user_role=request.user_role,
        )
        db.add(conversation)
        db.flush()
        return conversation

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

    def _localized_security_refusal(self, language: str) -> str:
        if language == "ms":
            return "Saya boleh bantu soalan sokongan DUDU Car, tetapi saya tidak boleh mengikut arahan yang cuba mengubah peraturan keselamatan sistem."
        if language == "zh":
            return "我可以协助处理 DUDU Car 客服问题，但不能执行试图绕过系统安全规则的指令。"
        return "I can help with DUDU Car support questions, but I cannot follow instructions that try to bypass system safety rules."

    def _localized_sensitive_attachment_refusal(self, language: str) -> str:
        if language == "ms":
            return "Untuk keselamatan anda, jangan hantar kad pembayaran, kata laluan, dokumen identiti, atau maklumat sensitif. Sila terangkan isu tanpa memuat naik dokumen tersebut."
        if language == "zh":
            return "为保障你的安全，请不要发送银行卡、密码、身份证件或其他敏感资料。请用文字说明问题即可。"
        return "For your safety, please do not send payment cards, passwords, identity documents, or other sensitive files. Describe the issue without uploading those documents."

    def _localized_no_account_actions(self, language: str) -> str:
        if language == "ms":
            return "Saya tidak boleh membuat bayaran balik, pembatalan, perubahan akaun, atau tindakan akaun lain. Saya boleh bantu rekodkan isu ini sebagai tiket untuk pasukan sokongan."
        if language == "zh":
            return "我不能直接退款、取消行程、更改账户或执行账户操作。我可以帮你创建客服工单，让支持团队跟进。"
        return "I cannot directly process refunds, cancellations, account changes, or other account actions. I can help create a support ticket for the team to review."

    def _localized_consent_request(self, language: str, urgency: str) -> str:
        emergency_note = ""
        if urgency == "urgent":
            emergency_note = " If anyone is in immediate danger, contact local emergency services first."
        if language == "ms":
            return (
                "Saya boleh buat tiket sokongan untuk pasukan DUDU Car. "
                "Dengan meneruskan, anda bersetuju maklumat isu ini disimpan untuk tujuan sokongan. "
                "Balas dengan persetujuan dan butiran yang relevan sahaja."
            )
        if language == "zh":
            return (
                "我可以为你创建 DUDU Car 客服工单。继续操作即表示你同意我们保存此问题资料用于客服跟进。"
                "请确认同意，并只提供与问题相关的资料。"
            )
        return (
            "I can create a support ticket for the DUDU Car team. "
            "By continuing, you agree that these issue details will be stored for support follow-up. "
            "Please confirm consent and provide only relevant details."
            f"{emergency_note}"
        )

    def _localized_ticket_created(self, language: str, public_id: str, urgency: str) -> str:
        if language == "ms":
            return f"Tiket sokongan anda telah dibuat: {public_id}. Keutamaan: {urgency}. Pasukan sokongan akan menyemaknya mengikut keutamaan."
        if language == "zh":
            return f"你的客服工单已创建：{public_id}。优先级：{urgency}。客服团队会按优先级跟进。"
        return f"Your support ticket has been created: {public_id}. Priority: {urgency}. The support team will review it based on priority."


chatbot_service = ChatbotService()
