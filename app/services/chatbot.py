import json
import logging
import re
import uuid
from datetime import datetime
from time import monotonic
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.models import (
    AuditLog,
    Conversation,
    KnowledgeDocument,
    MediaAttachment,
    Message,
    Ticket,
    WhatsAppInboundMessage,
    WhatsAppOutboundMessage,
)
from app.schemas import ChatRequest, ChatResponse
from app.services.answer_generation import (
    deterministic_answer,
    render_case_confirmation,
    render_case_receipt,
    render_control_prompt,
    render_ticket_declined,
    render_ticket_receipt,
    render_ticket_review,
)
from app.services.dialogue import (
    AgentUsage,
    DialogueRunResult,
    HISTORY_REFERENCE_PATTERN,
    _allowed_next_prompts,
    build_dialogue_context,
    load_dialogue_data,
    run_dialogue_agent,
    store_dialogue_data,
)
from app.services.guardrails import assess_message, is_account_action_request
from app.services.language import detect_language, is_language_selection, selected_language
from app.services.pii import redact_sensitive
from app.services.rate_limit import rate_limiter
from app.services.retrieval import RetrievedChunk, search_knowledge
from app.services.ticket_drafts import (
    DialogueData,
    FieldReferences,
    OperationReceipt,
    _asserted_detail,
    accept_ticket_offer,
    build_field_references,
    consent_answer,
    is_cancel,
    is_done,
    is_explicit_withdrawal,
    is_skip,
    is_submit,
    make_prompt,
    owned_case_view,
    prepare_ticket_review,
    record_consent,
    request_case_update,
    request_api_ticket_submission,
    request_ticket_submission,
    accumulate_ride_details,
    set_draft_status,
    update_ticket_draft,
    validate_draft,
)
from app.services.ticket_operations import get_owned_case, reopen_closed_ticket_for_customer
from app.services.tickets import create_ticket, to_ticket_response


logger = logging.getLogger(__name__)


def human_support_is_open(now: datetime | None = None) -> bool:
    local_now = now or datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
    return 9 <= local_now.hour < 18


class ChatbotService:
    """Run one bounded dialogue turn, then atomically apply its validated result."""

    def __init__(
        self,
        *,
        model=None,
        settings: Settings | None = None,
        metrics_sink: Callable[[dict], None] | None = None,
    ) -> None:
        self.model = model
        self.settings = settings
        self.metrics_sink = metrics_sink

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
        turn_started = monotonic()
        agent_usage = AgentUsage()
        agent_error = None
        agent_error_code = None
        used_fallback = False
        agent_failure_fallback = False
        agent_executions = 0
        tool_schema_chars = 0
        tool_schema_calls = 0
        settings = self.settings or get_settings()
        if self._rate_limited(db, request, ip_address, settings):
            if commit:
                db.commit()
            return ChatResponse(
                answer="Too many messages in a short time. Please wait a moment and try again.",
                language=request.preferred_language or "en",
                safety_flags=["rate_limited"],
            )

        turn_id = f"inbound:{inbound_id}" if inbound_id else f"chat:{uuid.uuid4()}"
        redaction = redact_sensitive(request.text)
        assessment = assess_message(redaction.text, request.attachments)
        try:
            conversation, is_new = self._get_or_create_conversation(db, request)
            language = self._select_language(conversation, request)
            request.preferred_language = language
            dialogue = load_dialogue_data(conversation)
            if (
                dialogue.draft
                and dialogue.draft.status in {"active", "paused"}
                and datetime.utcnow() >= dialogue.draft.expires_at
            ):
                dialogue.draft.status = "cancelled"
                dialogue.draft.version += 1
                dialogue.pending_prompt = None
            if request.attachments and dialogue.draft:
                evidence = [item.model_dump(mode="json") for item in request.attachments]
                for item in evidence:
                    if item not in dialogue.draft.evidence:
                        dialogue.draft.evidence.append(item)
                        dialogue.draft.attachment_count += 1
                        dialogue.draft.version += 1
                        dialogue.draft.review_required = True
                if dialogue.pending_prompt and dialogue.pending_prompt.purpose == "review":
                    dialogue.pending_prompt = None
            stale = bool(inbound_id) and not self._inbound_is_current(
                db, inbound_id, claim_token, request.external_user_id
            )
            last_message_id = self._last_message_id(db, conversation.id)
            expected_revision = conversation.dialogue_revision or 0
            pending = dialogue.pending_prompt
            references = build_field_references(
                request,
                request.text,
                pending_prompt=pending,
                draft=dialogue.draft,
                include_case_update=bool(self._case_reference(request.text)),
                include_description=(
                    (dialogue.draft is None and not assessment.is_human_request)
                    or (
                        assessment.issue_type not in {"general_faq", "human_escalation"}
                        and not assessment.is_human_request
                    )
                    or bool(pending and pending.purpose == "consent" and request.create_ticket)
                    or bool(pending and pending.purpose == "field" and pending.field == "description")
                    or bool(
                        pending
                        and pending.purpose == "field"
                        and pending.field != "description"
                        and getattr(request, pending.field or "", None)
                    )
                ),
            )
            local_control = self._local_control(
                request,
                dialogue,
                references,
                assessment,
                language,
                stale=stale,
            )
            prepared_locally = self._prepare_agent_dialogue(
                request, dialogue, references, language, local_control
            )
            if prepared_locally:
                dialogue = prepared_locally
            local_terminal = bool(
                local_control
                and (
                    not prepared_locally
                    or local_control in {"consent", "prompted_field"}
                )
            )
            local_values = dialogue.draft.fields.model_dump(exclude_none=True) if dialogue.draft else {}
            context = build_dialogue_context(
                db,
                conversation,
                redaction.text,
                dialogue=dialogue,
                local_values=local_values,
                current_input_applied=bool(prepared_locally),
                max_input_chars=settings.llm_max_input_chars,
            )
            history_reference = bool(
                context.current_question
                and HISTORY_REFERENCE_PATTERN.search(context.current_question.lower())
            )
            if local_terminal:
                retrieval_chunks = []
                retrieval_confidence = 0.0
            else:
                retrieval = search_knowledge(
                    db, "" if history_reference else context.current_question or "", language
                )
                retrieval_chunks = retrieval.chunks
                retrieval_confidence = retrieval.confidence
            conversation_id = conversation.id
            if commit:
                db.commit()

            use_agent = (
                not local_terminal
                and provider_allowed
                and commit
                and (
                    self.model is not None
                    or (settings.llm_enabled and settings.llm_customer_context_enabled)
                )
            )
            result = None
            if local_terminal:
                # Local controls are intentional: their prompt/version evidence is the
                # authorization boundary, so do not let the agent reinterpret them.
                used_fallback = True
                result = self._fallback(
                    request,
                    dialogue,
                    references,
                    assessment,
                    retrieval_chunks,
                    retrieval_confidence,
                    turn_id,
                    self._session_factory(db),
                    settings,
                    stale=stale,
                )
            elif use_agent:
                agent_executions = 1
                try:
                    result = run_dialogue_agent(
                        request,
                        context,
                        dialogue,
                        references,
                        session_factory=self._session_factory(db),
                        originating_turn=turn_id,
                        initial_chunks=retrieval_chunks,
                        model=self.model,
                        settings=settings,
                        usage=agent_usage,
                        before_first_model=(
                            self._before_first_model_dispatch(
                                db, inbound_id, claim_token, request.external_user_id
                            )
                            if inbound_id
                            else None
                        ),
                    )
                    tool_schema_chars = result.tool_schema_chars
                    tool_schema_calls = result.tool_schema_calls
                    prepared_draft = result.dialogue.draft
                    prepared_prompt = result.dialogue.pending_prompt
                    if (
                        assessment.should_create_ticket
                        and (
                            dialogue.draft is None
                            or dialogue.draft.status in {"cancelled", "submitted"}
                        )
                        and (
                            prepared_draft is None
                            or prepared_draft.status != "active"
                            or prepared_draft.issue_type != assessment.issue_type
                            or prepared_draft.priority != assessment.urgency
                            or prepared_prompt is None
                            or prepared_prompt.purpose != "consent"
                        )
                    ):
                        raise ValueError("mandatory_intake_not_prepared")
                    if dialogue.draft and dialogue.draft.status == "active":
                        current_references = self._current_references(
                            references, text=request.text
                        )
                        expected_fields = {
                            field: references.resolve(reference, field)[1]
                            for field, reference in current_references.items()
                            if field in {"name", "email", "phone_number"}
                            or (
                                pending
                                and pending.purpose == "field"
                                and field == pending.field
                            )
                            or (
                                pending
                                and pending.purpose == "details"
                                and field == "ride_details"
                            )
                        }
                        if expected_fields and (
                            prepared_draft is None
                            or any(
                                not self._field_value_applied(
                                    dialogue.draft, prepared_draft, field, value
                                )
                                for field, value in expected_fields.items()
                            )
                        ):
                            raise ValueError("current_fields_not_applied")
                        if (
                            expected_fields
                            and prepared_draft
                            and prepared_draft.consent
                            and validate_draft(prepared_draft).valid
                            and not result.ticket_submission
                            and (
                                not prepared_prompt
                                or {
                                    "purpose": prepared_prompt.purpose,
                                    "field": prepared_prompt.field,
                                } not in _allowed_next_prompts(result.dialogue)
                            )
                        ):
                            raise ValueError("next_prompt_required_after_field_progress")
                    if (
                        pending
                        and (
                            (pending.purpose == "review" and is_submit(request.text))
                            or (
                                pending.purpose == "details"
                                and (
                                    is_done(request.text, language)
                                    or is_skip(request.text, language)
                                )
                            )
                        )
                        and result.ticket_submission is None
                    ):
                        raise ValueError("submission_not_prepared")
                except Exception as exc:
                    result = None
                    agent_failure_fallback = True
                    agent_error = type(exc).__name__
                    if isinstance(exc, (ValueError, TimeoutError)) and re.fullmatch(
                        r"[a-z0-9_]+", str(exc)
                    ):
                        agent_error_code = str(exc)
                    logger.warning(
                        "dialogue_agent_failed error_type=%s error_code=%s",
                        type(exc).__name__, agent_error_code or "unclassified",
                    )
            if result is None:
                used_fallback = True
                result = (
                    self._local_result(self._local(language, "clarify"), dialogue)
                    if history_reference and not local_control
                    else self._fallback(
                        request,
                        dialogue,
                        references,
                        assessment,
                        retrieval_chunks,
                        retrieval_confidence,
                        turn_id,
                        self._session_factory(db),
                        settings,
                        stale=stale,
                    )
                )

            conversation = (
                db.query(Conversation)
                .filter_by(id=conversation_id)
                .populate_existing()
                .with_for_update()
                .one()
            )
            stale = stale or (conversation.dialogue_revision or 0) != expected_revision
            stale = stale or self._last_message_id(db, conversation.id) != last_message_id
            if inbound_id:
                stale = stale or not self._inbound_is_current(
                    db, inbound_id, claim_token, request.external_user_id
                )
            if stale or not self._sources_still_current(db, result.cited_sources):
                used_fallback = True
                result = self._fallback(
                    request,
                    load_dialogue_data(conversation),
                    references,
                    assessment,
                    [],
                    0.0,
                    turn_id,
                    self._session_factory(db),
                    settings,
                    stale=True,
                )

            turn_flags = assessment.flags + redaction.findings
            if is_account_action_request(request.text):
                turn_flags.append("account_action_blocked")
            response = self._commit_result(db, conversation, request, result, turn_flags)
            if is_new and not self._identity_question(request.text):
                response.answer = f"{self._bot_identity(response.language)}\n\n{response.answer}"
            if attachment_status:
                response.answer = f"{attachment_status}\n\n{response.answer}"

            db.add(
                Message(
                    conversation_id=conversation.id,
                    direction="inbound",
                    content=redaction.text,
                    language=response.language,
                    safety_flags=turn_flags,
                    payload={
                        "channel": request.channel,
                        "inbound_event_id": inbound_id,
                        "attachments": [item.model_dump() for item in request.attachments],
                    },
                )
            )
            self._store_outbound(db, conversation.id, response)
            if inbound_id:
                inbound = (
                    db.query(WhatsAppInboundMessage)
                    .filter_by(id=inbound_id)
                    .with_for_update()
                    .one()
                )
                if inbound.status != "processing" or inbound.claim_token != claim_token:
                    raise ValueError("inbound lease lost")
                db.add(
                    WhatsAppOutboundMessage(
                        inbound_message_id=inbound_id,
                        recipient=request.external_user_id,
                        body=response.answer[:4096],
                    )
                )
                inbound.status = "done"
                inbound.processed_at = datetime.utcnow()
                inbound.lease_until = None
            if commit:
                db.commit()
            else:
                db.flush()
            outcome = (
                "ticket_created" if response.ticket else
                "ticket_started" if response.needs_ticket_consent else
                "answered" if response.sources else "deterministic_fallback"
            )
            logger.info("chat_outcome %s", json.dumps({"outcome": outcome, "language": response.language}))
            if self.metrics_sink:
                self.metrics_sink(
                    {
                        "inbound_turns": 1,
                        "model_attempts": agent_usage.model_attempts,
                        "model_successes": agent_usage.model_successes,
                        "model_timeouts": agent_usage.model_timeouts,
                        "tool_calls": agent_usage.tool_calls,
                        "prompt_tokens": agent_usage.prompt_tokens,
                        "completion_tokens": agent_usage.completion_tokens,
                        "reasoning_tokens": agent_usage.reasoning_tokens,
                        "reasoning_usage_missing": agent_usage.reasoning_usage_missing,
                        "fallbacks": int(used_fallback),
                        "local_control": local_control,
                        "agent_executions": agent_executions,
                        "agent_failure_fallback": int(agent_failure_fallback),
                        "agent_error": agent_error,
                        "agent_error_code": agent_error_code,
                        "tool_schema_chars": tool_schema_chars,
                        "tool_schema_calls": tool_schema_calls,
                        "latency_ms": round((monotonic() - turn_started) * 1000),
                        "outcome": outcome,
                    }
                )
            return response
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def _dedicated_contact_field(
        request: ChatRequest,
        dialogue: DialogueData,
        references: FieldReferences,
    ) -> str | None:
        """Recognize only a standalone, validated first contact response."""
        prompt = dialogue.pending_prompt
        if (
            not prompt
            or prompt.purpose != "field"
            or prompt.field not in {"email", "phone_number"}
            or not dialogue.draft
            or getattr(dialogue.draft.fields, prompt.field) is not None
            or references.ambiguous_fields
        ):
            return None
        current = ChatbotService._current_references(references, text=request.text)
        if set(current) != {prompt.field}:
            return None
        try:
            _, value = references.resolve(current[prompt.field], prompt.field)
        except ValueError:
            return None
        raw = request.text.strip()
        if prompt.field == "email":
            return prompt.field if raw.casefold() == value.casefold() else None
        if not re.fullmatch(r"[+\d][\d ()-]{7,24}", raw):
            return None
        return prompt.field if re.sub(r"\D", "", raw) == re.sub(r"\D", "", value) else None

    def _local_control(
        self,
        request: ChatRequest,
        dialogue: DialogueData,
        references: FieldReferences,
        assessment,
        language: str,
        *,
        stale: bool,
    ) -> str | None:
        """Return the deterministic transition that must bypass the dialogue agent."""
        if stale:
            return "stale_turn"
        if assessment.is_safety_critical:
            return "safety_handoff"
        if self._identity_question(request.text):
            return "identity"
        if is_language_selection(request.text):
            return "language_selection"
        if "sensitive_attachment_rejected" in assessment.flags:
            return "sensitive_attachment"
        if request.text == "[attachment]":
            return "attachment_prompt"

        prompt = dialogue.pending_prompt
        prompt_authorized = bool(
            prompt and (request.channel == "whatsapp" or request.prompt_id == prompt.id)
        )
        if assessment.should_create_ticket:
            return "mandatory_handoff"
        if dialogue.draft:
            for status, control in (
                ("cancelled", "draft_cancel"),
                ("paused", "draft_pause"),
                ("active", "draft_resume"),
            ):
                if set_draft_status(
                    dialogue, status, customer_input=request.text, language=language
                ).status == "prepared":
                    return control
        if request.consent_to_ticket or request.create_ticket:
            return "api_control"
        if prompt and prompt_authorized:
            answer = consent_answer(request.text, language)
            if prompt.purpose in {"offer", "consent"} and answer is not None:
                return prompt.purpose
            if prompt.purpose == "case_confirmation" and answer is True:
                return "case_confirmation"
            if prompt.purpose == "review" and is_submit(request.text):
                return "submit"
            if prompt.purpose == "details" and (
                is_done(request.text, language)
                or is_skip(request.text, language)
                or answer is False
            ):
                return "details_complete"

        if (
            dialogue.draft
            and dialogue.draft.status == "active"
            and prompt_authorized
            and self._dedicated_contact_field(request, dialogue, references)
        ):
            return "prompted_field"
        if dialogue.draft and dialogue.draft.status == "active" and prompt_authorized:
            current = self._current_references(references, text=request.text)
            if any(
                field in current
                and getattr(dialogue.draft.fields, field) is not None
                and references.resolve(current[field], field)[1] != getattr(dialogue.draft.fields, field)
                for field in ("email", "phone_number")
            ):
                return "contact_correction"
        return None

    @staticmethod
    def _prepare_agent_dialogue(
        request: ChatRequest,
        dialogue: DialogueData,
        references: FieldReferences,
        language: str,
        control: str | None,
    ) -> DialogueData | None:
        """Apply a proven input transition before the agent chooses the next prompt."""
        prompt = dialogue.pending_prompt
        if control == "consent" and prompt:
            recorded = record_consent(
                dialogue,
                prompt_id=prompt.id,
                originating_turn=prompt.originating_turn,
                customer_input=request.text,
                language=language,
                api_control=request.consent_to_ticket,
            )
            if recorded.status != "prepared" or recorded.reason == "consent_declined":
                return None
            updated = update_ticket_draft(
                recorded.dialogue,
                references,
                ChatbotService._current_references(references, text=request.text),
            )
            return updated.dialogue if updated.status == "prepared" else None
        if control in {"prompted_field", "contact_correction"}:
            current = ChatbotService._current_references(references, text=request.text)
            if control == "contact_correction":
                current = {
                    field: reference for field, reference in current.items()
                    if field in {"email", "phone_number"}
                    and getattr(dialogue.draft.fields, field) is not None
                    and references.resolve(reference, field)[1] != getattr(dialogue.draft.fields, field)
                }
            updated = update_ticket_draft(
                dialogue,
                references,
                current,
            )
            if updated.status != "prepared":
                return None
            if control == "prompted_field":
                # The fulfilled prompt was never sent as a new authorization context.
                updated.dialogue.pending_prompt = None
            return updated.dialogue
        return None

    def _fallback(
        self,
        request: ChatRequest,
        dialogue: DialogueData,
        references: FieldReferences,
        assessment,
        chunks: list[RetrievedChunk],
        confidence: float,
        turn_id: str,
        session_factory,
        settings: Settings,
        *,
        stale: bool = False,
    ) -> DialogueRunResult:
        language = request.preferred_language or "en"
        working = dialogue.model_copy(deep=True)
        if stale:
            return self._local_result(self._local(language, "clarify"), working)
        if "sensitive_attachment_rejected" in assessment.flags:
            return self._local_result(self._sensitive_information_refusal(language), working)
        if request.text == "[attachment]":
            return self._local_result(self._attachment_text_prompt(language), working)
        if self._identity_question(request.text):
            return self._local_result(self._bot_identity(language), working)
        if is_language_selection(request.text):
            return self._local_result(self._render_pending(language, working), working)

        if (
            working.last_receipt
            and not working.pending_prompt
            and (
                request.prompt_id == working.last_receipt.prompt_id
                or request.text.strip() == working.last_receipt.customer_input
            )
        ):
            if working.draft and working.draft.status == "submitted":
                with session_factory() as read_db:
                    ticket = get_owned_case(
                        read_db,
                        public_id=working.last_receipt.case_reference,
                        channel=request.channel,
                        external_user_id=request.external_user_id,
                    )
                    if ticket:
                        return self._local_result(
                            render_ticket_receipt(
                                language,
                                ticket.public_id,
                                ticket.urgency,
                                support_is_open=human_support_is_open(),
                            ),
                            working,
                        )
            return self._local_result(render_case_receipt(language), working)

        prompt = working.pending_prompt
        prompt_authorized = bool(prompt and (request.channel == "whatsapp" or request.prompt_id == prompt.id))
        if (is_cancel(request.text, language) or is_explicit_withdrawal(request.text)) and working.draft:
            cancelled = set_draft_status(working, "cancelled", customer_input=request.text, language=language)
            if cancelled.status == "prepared":
                return self._local_result(render_ticket_declined(language), cancelled.dialogue)
        if working.draft and working.draft.status == "active":
            paused = set_draft_status(
                working, "paused", customer_input=request.text, language=language
            )
            if paused.status == "prepared":
                return self._local_result(self._local(language, "paused"), paused.dialogue)

        if prompt and prompt.purpose == "case_confirmation" and prompt_authorized:
            case_result = self._confirm_case_fallback(request, working, references, session_factory)
            if case_result:
                return DialogueRunResult(
                    answer=render_case_receipt(language),
                    dialogue=working,
                    case_update=case_result,
                )

        if not working.draft and (reference := self._case_reference(request.text)):
            staged_case = self._start_case_fallback(
                request, working, references, reference, turn_id, session_factory
            )
            if staged_case:
                case_dialogue, operation = staged_case
                return DialogueRunResult(
                    answer=render_case_confirmation(
                        language, operation.public_id, reopen=operation.reopen
                    ),
                    dialogue=case_dialogue,
                    case_update=operation,
                )

        if prompt and prompt.purpose == "offer" and prompt_authorized:
            answer = consent_answer(request.text, language)
            if answer is not None or request.create_ticket:
                offered = accept_ticket_offer(
                    working,
                    prompt_id=prompt.id,
                    originating_turn=prompt.originating_turn,
                    consent_prompt_turn=turn_id,
                    customer_input=request.text if answer is not None else "Yes",
                    language=language,
                    expiry_minutes=settings.intake_expiry_minutes,
                )
                if offered.status == "prepared":
                    if offered.reason == "offer_declined":
                        return self._local_result(render_ticket_declined(language), offered.dialogue)
                    return self._local_result(render_control_prompt(language, "consent"), offered.dialogue)

        if prompt and prompt.purpose == "consent" and prompt_authorized:
            consent = record_consent(
                working,
                prompt_id=prompt.id,
                originating_turn=prompt.originating_turn,
                customer_input=request.text,
                language=language,
                api_control=request.consent_to_ticket,
            )
            if consent.status == "prepared":
                working = consent.dialogue
                if consent.reason == "consent_declined":
                    return self._local_result(render_ticket_declined(language), working)
            elif consent_answer(request.text, language) is not None or request.consent_to_ticket:
                return self._local_result(render_control_prompt(language, "consent"), working)

        prompt = working.pending_prompt
        prompt_authorized = bool(prompt and (request.channel == "whatsapp" or request.prompt_id == prompt.id))
        if (
            prompt
            and prompt.purpose in {"details", "review"}
            and prompt_authorized
            and (
                is_done(request.text, language)
                or is_skip(request.text, language)
                or is_submit(request.text)
                or (prompt.purpose == "details" and consent_answer(request.text, language) is False)
                or request.create_ticket
            )
        ):
            submitted = request_ticket_submission(
                working,
                prompt_id=prompt.id,
                originating_turn=prompt.originating_turn,
                customer_input=request.text,
                language=language,
                api_control=request.create_ticket,
            )
            if submitted.status == "prepared" and submitted.operation:
                return DialogueRunResult(
                    answer="Ticket submission prepared.",
                    dialogue=submitted.dialogue,
                    ticket_submission=submitted.operation,
                )

        if working.draft and working.draft.status == "paused":
            resumed = set_draft_status(working, "active", customer_input=request.text, language=language)
            if resumed.status != "prepared":
                return self._local_result(self._local(language, "paused"), working)
            working = resumed.dialogue

        prompt = working.pending_prompt
        if (
            working.draft
            and prompt
            and prompt.purpose == "field"
            and prompt.field in {"name", "email", "phone_number"}
            and consent_answer(request.text, language) is False
        ):
            return self._local_result(self._required_contact_prompt(language), working)

        if working.draft and prompt and self._question_form(request.text):
            if confidence >= settings.retrieval_min_confidence and chunks:
                return DialogueRunResult(
                    answer=f"{deterministic_answer(language, chunks)}\n\n{self._render_pending(language, working)}",
                    source_titles=[chunks[0].source_title],
                    cited_sources=[self._source_ref(chunks[0])],
                    confidence=confidence,
                    dialogue=working,
                )
            return self._local_result(
                f"{self._local(language, 'clarify')}\n\n{self._render_pending(language, working)}",
                working,
            )

        if (not working.draft or working.draft.status in {"cancelled", "submitted"}) and (
            assessment.should_create_ticket or assessment.issue_type != "general_faq"
        ):
            updated = update_ticket_draft(
                working,
                references,
                self._current_references(references),
                expiry_minutes=settings.intake_expiry_minutes,
                issue_type=assessment.issue_type,
                priority=assessment.urgency,
            )
            if updated.status == "prepared":
                working = updated.dialogue
                if is_account_action_request(request.text) and working.draft:
                    working.draft.safety_flags = sorted(set(working.draft.safety_flags + ["account_action_blocked"]))
                working = make_prompt(working, "consent", turn_id)
                lead = self._intake_lead(language, assessment.issue_type, is_account_action_request(request.text))
                return self._local_result(f"{lead}\n\n{render_control_prompt(language, 'consent')}", working)

        if working.draft and working.draft.status in {"cancelled", "submitted"}:
            if confidence >= settings.retrieval_min_confidence and chunks:
                return DialogueRunResult(
                    answer=deterministic_answer(language, chunks),
                    source_titles=[chunks[0].source_title],
                    cited_sources=[self._source_ref(chunks[0])],
                    confidence=confidence,
                    dialogue=working,
                )
            return self._local_result(self._local(language, "clarify"), working)

        if not working.draft:
            if confidence >= settings.retrieval_min_confidence and chunks:
                return DialogueRunResult(
                    answer=deterministic_answer(language, chunks),
                    source_titles=[chunks[0].source_title],
                    cited_sources=[self._source_ref(chunks[0])],
                    confidence=confidence,
                    dialogue=working,
                )
            return self._local_result(self._local(language, "clarify"), working)

        if not prompt_authorized and prompt and request.channel != "whatsapp":
            return self._local_result(self._render_pending(language, working), working)

        updated = update_ticket_draft(
            working,
            references,
            self._current_references(references),
            expiry_minutes=settings.intake_expiry_minutes,
            issue_type=assessment.issue_type if assessment.should_create_ticket else None,
            priority=assessment.urgency if assessment.should_create_ticket else None,
        )
        if updated.status == "prepared":
            working = updated.dialogue
        elif updated.reason == "details_too_long":
            return self._local_result(self._details_too_long(language), working)
        draft = working.draft
        if not draft:
            return self._local_result(self._local(language, "clarify"), working)
        if not draft.consent:
            working = make_prompt(working, "consent", turn_id)
            return self._local_result(render_control_prompt(language, "consent"), working)
        validation = validate_draft(draft)
        if request.create_ticket and request.consent_to_ticket and request.prompt_id:
            submitted = request_api_ticket_submission(
                working,
                consent_prompt_id=request.prompt_id,
                customer_input=request.text,
            )
            if submitted.status == "prepared" and submitted.operation:
                return DialogueRunResult(
                    answer="Ticket submission prepared.",
                    dialogue=submitted.dialogue,
                    ticket_submission=submitted.operation,
                )
        if validation.missing_fields or validation.invalid_fields:
            field = (validation.invalid_fields or validation.missing_fields)[0]
            working = make_prompt(working, "field", turn_id, field=field)
            return self._local_result(render_control_prompt(language, "field", field), working)
        if draft.review_required:
            reviewed = prepare_ticket_review(working, originating_turn=turn_id)
            if reviewed.status == "prepared":
                return self._local_result(
                    render_ticket_review(
                        language,
                        draft.fields.model_dump(exclude_none=True),
                        proposed_priority=draft.proposed_priority,
                    ),
                    reviewed.dialogue,
                )
        working = make_prompt(working, "details", turn_id)
        return self._local_result(render_control_prompt(language, "details", "ride_details"), working)

    def _commit_result(
        self,
        db: Session,
        conversation: Conversation,
        request: ChatRequest,
        result: DialogueRunResult,
        flags: list[str],
    ) -> ChatResponse:
        if result.ticket_submission and result.case_update:
            raise ValueError("conflicting staged operations")
        dialogue = result.dialogue.model_copy(deep=True)
        answer = result.answer
        ticket_response = None
        if dialogue.draft:
            dialogue.draft.safety_flags = sorted(set(dialogue.draft.safety_flags + flags))
            if request.attachments:
                evidence = [item.model_dump(mode="json") for item in request.attachments]
                for item in evidence:
                    if item not in dialogue.draft.evidence:
                        dialogue.draft.evidence.append(item)
                        dialogue.draft.attachment_count += 1
        if result.ticket_submission:
            ticket, dialogue = self._apply_ticket_submission(db, conversation, request, dialogue, result.ticket_submission)
            ticket_response = to_ticket_response(ticket)
            answer = render_ticket_receipt(
                request.preferred_language or "en",
                ticket.public_id,
                ticket.urgency,
                support_is_open=human_support_is_open(),
            )
        elif result.case_update and result.case_update.confirmed:
            dialogue = self._apply_case_update(db, conversation, request, dialogue, result.case_update)
            answer = render_case_receipt(request.preferred_language or "en")

        store_dialogue_data(conversation, dialogue)
        conversation.preferred_language = request.preferred_language or conversation.preferred_language
        if request.user_role != "unknown" or not conversation.user_role:
            conversation.user_role = request.user_role
        if dialogue.draft:
            conversation.risk_level = dialogue.draft.priority
            if dialogue.draft.issue_type == "partnership" and conversation.user_role == "unknown":
                conversation.user_role = "business_partner"
        response = ChatResponse(
            answer=answer,
            language=request.preferred_language or "en",
            confidence=result.confidence,
            safety_flags=sorted(set(flags)),
            needs_ticket_consent=bool(dialogue.pending_prompt and dialogue.pending_prompt.purpose == "consent"),
            ticket=ticket_response,
            sources=list(dict.fromkeys(result.source_titles)),
        )
        response.prompt_id = dialogue.pending_prompt.id if dialogue.pending_prompt else None
        return response

    def _apply_ticket_submission(
        self,
        db: Session,
        conversation: Conversation,
        request: ChatRequest,
        dialogue: DialogueData,
        staged: OperationReceipt,
    ) -> tuple[Ticket, DialogueData]:
        draft = dialogue.draft
        prompt = dialogue.pending_prompt
        if not draft:
            raise ValueError("ticket submission lost its draft")
        verified = (
            request_ticket_submission(
                dialogue,
                prompt_id=staged.prompt_id,
                originating_turn=prompt.originating_turn,
                customer_input=staged.customer_input,
                language=request.preferred_language or "en",
            )
            if prompt
            else request_api_ticket_submission(
                dialogue,
                consent_prompt_id=staged.prompt_id,
                customer_input=staged.customer_input,
            )
        )
        if verified.status != "prepared" or not verified.operation or verified.operation.operation_id != staged.operation_id:
            raise ValueError("ticket submission became invalid")
        fields = draft.fields
        ticket_request = ChatRequest(
            channel=request.channel,
            external_user_id=request.external_user_id,
            text=fields.description or "",
            user_role=conversation.user_role or request.user_role,
            preferred_language=request.preferred_language,
            name=fields.name,
            email=fields.email,
            phone_number=fields.phone_number,
            account_id=fields.account_id,
            trip_id=fields.trip_id,
            ride_details=fields.ride_details,
            consent_to_ticket=True,
        )
        ticket = create_ticket(
            db,
            ticket_request,
            fields.description or "",
            draft.proposed_issue_type or draft.issue_type,
            draft.proposed_priority or draft.priority,
            draft.safety_flags,
        )
        ticket.conversation_id = conversation.id
        bound = self._bind_media(db, conversation.id, draft.evidence_group, ticket)
        ticket.attachment_count = draft.attachment_count + sum(item.status == "approved" for item in bound)
        ticket.extra = {
            **(ticket.extra or {}),
            "supporting_evidence": draft.evidence,
            "customer_updates": draft.safety_notes,
        }
        db.add(
            AuditLog(
                actor="customer",
                event_type="ticket_created",
                subject_type="ticket",
                subject_id=ticket.id,
                details={"ticket": ticket.public_id, "urgency": ticket.urgency, "issue_type": ticket.issue_type},
            )
        )
        draft.status = "submitted"
        draft.version += 1
        draft.evidence_group = None
        dialogue.evidence_group = None
        dialogue.pending_prompt = None
        dialogue.pending_offer = None
        dialogue.last_case_reference = ticket.public_id
        dialogue.pending_case_update = None
        dialogue.last_receipt = staged.model_copy(update={"case_reference": ticket.public_id})
        return ticket, dialogue

    def _apply_case_update(self, db, conversation, request, dialogue, staged) -> DialogueData:
        pending = dialogue.pending_prompt
        pending_update = dialogue.pending_case_update
        ticket = get_owned_case(
            db,
            ticket_id=staged.case_id,
            channel=request.channel,
            external_user_id=request.external_user_id,
            for_update=True,
        )
        if (
            not ticket
            or not pending
            or not pending_update
            or pending.operation_id != staged.operation_id
            or pending_update.operation_id != staged.operation_id
            or len((ticket.extra or {}).get("customer_updates", [])) >= 20
        ):
            raise ValueError("case update became invalid")
        owned = owned_case_view(
            case_id=ticket.id,
            public_id=ticket.public_id,
            status=ticket.status,
            channel=ticket.channel,
            external_user_id=ticket.external_user_id,
            expected_channel=request.channel,
            expected_external_user_id=request.external_user_id,
            update_count=len((ticket.extra or {}).get("customer_updates", [])),
        )
        verified = request_case_update(
            dialogue,
            owned,
            FieldReferences([]),
            originating_turn=pending.originating_turn,
            customer_input=request.text,
            prompt_id=pending.id,
            language=request.preferred_language or "en",
        )
        if verified.status != "prepared" or not verified.operation or verified.operation.operation_id != staged.operation_id:
            raise ValueError("case confirmation became invalid")
        if ticket.status == "closed" and not reopen_closed_ticket_for_customer(
            db,
            channel=request.channel,
            external_user_id=request.external_user_id,
            public_id=ticket.public_id,
        ):
            raise ValueError("case reopen became invalid")
        updates = list((ticket.extra or {}).get("customer_updates", []))
        updates.append(staged.update)
        ticket.extra = {**(ticket.extra or {}), "customer_updates": updates}
        for key, value in staged.fields.items():
            setattr(ticket, key, value)
        bound = self._bind_media(db, conversation.id, staged.evidence_group, ticket)
        ticket.attachment_count += sum(item.status == "approved" for item in bound)
        db.add(
            AuditLog(
                actor="customer",
                event_type="ticket_updated_by_customer",
                subject_type="ticket",
                subject_id=ticket.id,
                details={"ticket": ticket.public_id},
            )
        )
        dialogue.pending_prompt = None
        dialogue.pending_case_update = None
        dialogue.evidence_group = None
        dialogue.last_case_reference = ticket.public_id
        dialogue.last_receipt = OperationReceipt(
            operation_id=staged.operation_id,
            prompt_id=staged.prompt_id,
            version=pending.version or 1,
            customer_input=request.text[:64],
            case_reference=ticket.public_id,
        )
        return dialogue

    @staticmethod
    def _bind_media(db: Session, conversation_id: str, evidence_group: str | None, ticket: Ticket) -> list[MediaAttachment]:
        if not evidence_group:
            return []
        rows = (
            db.query(MediaAttachment)
            .filter(
                MediaAttachment.conversation_id == conversation_id,
                MediaAttachment.evidence_group == evidence_group,
                MediaAttachment.ticket_id.is_(None),
                MediaAttachment.status.in_(("queued", "approved")),
            )
            .with_for_update()
            .all()
        )
        for row in rows:
            row.ticket_id = ticket.id
        return rows

    def _confirm_case_fallback(self, request, dialogue, references, session_factory):
        if consent_answer(request.text, request.preferred_language or "en") is not True:
            return None
        pending = dialogue.pending_case_update
        prompt = dialogue.pending_prompt
        if not pending or not prompt:
            return None
        with session_factory() as read_db:
            ticket = get_owned_case(
                read_db,
                ticket_id=pending.case_id,
                channel=request.channel,
                external_user_id=request.external_user_id,
            )
            if not ticket:
                return None
            owned = owned_case_view(
                case_id=ticket.id,
                public_id=ticket.public_id,
                status=ticket.status,
                channel=ticket.channel,
                external_user_id=ticket.external_user_id,
                expected_channel=request.channel,
                expected_external_user_id=request.external_user_id,
                update_count=len((ticket.extra or {}).get("customer_updates", [])),
            )
        result = request_case_update(
            dialogue,
            owned,
            references,
            originating_turn=prompt.originating_turn,
            customer_input=request.text,
            prompt_id=prompt.id,
            language=request.preferred_language or "en",
        )
        return result.operation if result.status == "prepared" else None

    def _start_case_fallback(
        self, request, dialogue, references, public_id, turn_id, session_factory
    ):
        update_reference = next(
            (
                item.id
                for item in references.public
                if item.source == "current_input" and item.field == "description"
            ),
            None,
        )
        if not update_reference:
            return None
        with session_factory() as read_db:
            ticket = get_owned_case(
                read_db,
                public_id=public_id,
                channel=request.channel,
                external_user_id=request.external_user_id,
            )
            if not ticket:
                return None
            owned = owned_case_view(
                case_id=ticket.id,
                public_id=ticket.public_id,
                status=ticket.status,
                channel=ticket.channel,
                external_user_id=ticket.external_user_id,
                expected_channel=request.channel,
                expected_external_user_id=request.external_user_id,
                update_count=len((ticket.extra or {}).get("customer_updates", [])),
            )
        result = request_case_update(
            dialogue,
            owned,
            references,
            update_reference=update_reference,
            originating_turn=turn_id,
        )
        return (result.dialogue, result.operation) if result.status == "prepared" and result.operation else None

    @staticmethod
    def _case_reference(text: str) -> str | None:
        match = re.search(r"DUDU-\d{8}-[A-Z0-9]{5}", text, re.I)
        return match.group().upper() if match else None

    @staticmethod
    def _question_form(text: str) -> bool:
        return bool(re.search(
            r"(?i)[?？]|^(?:how|what|why|where|when|can |could |explain|if |suppose|bagaimana|apakah|mengapa|boleh|jika|如何|怎么|什么|为什么|如果)",
            text.strip(),
        ))

    @staticmethod
    def _current_references(
        references: FieldReferences, *, text: str | None = None
    ) -> dict:
        defer_description = text is not None and _asserted_detail(text) != text.strip()
        return {
            item.field: item.id
            for item in references.public
            if item.source == "current_input"
            and not (defer_description and item.field == "description")
        }

    @staticmethod
    def _field_value_applied(original, prepared, field: str, value: str) -> bool:
        actual = getattr(prepared.fields, field)
        if field == "ride_details":
            return actual == accumulate_ride_details(getattr(original.fields, field), value)
        return actual == value

    @staticmethod
    def _local_result(answer: str, dialogue: DialogueData) -> DialogueRunResult:
        return DialogueRunResult(answer=answer, dialogue=dialogue)

    @staticmethod
    def _source_ref(chunk: RetrievedChunk) -> dict:
        return {"document_key": chunk.document_key, "version": chunk.version, "language": chunk.language}

    @staticmethod
    def _sources_still_current(db: Session, sources: list[dict]) -> bool:
        now = datetime.utcnow()
        for source in sources:
            exists = (
                db.query(KnowledgeDocument.id)
                .filter_by(
                    document_key=source["document_key"],
                    version=source["version"],
                    language=source["language"],
                    status="active",
                )
                .filter(KnowledgeDocument.effective_at.is_not(None), KnowledgeDocument.effective_at <= now)
                .first()
            )
            if not exists:
                return False
        return True

    def _inbound_is_current(self, db: Session, inbound_id: str, claim_token: str | None, sender: str) -> bool:
        inbound = db.query(WhatsAppInboundMessage).filter_by(id=inbound_id).with_for_update().one()
        if (
            inbound.status != "processing"
            or inbound.claim_token != claim_token
            or not inbound.lease_until
            or inbound.lease_until < datetime.utcnow()
        ):
            raise ValueError("inbound lease lost")
        previous = (
            db.query(WhatsAppInboundMessage)
            .filter(
                WhatsAppInboundMessage.sender == sender,
                WhatsAppInboundMessage.id != inbound_id,
                WhatsAppInboundMessage.status == "done",
            )
            .order_by(WhatsAppInboundMessage.created_at.desc(), WhatsAppInboundMessage.id.desc())
            .first()
        )
        timestamp = str((inbound.payload or {}).get("timestamp", ""))
        previous_timestamp = str((previous.payload or {}).get("timestamp", "")) if previous else ""
        if timestamp and previous_timestamp:
            try:
                if int(timestamp) < int(previous_timestamp):
                    return False
            except ValueError:
                return False
        quoted = str((inbound.payload or {}).get("context_message_id", ""))
        if quoted:
            latest = (
                db.query(WhatsAppOutboundMessage)
                .filter_by(recipient=sender)
                .order_by(WhatsAppOutboundMessage.created_at.desc(), WhatsAppOutboundMessage.id.desc())
                .first()
            )
            if not latest or latest.provider_message_id != quoted:
                return False
        return True

    def _before_first_model_dispatch(
        self,
        db: Session,
        inbound_id: str | None,
        claim_token: str | None,
        sender: str,
    ) -> Callable[[float], float]:
        """Persist the inbound agent-attempt fence immediately before dispatch."""

        def mark_attempted(deadline: float) -> float:
            if not inbound_id or not self._inbound_is_current(
                db, inbound_id, claim_token, sender
            ):
                raise ValueError("inbound lease lost")
            inbound = (
                db.query(WhatsAppInboundMessage)
                .filter_by(id=inbound_id)
                .with_for_update()
                .populate_existing()
                .one()
            )
            payload = dict(inbound.payload or {})
            if payload.get("agent_attempted") or payload.get("provider_attempted"):
                raise ValueError("inbound agent already attempted")
            if monotonic() >= deadline:
                db.rollback()
                raise TimeoutError("agent_deadline_exceeded")
            payload["agent_attempted"] = True
            inbound.payload = payload
            # The fence must survive a provider crash so the next worker uses
            # deterministic fallback instead of running another agent turn.
            db.commit()
            remaining = deadline - monotonic()
            if remaining <= 0:
                # No dispatch occurred. Clear only our own marker so a known
                # pre-dispatch timeout does not suppress a later retry.
                owned = (
                    db.query(WhatsAppInboundMessage)
                    .filter_by(id=inbound_id, claim_token=claim_token, status="processing")
                    .with_for_update()
                    .populate_existing()
                    .one_or_none()
                )
                if owned:
                    payload = dict(owned.payload or {})
                    payload.pop("agent_attempted", None)
                    owned.payload = payload
                db.commit()
                raise TimeoutError("agent_deadline_exceeded")
            return remaining

        return mark_attempted

    @staticmethod
    def _last_message_id(db: Session, conversation_id: str) -> str | None:
        row = (
            db.query(Message.id)
            .filter_by(conversation_id=conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .first()
        )
        return row[0] if row else None

    @staticmethod
    def _session_factory(db: Session):
        return sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)

    @staticmethod
    def _get_or_create_conversation(db: Session, request: ChatRequest) -> tuple[Conversation, bool]:
        conversation = (
            db.query(Conversation)
            .filter_by(channel=request.channel, external_user_id=request.external_user_id)
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

    @staticmethod
    def _select_language(conversation: Conversation, request: ChatRequest) -> str:
        if request.preferred_language:
            return request.preferred_language
        if selected := selected_language(request.text):
            return selected
        detected = detect_language(request.text)
        dialogue = load_dialogue_data(conversation)
        active = bool(dialogue.draft and dialogue.draft.status in {"active", "paused"})
        if active and detected == "en" and conversation.preferred_language != "en" and len(request.text.split()) <= 3:
            return conversation.preferred_language
        return detected

    @staticmethod
    def _rate_limited(db: Session, request: ChatRequest, ip_address: str | None, settings: Settings) -> bool:
        keys = []
        if ip_address and request.channel != "whatsapp":
            keys.append(f"chat-ip:{ip_address}")
        if request.channel != "whatsapp":
            keys.append(f"chat-user:{request.channel}:{request.external_user_id}")
        return not all(
            rate_limiter.allow(db, key, settings.rate_limit_messages_per_minute, max_keys=settings.rate_limit_max_keys)
            for key in keys
        )

    @staticmethod
    def _store_outbound(db: Session, conversation_id: str, response: ChatResponse) -> None:
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

    @staticmethod
    def _identity_question(text: str) -> bool:
        return bool(re.fullmatch(
            r"\s*(?:hi[,! ]+)?(?:are you (?:a human|human|an? ai|ai|a bot|a robot)|你是(?:人工|真人|机器人|AI)(?:吗)?|adakah (?:anda|awak) (?:manusia|AI|bot))\s*[?？.!。]*\s*",
            text,
            re.I,
        ))

    @staticmethod
    def _render_pending(language: str, dialogue: DialogueData) -> str:
        prompt = dialogue.pending_prompt
        if not prompt:
            return ChatbotService._bot_identity(language)
        if prompt.purpose == "offer":
            return ChatbotService._offer_prompt(language)
        if prompt.purpose == "review" and dialogue.draft:
            return render_ticket_review(language, dialogue.draft.fields.model_dump(exclude_none=True))
        if prompt.purpose == "case_confirmation" and dialogue.pending_case_update:
            return render_case_confirmation(language, dialogue.pending_case_update.case_reference, reopen=False)
        return render_control_prompt(language, prompt.purpose, prompt.field)

    @staticmethod
    def _bot_identity(language: str) -> str:
        return {
            "en": "Hi! I’m DUDU Car’s automated AI assistant. I’m happy to help.",
            "ms": "Hai! Saya pembantu AI automatik DUDU Car. Saya gembira dapat membantu.",
            "zh": "你好！我是 DUDU Car 的 AI 自动客服助手。很高兴为你服务。",
        }[language]

    @staticmethod
    def _offer_prompt(language: str) -> str:
        return {
            "en": "Would you like a support ticket for human follow-up? Reply Yes or No.",
            "ms": "Adakah anda mahu tiket sokongan untuk susulan oleh pegawai? Balas Ya atau Tidak.",
            "zh": "你希望创建客服工单，由人工客服跟进吗？请回复同意或不同意。",
        }[language]

    @staticmethod
    def _local(language: str, key: str) -> str:
        return {
            "clarify": {
                "en": "Could you clarify which DUDU Car service or question you mean?",
                "ms": "Boleh jelaskan perkhidmatan atau soalan DUDU Car yang anda maksudkan?",
                "zh": "请说明你指的是哪项 DUDU Car 服务或问题？",
            },
            "paused": {
                "en": "Your ticket draft is paused. You can resume it or ask another question.",
                "ms": "Draf tiket dijeda. Anda boleh menyambungnya atau bertanya soalan lain.",
                "zh": "工单草稿已暂停。你可以继续填写或询问其他问题。",
            },
        }[key][language]

    @staticmethod
    def _attachment_text_prompt(language: str) -> str:
        return {
            "en": "Please describe your support question in a text message. I cannot view the attachment.",
            "ms": "Sila terangkan soalan sokongan dalam mesej teks. Saya tidak boleh melihat lampiran.",
            "zh": "请用文字描述客服问题。我无法查看附件内容。",
        }[language]

    @staticmethod
    def _required_contact_prompt(language: str) -> str:
        return {
            "en": "A name, valid email and phone number are required to submit a ticket. You can provide them later, or reply Stop to cancel.",
            "ms": "Nama, e-mel sah dan nombor telefon diperlukan untuk menghantar tiket. Anda boleh memberikannya kemudian, atau balas Berhenti untuk membatalkan.",
            "zh": "提交工单需要姓名、有效邮箱和电话号码。你可以稍后提供，或回复“停止”取消。",
        }[language]

    @staticmethod
    def _details_too_long(language: str) -> str:
        return {
            "en": "Ticket details are limited to 2,000 characters in total. Your earlier details are kept; this addition was not added. Please send a shorter version.",
            "ms": "Butiran tiket terhad kepada 2,000 aksara keseluruhan. Butiran terdahulu disimpan; tambahan ini tidak ditambah. Sila hantar versi lebih ringkas.",
            "zh": "工单详情总共最多可包含 2,000 个字符。之前的资料已保留；本次补充未加入。请发送较短的版本。",
        }[language]

    @staticmethod
    def _sensitive_information_refusal(language: str) -> str:
        return {
            "en": "To help keep your information safe, please do not send payment-card details, passwords, OTPs, identity documents, or other unnecessary sensitive information.",
            "ms": "Untuk membantu melindungi maklumat anda, jangan hantar butiran kad pembayaran, kata laluan, OTP, dokumen identiti, atau maklumat sensitif lain yang tidak diperlukan.",
            "zh": "为了帮助保护你的资料，请勿发送银行卡资料、密码、一次性验证码、身份证件或其他不必要的敏感信息。",
        }[language]

    @staticmethod
    def _intake_lead(language: str, issue_type: str, prohibited: bool) -> str:
        if prohibited:
            return {
                "en": "I can’t perform refunds, cancellations, bookings, payments, account changes, approvals, suspensions, or bans, but I can help create a support ticket for review.",
                "ms": "Saya tidak boleh membuat bayaran balik, pembatalan, tempahan, pembayaran atau perubahan akaun, tetapi saya boleh membantu membuat tiket untuk semakan.",
                "zh": "我不能执行退款、取消、预订、付款或账户更改，但可以协助创建工单供团队审核。",
            }[language]
        if issue_type == "safety_incident":
            return {
                "en": "Your safety comes first. If anyone is in immediate danger, contact Malaysian emergency services at 999 first. Once you are safe, I can help create an urgent ticket.",
                "ms": "Keselamatan anda adalah keutamaan. Jika sesiapa berada dalam bahaya segera, hubungi perkhidmatan kecemasan Malaysia di 999 terlebih dahulu. Setelah anda selamat, saya boleh membantu membuat tiket segera.",
                "zh": "你的安全最重要。如果任何人正面临紧急危险，请先拨打马来西亚紧急求助电话 999。确认安全后，我可以协助创建紧急工单。",
            }[language]
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
            "en": "I can help create a support ticket for human follow-up. Human support is available 9:00 AM–6:00 PM every day, Malaysia time.",
            "ms": "Saya boleh membantu membuat tiket sokongan untuk susulan oleh pegawai. Sokongan manusia tersedia setiap hari, 9:00 pagi–6:00 petang waktu Malaysia.",
            "zh": "我可以协助创建客服工单，由人工客服跟进。人工客服时间为马来西亚时间每天上午 9:00 至下午 6:00。",
        }[language]


chatbot_service = ChatbotService()
