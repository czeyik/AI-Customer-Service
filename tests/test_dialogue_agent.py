import json
import time
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from langchain_openai import ChatOpenAI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.services import dialogue as dialogue_service
from app.models import Base, KnowledgeChunk, KnowledgeDocument, Ticket
from app.schemas import ChatRequest
from app.services.dialogue import DialogueContext, run_dialogue_agent
from app.services.pii import provider_question
from app.services.retrieval import RetrievedChunk
from app.services.ticket_drafts import (
    DialogueData,
    build_field_references,
    extract_customer_fields,
    make_prompt,
    new_draft,
    record_consent,
)


def completion(tool_name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {
        "id": f"chatcmpl-{call_id}",
        "object": "chat.completion",
        "created": 1,
        "model": "glm-5.3-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "completion_tokens_details": {"reasoning_tokens": 1},
        },
    }


def plain_completion(content: str = "Plain text answer") -> dict:
    body = completion("DialogueAnswer", {}, "plain")
    body["choices"][0]["message"] = {"role": "assistant", "content": content}
    body["choices"][0]["finish_reason"] = "stop"
    return body


def multi_completion(calls: list[tuple[str, dict]], call_id: str = "batch") -> dict:
    body = completion(calls[0][0], calls[0][1], call_id)
    body["choices"][0]["message"]["tool_calls"] = [
        {
            "id": f"{call_id}-{index}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }
        for index, (name, arguments) in enumerate(calls)
    ]
    return body


def model(handler) -> ChatOpenAI:
    return ChatOpenAI(
        api_key="test-key",
        base_url="https://api.z.ai/api/paas/v4/",
        model="glm-5.3-flash",
        max_retries=0,
        timeout=0.1,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.fixture()
def sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[KnowledgeDocument.__table__, KnowledgeChunk.__table__, Ticket.__table__],
    )
    return sessionmaker(bind=engine)


def run(
    handler,
    sessions,
    *,
    text="How long does support take?",
    language="en",
    dialogue=None,
    initial_chunks=None,
    request_fields=None,
    history=None,
    state=None,
    max_input_chars=8000,
    before_first_model=None,
):
    request = ChatRequest(
        external_user_id="customer-1",
        text=text,
        preferred_language=language,
        **(request_fields or {}),
    )
    dialogue = dialogue or DialogueData()
    references = build_field_references(
        request,
        text,
        pending_prompt=dialogue.pending_prompt,
        draft=dialogue.draft,
        include_case_update=True,
    )
    safe_question = provider_question(
        text, extract_customer_fields(request, text).values
    )
    context = DialogueContext(
        current_question=safe_question,
        state=state or {"draft": None},
        history=history or [],
        input_chars=len(text),
    )
    return run_dialogue_agent(
        request,
        context,
        dialogue,
        references,
        session_factory=sessions,
        originating_turn="turn-1",
        initial_chunks=initial_chunks,
        model=model(handler),
        settings=Settings(llm_max_input_chars=max_input_chars),
        before_first_model=before_first_model,
    )


@pytest.mark.parametrize(
    ("language", "text", "history", "answer"),
    [
        (
            "en",
            "Please answer my first question again.",
            [
                {"role": "user", "content": "How do I book a ride?"},
                {"role": "assistant", "content": "Use the booking screen."},
            ],
            "Please restate it.",
        ),
        (
            "ms",
            "Sila jawab soalan pertama saya semula.",
            [
                {"role": "user", "content": "Bagaimana saya tempah perjalanan?"},
                {"role": "assistant", "content": "Gunakan skrin tempahan."},
            ],
            "Sila nyatakan semula.",
        ),
        (
            "zh",
            "请再回答我的第一个问题。",
            [
                {"role": "user", "content": "如何预订行程？"},
                {"role": "assistant", "content": "请使用预订页面。"},
            ],
            "请重新说明。",
        ),
    ],
)
def test_current_payload_binds_language_and_ordered_history(
    sessions, language, text, history, answer
):
    payloads = []

    def handler(request):
        body = json.loads(request.content)
        payloads.append(json.loads(body["messages"][-1]["content"]))
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": answer, "citation_ids": [], "next_prompt": None},
            ),
        )

    result = run(
        handler,
        sessions,
        text=text,
        language=language,
        history=history,
        initial_chunks=[
            RetrievedChunk(
                content="Irrelevant approved information.",
                source_title="Irrelevant",
                language=language,
                score=0.2,
                document_key="irrelevant",
                version=1,
            )
        ],
    )

    assert result.answer == answer
    assert len(payloads) == 1
    assert payloads[0]["language"] == language
    assert payloads[0]["current_question"] == text
    assert payloads[0]["history"] == history
    assert payloads[0]["prior_user_questions"] == [history[0]["content"]]
    assert payloads[0]["initial_excerpts"] == []


def test_applied_contact_correction_retains_retrieval_and_valid_prompts(sessions):
    dialogue = make_prompt(
        DialogueData(draft=new_draft(expiry_minutes=60)), "consent", "turn-0"
    )
    consented = record_consent(
        dialogue,
        prompt_id=dialogue.pending_prompt.id,
        originating_turn="turn-0",
        customer_input="Yes",
        language="en",
    ).dialogue
    consented.draft.fields.name = "Alex"
    state = dialogue_service._safe_state(consented)
    state["current_input_applied"] = True

    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][-1]["content"])
        assert payload["initial_excerpts"][0]["document"] == "support-hours"
        assert "search_knowledge" in {tool["function"]["name"] for tool in body["tools"]}
        assert payload["state"]["current_input_applied"] is True
        assert payload["state"]["allowed_next_prompts"] == [
            None,
            {"purpose": "field", "field": "email"},
            {"purpose": "field", "field": "phone_number"},
            {"purpose": "field", "field": "description"},
        ]
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Which email address should support use?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "field", "field": "email"},
                },
            ),
        )

    result = run(
        handler,
        sessions,
        text="My name is Alex. What are human support hours?",
        dialogue=consented,
        state=state,
        initial_chunks=[
            RetrievedChunk(
                content="Human support hours are published in the app.",
                source_title="Support hours",
                language="en",
                score=1,
                document_key="support-hours",
                version=1,
            )
        ],
    )

    assert result.dialogue.pending_prompt.field == "email"


def test_ms_first_question_survives_irrelevant_initial_retrieval_and_resolves_search(
    sessions,
):
    with sessions.begin() as db:
        document = KnowledgeDocument(
            document_key="ride-booking-ms",
            version=1,
            title="Tempahan perjalanan",
            source_type="manual",
            source_uri="https://duducar.co/ms/bookings",
            language="ms",
            status="active",
            effective_at=datetime(2026, 1, 1),
            content_hash="ms-booking",
        )
        document.chunks.append(
            KnowledgeChunk(
                content="Penumpang boleh menempah perjalanan melalui aplikasi DUDU Car.",
                language="ms",
                tags=["tempah", "perjalanan"],
            )
        )
        db.add(document)
    history = [
        {"role": "user", "content": "Bagaimana saya tempah perjalanan?"},
        {
            "role": "assistant",
            "content": "Maklumat tempahan yang diluluskan. " * 11,
        },
        {"role": "user", "content": "Apakah had baucar?"},
        {
            "role": "assistant",
            "content": "Maklumat promosi dan baucar yang diluluskan. " * 9,
        },
        {"role": "user", "content": "Bagaimana saya log masuk?"},
        {
            "role": "assistant",
            "content": "Maklumat akaun dan log masuk yang diluluskan. " * 9,
        },
    ]
    irrelevant = [
        RetrievedChunk(
            content="Maklumat yang tidak berkaitan. " * 20,
            source_title=f"Tidak berkaitan {index}",
            language="ms",
            score=0.2,
            document_key=f"irrelevant-{index}",
            version=1,
        )
        for index in range(4)
    ]
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            payload = json.loads(body["messages"][-1]["content"])
            assert payload["language"] == "ms"
            assert payload["history"] == history
            assert payload["prior_user_questions"] == [
                item["content"] for item in history if item["role"] == "user"
            ]
            assert payload["initial_excerpts"] == []
            return httpx.Response(
                200,
                json=completion(
                    "search_knowledge", {"query": "cara tempah perjalanan"}
                ),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Penumpang boleh menempah perjalanan melalui aplikasi DUDU Car.",
                    "citation_ids": [1],
                    "next_prompt": None,
                },
                "call-2",
            ),
        )

    result = run(
        handler,
        sessions,
        text="Sila jawab soalan pertama saya semula.",
        language="ms",
        history=history,
        initial_chunks=irrelevant,
    )

    assert result.answer == "Penumpang boleh menempah perjalanan melalui aplikasi DUDU Car."
    assert result.source_titles == ["Tempahan perjalanan"]
    assert result.model_attempts == 2
    assert result.tool_calls == 1
    first_request_chars = sum(
        len(message.get("content") or "") for message in requests[0]["messages"]
    ) + len(json.dumps(requests[0]["tools"], separators=(",", ":")))
    assert first_request_chars <= 8000


def test_real_agent_returns_grounded_faq_and_filters_business_tools_by_state(sessions):
    requests = []
    chunk = RetrievedChunk(
        content="Human support replies within 24 hours.",
        source_title="Support times",
        language="en",
        score=1,
        document_key="support-times",
        version=2,
    )

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Human support replies within 24 hours.",
                    "citation_ids": [1],
                    "next_prompt": None,
                },
            ),
        )

    result = run(handler, sessions, initial_chunks=[chunk])

    business_tools = {"search_knowledge"}
    assert result.answer == chunk.content
    assert result.source_titles == ["Support times"]
    assert result.model_attempts == 1
    assert result.model_successes == 1
    assert business_tools == {
        tool["function"]["name"]
        for tool in requests[0]["tools"]
        if tool["function"]["name"] != "DialogueAnswer"
    }
    model_input_chars = sum(
        len(message.get("content") or "") for message in requests[0]["messages"]
    ) + len(json.dumps(requests[0]["tools"], separators=(",", ":")))
    assert model_input_chars <= 8000


def test_before_first_model_callback_runs_once_before_dispatch(sessions):
    events = []

    def handler(request):
        events.append("model")
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": "Please clarify.", "citation_ids": [], "next_prompt": None},
            ),
        )

    result = run(
        handler,
        sessions,
        before_first_model=lambda _deadline: (events.append("fence"), 30.0)[1],
    )

    assert events == ["fence", "model"]
    assert result.model_attempts == 1


def test_follow_up_search_uses_context_resolved_query_and_deduplicates(sessions):
    with sessions.begin() as db:
        document = KnowledgeDocument(
            document_key="refund-time",
            version=1,
            title="Refund timing",
            source_type="manual",
            source_uri="https://duducar.co/refunds",
            language="en",
            status="active",
            effective_at=datetime(2026, 1, 1),
            content_hash="x",
        )
        document.chunks.append(
            KnowledgeChunk(
                content="Refund reviews take 3 days.", language="en", tags=["refund"]
            )
        )
        db.add(document)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=multi_completion(
                    [
                        ("search_knowledge", {"query": "refund review timing"}),
                        ("search_knowledge", {"query": "refund review timing"}),
                    ]
                ),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Refund reviews take 3 days.",
                    "citation_ids": [1],
                    "next_prompt": None,
                },
                "call-2",
            ),
        )

    result = run(
        handler,
        sessions,
        text="How long does that take?",
        history=[
            {"role": "user", "content": "What happens with refund reviews?"},
            {"role": "assistant", "content": "Which part do you mean?"},
        ],
    )

    assert result.answer == "Refund reviews take 3 days."
    assert result.tool_calls == 2
    assert 'refund review timing' in json.dumps(requests[1]["messages"])
    first_search = json.loads(requests[1]["messages"][-2]["content"])
    repeated_search = json.loads(requests[1]["messages"][-1]["content"])
    assert first_search["excerpts"] == [
        {
            "id": 1,
            "document": "refund-time",
            "version": 1,
            "language": "en",
            "content": "Refund reviews take 3 days.",
        }
    ]
    assert repeated_search["excerpts"] == [{"id": 1, "already_supplied": True}]


def test_overlapping_and_repeated_searches_keep_one_grounded_excerpt(sessions):
    chunk = RetrievedChunk(
        content="Refund reviews take 3 days.",
        source_title="Refund timing",
        language="en",
        score=1,
        document_key="refund-time",
        version=1,
    )
    with sessions.begin() as db:
        document = KnowledgeDocument(
            document_key=chunk.document_key,
            version=chunk.version,
            title=chunk.source_title,
            source_type="manual",
            source_uri="https://duducar.co/refunds",
            language=chunk.language,
            status="active",
            effective_at=datetime(2026, 1, 1),
            content_hash="refund-time-v1",
        )
        document.chunks.append(KnowledgeChunk(content=chunk.content, language="en"))
        db.add(document)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) < 3:
            return httpx.Response(
                200,
                json=completion("search_knowledge", {"query": "refund timing"}, f"call-{len(requests)}"),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Refund reviews take 3 days.",
                    "citation_ids": [1],
                    "next_prompt": None,
                },
                "call-3",
            ),
        )

    result = run(handler, sessions, initial_chunks=[chunk])

    initial_payload = json.loads(requests[0]["messages"][-1]["content"])
    assert initial_payload["initial_excerpts"] == [
        {
            "id": 1,
            "document": "refund-time",
            "version": 1,
            "language": "en",
            "content": chunk.content,
        }
    ]
    assert json.loads(requests[1]["messages"][-1]["content"])["excerpts"] == [
        {"id": 1, "already_supplied": True}
    ]
    assert json.loads(requests[2]["messages"][-1]["content"])["excerpts"] == [
        {"id": 1, "already_supplied": True}
    ]
    assert result.answer == chunk.content
    assert result.citation_ids == [1]
    assert result.source_titles == [chunk.source_title]
    assert result.model_attempts == 3
    assert result.tool_calls == 2


def test_excerpt_reference_size_check_does_not_retain_undelivered_source(monkeypatch):
    existing = RetrievedChunk(
        content="Existing source.",
        source_title="Existing",
        language="en",
        score=1,
        document_key="existing",
        version=1,
    )
    new = RetrievedChunk(
        content="New source.",
        source_title="New",
        language="en",
        score=1,
        document_key="new",
        version=1,
    )
    runtime = SimpleNamespace(excerpts=[existing])
    full = {
        "id": 2,
        "document": "new",
        "version": 1,
        "language": "en",
        "content": new.content,
    }
    full_envelope = json.dumps(
        {"status": "prepared", "excerpts": [full]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    monkeypatch.setattr(dialogue_service, "MAX_TOOL_RESULT_CHARS", len(full_envelope))

    excerpts = dialogue_service._add_excerpts(runtime, [new, existing])

    assert excerpts == [full]
    assert runtime.excerpts == [existing, new]
    assert json.loads(dialogue_service._tool_result({"status": "prepared", "excerpts": excerpts})) == {
        "status": "prepared",
        "excerpts": [full],
    }


def test_trimming_duplicate_initial_reference_keeps_grounded_citation(
    sessions, monkeypatch
):
    chunk = RetrievedChunk(
        content="Refund reviews take 3 days.",
        source_title="Refund timing",
        language="en",
        score=1,
        document_key="refund-time",
        version=1,
    )
    requests = []
    overhead = iter((8000, 0))
    monkeypatch.setattr(dialogue_service, "_agent_overhead_chars", lambda: next(overhead, 0))

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": chunk.content, "citation_ids": [1], "next_prompt": None},
            ),
        )

    result = run(handler, sessions, initial_chunks=[chunk, chunk])

    payload = json.loads(requests[0]["messages"][-1]["content"])
    assert payload["initial_excerpts"] == [
        {
            "id": 1,
            "document": "refund-time",
            "version": 1,
            "language": "en",
            "content": chunk.content,
        }
    ]
    assert result.citation_ids == [1]
    assert result.source_titles == [chunk.source_title]


def test_combined_fields_update_once_and_agent_chooses_the_missing_field(sessions):
    requests = []
    selected = {}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            payload = json.loads(body["messages"][-1]["content"])
            selected.update(
                {
                    item["field"]: item["id"]
                    for item in payload["field_references"]
                    if item["source"] == "current_input"
                }
            )
            return httpx.Response(
                200,
                json=completion(
                    "update_ticket_draft",
                    {
                        "field_references": {
                            key: selected[key]
                            for key in ("name", "email", "description")
                        },
                        "issue_type": "complaint",
                    },
                ),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Which contact phone should support use?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "field", "field": "phone_number"},
                },
                "call-2",
            ),
        )

    result = run(
        handler,
        sessions,
        text="My name is Alex Tan; email alex@example.com; my receipt is missing",
    )

    assert result.dialogue.draft.fields.name == "Alex Tan"
    assert result.dialogue.draft.fields.email == "alex@example.com"
    assert result.dialogue.draft.fields.description.endswith("receipt is missing")
    assert result.dialogue.pending_prompt.field == "phone_number"
    assert result.answer == "Which contact phone should support use?"
    assert "alex@example.com" not in json.dumps(requests[0])
    assert "Alex Tan" not in json.dumps(requests[0])


def test_consent_and_channel_phone_are_applied_by_the_same_tool_call(sessions):
    dialogue = make_prompt(
        DialogueData(draft=new_draft(expiry_minutes=60)), "consent", "turn-1"
    )

    def handler(request):
        body = json.loads(request.content)
        if body["messages"][-1]["role"] == "user":
            payload = json.loads(body["messages"][-1]["content"])
            phone_ref = next(
                item["id"]
                for item in payload["field_references"]
                if item["field"] == "phone_number"
            )
            return httpx.Response(
                200,
                json=completion(
                    "update_ticket_draft",
                    {"field_references": {"phone_number": phone_ref}},
                ),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "What name should support use?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "field", "field": "name"},
                },
                "call-2",
            ),
        )

    result = run(
        handler,
        sessions,
        dialogue=dialogue,
        text="Yes",
        request_fields={"channel": "whatsapp", "phone_number": "+60123456789"},
    )

    assert result.dialogue.draft.consent is not None
    assert result.dialogue.draft.fields.phone_number == "+60123456789"
    assert result.dialogue.pending_prompt.field == "name"


def test_invented_field_reference_is_rejected_without_mutating_the_draft(sessions):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=completion(
                    "update_ticket_draft",
                    {"field_references": {"email": "invented"}},
                ),
            )
        assert (
            "unknown or mismatched customer field reference"
            in requests[-1]["messages"][-1]["content"]
        )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Which email address should support use?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "field", "field": "email"},
                },
                "call-2",
            ),
        )

    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    result = run(handler, sessions, dialogue=dialogue, text="Use my email")

    assert result.dialogue.draft.fields.email is None
    assert dialogue.pending_prompt is None


def test_repeated_parallel_draft_tools_are_serial_and_idempotent(sessions):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            payload = json.loads(body["messages"][-1]["content"])
            name_ref = next(
                item["id"]
                for item in payload["field_references"]
                if item["field"] == "name" and item["source"] == "current_input"
            )
            call = ("update_ticket_draft", {"field_references": {"name": name_ref}})
            return httpx.Response(200, json=multi_completion([call, call]))
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "What happened?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "field", "field": "description"},
                },
                "call-2",
            ),
        )

    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    initial_version = dialogue.draft.version
    result = run(
        handler,
        sessions,
        dialogue=dialogue,
        text="My name is Alex Tan",
        max_input_chars=Settings(_env_file=None).llm_max_input_chars,
    )

    assert result.tool_calls == 2
    assert result.dialogue.draft.version == initial_version + 1
    assert result.dialogue.draft.fields.name == "Alex Tan"


def test_followup_input_cap_trims_only_history_and_keeps_tool_pairs(sessions):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert len(json.dumps(
            {"messages": body["messages"], "tools": body["tools"]},
            ensure_ascii=False, separators=(",", ":"),
        )) <= 8000
        answer = (
            {"answer": "Please clarify. " * 60, "next_prompt": {"purpose": "none"}}
            if len(requests) == 1
            else {"answer": "Please clarify.", "next_prompt": None}
        )
        return httpx.Response(200, json=completion("DialogueAnswer", answer))

    result = run(handler, sessions, history=[
        {"role": "user", "content": f"Earlier topic {index}: " + "a" * 400}
        for index in range(4)
    ])

    first = json.loads(requests[0]["messages"][1]["content"])
    second = json.loads(requests[1]["messages"][1]["content"])
    assert len(second.pop("history")) <= len(first.pop("history"))
    assert first == second
    assert requests[1]["messages"][-1]["tool_call_id"] == "call-1"
    assert requests[1]["messages"][-2]["tool_calls"][0]["id"] == "call-1"
    assert result.model_attempts == 2


def test_irreducible_followup_input_is_blocked_before_next_provider_request(sessions):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completion("DialogueAnswer", {
            "answer": "Please clarify.", "unexpected": "x" * 4000,
        }))

    with pytest.raises(ValueError, match="agent_input_limit"):
        run(handler, sessions)
    assert calls == 1


def test_input_budget_fits_at_15000_after_history_compaction(sessions):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        wire_size = len(json.dumps(
            {"messages": body["messages"], "tools": body["tools"]},
            ensure_ascii=False,
            separators=(",", ":"),
        ))
        payload = json.loads(body["messages"][-1]["content"])
        assert wire_size <= 15000
        assert payload["history"] == []
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": "Please clarify.", "citation_ids": [], "next_prompt": None},
            ),
        )

    result = run(
        handler,
        sessions,
        history=[{"role": "user", "content": "Earlier " + "x" * 3000}],
        state={"draft": None, "padding": "x" * 8900},
        max_input_chars=15000,
    )

    assert result.answer == "Please clarify."
    assert len(requests) == 1


def test_input_budget_rejects_over_15000_after_history_compaction(sessions):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": "Please clarify.", "citation_ids": [], "next_prompt": None},
            ),
        )

    with pytest.raises(ValueError, match="agent_input_limit"):
        run(
            handler,
            sessions,
            history=[{"role": "user", "content": "Earlier " + "x" * 3000}],
            state={"draft": None, "padding": "x" * 10000},
            max_input_chars=15000,
        )

    assert calls == 0


def test_rejected_answer_is_compacted_for_bounded_native_retry(sessions, monkeypatch):
    requests = []
    rejected = {
        "answer": "Unsupported explanation. " * 300,
        "citation_ids": [1],
        "next_prompt": {"purpose": "none", "unknown_field": "preserve"},
    }
    chunk = RetrievedChunk(
        content="Approved support excerpt.",
        source_title="Support",
        language="en",
        score=1,
    )

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200, json=completion("DialogueAnswer", rejected, "rejected-call")
            )
        assert len(json.dumps(
            {"messages": body["messages"], "tools": body["tools"]},
            ensure_ascii=False,
            separators=(",", ":"),
        )) <= 8000
        payload = json.loads(body["messages"][1]["content"])
        assert payload["current_question"] == "How long does support take?"
        assert payload["initial_excerpts"][0]["content"] == chunk.content
        assistant, repair = body["messages"][-2:]
        assert "at most 4 IDs" in body["messages"][0]["content"]
        assert "at most 4 IDs" in repair["content"]
        compacted = json.loads(assistant["tool_calls"][0]["function"]["arguments"])
        assert compacted == {
            **rejected,
            "answer": "[rejected answer omitted to fit retry]",
        }
        assert assistant["tool_calls"][0]["id"] == repair["tool_call_id"] == "rejected-call"
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": "Please clarify.", "citation_ids": [], "next_prompt": None},
                "call-2",
            ),
        )

    compactor = dialogue_service.DialogueLimits._compact_rejected_answers
    monkeypatch.setattr(
        dialogue_service.DialogueLimits,
        "_compact_rejected_answers",
        staticmethod(lambda _messages: False),
    )
    with pytest.raises(ValueError, match="agent_input_limit"):
        run(handler, sessions, initial_chunks=[chunk])
    assert len(requests) == 1

    requests.clear()
    monkeypatch.setattr(
        dialogue_service.DialogueLimits,
        "_compact_rejected_answers",
        staticmethod(compactor),
    )
    result = run(handler, sessions, initial_chunks=[chunk])

    assert result.answer == "Please clarify."
    assert len(requests) == result.model_attempts == 2


def test_plain_model_output_retries_as_structured_output(sessions):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=plain_completion())
        body = json.loads(request.content)
        assert body["messages"][-1]["role"] == "user"
        assert "DialogueAnswer" in body["messages"][-1]["content"]
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": "Please clarify.", "citation_ids": [], "next_prompt": None},
                "call-2",
            ),
        )

    result = run(handler, sessions)

    assert result.answer == "Please clarify."
    assert calls == result.model_attempts == 2


def test_repeated_plain_model_output_stops_at_five_actual_requests(sessions):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=plain_completion())

    with pytest.raises(Exception):
        run(handler, sessions, max_input_chars=12000)

    assert calls == 5


def test_bad_structured_schema_is_repaired_within_the_five_call_budget(
    sessions, caplog
):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        arguments = (
            {
                "answer": "Invalid",
                "citation_ids": [],
                "next_prompt": {"purpose": "none", "field": None},
            }
            if calls == 1
            else {"answer": "Please clarify.", "citation_ids": [], "next_prompt": None}
        )
        return httpx.Response(
            200,
            json=completion("DialogueAnswer", arguments, f"call-{calls}"),
        )

    result = run(handler, sessions)

    assert result.answer == "Please clarify."
    assert result.model_attempts == 2
    assert "structured_output_next_prompt_purpose_literal_error" in caplog.text


def test_schema_repair_telemetry_does_not_log_unknown_field_names(caplog):
    secret = "emailsecret@example.com"
    with pytest.raises(Exception) as caught:
        dialogue_service.DialogueAnswer.model_validate(
            {
                "answer": "Please clarify.",
                "citation_ids": [],
                "next_prompt": None,
                secret: "private",
            }
        )

    repair = dialogue_service._schema_repair_message(caught.value)

    assert secret not in caplog.text
    assert secret not in repair
    assert "structured_output_unknown_field_extra_forbidden" in caplog.text


def test_schema_repair_does_not_expose_unallowlisted_value_error(caplog):
    secret = "private answer and tool arguments"

    class MaliciousAnswer(dialogue_service.BaseModel):
        @dialogue_service.model_validator(mode="after")
        def reject(self):
            raise ValueError(secret)

    with pytest.raises(dialogue_service.ValidationError) as caught:
        MaliciousAnswer.model_validate({})
    repair = dialogue_service._schema_repair_message(caught.value)

    assert secret not in caplog.text
    assert secret not in repair


@pytest.mark.parametrize(
    "answer,citation_ids,repair_code,initial_chunks",
    [
        ("Unsupported fact", [1], "grounding_rejected", None),
        (
            "Our team is available 9:00 AM–6:00 PM daily. Could you share your name?",
            [],
            "unsafe_ungrounded_output",
            None,
        ),
        (
            "Fare estimates can change. [999]",
            [1],
            "grounding_rejected",
            [RetrievedChunk(content="Fare estimates can change.", source_title="Fares", language="en", score=1)],
        ),
    ],
)
def test_grounding_repair_explains_citation_and_numeric_constraints(
    sessions, answer, citation_ids, repair_code, initial_chunks
):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json=completion(
                    "DialogueAnswer",
                    {"answer": answer, "citation_ids": citation_ids, "next_prompt": None},
                ),
            )
        body = json.loads(request.content)
        repair = body["messages"][-1]["content"]
        assert repair_code in repair
        if repair_code == "grounding_rejected":
            assert "actual facts in supplied cited excerpts" in repair
            assert "numbered lists" in repair
            assert "extra numeric claims" in repair
            assert "qualifiers absent" in repair
            assert "IDs only in citation_ids" in repair
            assert "inline citation markers" in repair
        else:
            assert "support hours" in repair
            assert "excerpt IDs in citation_ids" in repair
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": "Please clarify.", "citation_ids": [], "next_prompt": None},
                "call-2",
            ),
        )

    result = run(handler, sessions, initial_chunks=initial_chunks)

    assert result.answer == "Please clarify."
    assert calls == result.model_attempts == 2


def test_declared_citation_marker_is_rendered_cleanly_without_a_repair(sessions):
    def handler(request):
        return httpx.Response(
            200,
            json=completion("DialogueAnswer", {
                "answer": "Fare estimates can change. [1]",
                "citation_ids": [1],
                "next_prompt": None,
            }),
        )

    result = run(handler, sessions, initial_chunks=[RetrievedChunk(
        content="Fare estimates can change.", source_title="Fares", language="en", score=1,
    )])

    assert result.answer == "Fare estimates can change."
    assert result.model_attempts == 1
    assert result.source_titles == ["Fares"]
    assert result.citation_ids == [1]


def test_combined_prompt_and_grounding_failures_repair_in_one_retry(sessions, caplog):
    calls = 0
    chunks = [
        RetrievedChunk(
            content="Keputusan bayaran balik perlu disemak oleh pegawai.",
            source_title="Fares and payments",
            language="ms",
            score=1,
        )
    ]

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json=completion("search_knowledge", {"query": "bayaran balik"}),
            )
        if calls == 2:
            return httpx.Response(
                200,
                json=completion(
                    "DialogueAnswer",
                    {
                        "answer": "Semua keputusan bayaran balik perlu disemak oleh pegawai.",
                        "citation_ids": [1],
                        "next_prompt": {"purpose": "field", "field": "trip_id"},
                    },
                    "call-2",
                ),
            )
        repair = json.loads(request.content)["messages"][-1]["content"]
        assert "next_prompt_not_allowed" in repair
        assert "Current allowed next_prompt values: [null]" in repair
        assert "grounding_rejected" in repair
        assert "qualifiers absent from excerpts" in repair
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Keputusan bayaran balik perlu disemak oleh pegawai.",
                    "citation_ids": [1],
                    "next_prompt": None,
                },
                "call-3",
            ),
        )

    result = run(handler, sessions, language="ms", initial_chunks=chunks)

    assert result.answer == "Keputusan bayaran balik perlu disemak oleh pegawai."
    assert calls == result.model_attempts == 3
    assert "next_prompt_not_allowed" in caplog.text
    assert "grounding_rejected" in caplog.text


def test_repeated_bad_structured_schema_stops_at_five_model_calls(sessions):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Invalid",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "none", "field": None},
                },
                f"call-{calls}",
            ),
        )

    with pytest.raises(Exception):
        run(handler, sessions, max_input_chars=12000)
    assert calls == 5


@pytest.mark.parametrize("purpose", ["field", "details"])
def test_prompt_outside_the_allowed_state_is_repaired_before_agent_exit(sessions, purpose):
    dialogue = make_prompt(
        DialogueData(draft=new_draft(expiry_minutes=60)), "consent", "turn-0"
    )
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        next_prompt = (
            {
                "purpose": purpose,
                "field": "name" if purpose == "field" else None,
            }
            if calls == 1
            else {"purpose": "consent", "field": None}
        )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "What name should support use?",
                    "citation_ids": [],
                    "next_prompt": next_prompt,
                },
                f"call-{calls}",
            ),
        )

    result = run(handler, sessions, dialogue=dialogue)

    assert calls == result.model_attempts == 2
    assert result.dialogue.pending_prompt.purpose == "consent"


def test_repeated_prompt_outside_the_allowed_state_stops_at_five_calls(sessions):
    dialogue = make_prompt(
        DialogueData(draft=new_draft(expiry_minutes=60)), "consent", "turn-0"
    )
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "What name should support use?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "details", "field": None},
                },
                f"call-{calls}",
            ),
        )

    with pytest.raises(Exception):
        run(handler, sessions, dialogue=dialogue, max_input_chars=12000)
    assert calls == 5


def test_persisted_case_confirmation_without_current_operation_repairs_to_null(sessions):
    dialogue = DialogueData.model_validate(
        {
            "pending_prompt": {
                "id": "prompt-1",
                "purpose": "case_confirmation",
                "case_id": "case-1",
                "version": 1,
                "operation_id": "operation-1",
                "originating_turn": "prior-turn",
            },
            "pending_case_update": {
                "operation_id": "operation-1",
                "case_id": "case-1",
                "case_reference": "DUDU-20260914-ABCDE",
                "case_version": 1,
                "update": "Synthetic update",
                "originating_turn": "prior-turn",
            },
        }
    )
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            body = json.loads(request.content)
            assert "Current allowed next_prompt values: [null]" in body["messages"][-1][
                "content"
            ]
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Please clarify.",
                    "citation_ids": [],
                    "next_prompt": (
                        {"purpose": "case_confirmation", "field": None}
                        if calls == 1
                        else None
                    ),
                },
                f"call-{calls}",
            ),
        )

    result = run(handler, sessions, dialogue=dialogue)

    assert calls == result.model_attempts == 2
    assert result.dialogue.pending_case_update.operation_id == "operation-1"
    assert result.dialogue.pending_prompt.purpose == "case_confirmation"


def test_optional_offer_remains_allowed_for_a_current_issue_reference(sessions):
    def handler(request):
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Would you like human review?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "offer", "field": None},
                },
            ),
        )

    result = run(handler, sessions, text="My voucher did not work")

    assert result.dialogue.pending_offer is not None
    assert result.dialogue.pending_prompt.purpose == "offer"


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "I am safe, I am just reading the safety guide."),
        ("ms", "Saya selamat, saya hanya membaca panduan keselamatan."),
        ("zh", "我没有危险，只是在阅读安全指南。"),
    ],
)
def test_factual_negative_offer_is_repaired_to_null_in_final_output(
    sessions, language, text
):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "I can arrange human support.",
                    "citation_ids": [],
                    "next_prompt": (
                        {"purpose": "offer", "field": None}
                        if calls == 1
                        else None
                    ),
                },
                f"call-{calls}",
            ),
        )

    result = run(handler, sessions, text=text, language=language)

    assert calls == result.model_attempts == 2
    assert result.dialogue.draft is None
    assert result.dialogue.pending_offer is None
    assert result.dialogue.pending_prompt is None


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "I am in danger."),
        ("ms", "Saya mahu pegawai manusia."),
        ("zh", "我要人工客服处理收据问题"),
    ],
)
def test_positive_issue_offer_remains_allowed_in_final_output(sessions, language, text):
    def handler(request):
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Would you like human review?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "offer", "field": None},
                },
            ),
        )

    result = run(handler, sessions, text=text, language=language)

    assert result.model_attempts == 1
    assert result.dialogue.pending_offer is not None
    assert result.dialogue.pending_prompt.purpose == "offer"


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "I am safe, I am just reading the safety guide."),
        ("ms", "Saya selamat, saya hanya membaca panduan keselamatan."),
        ("zh", "我没有危险，只是在阅读安全指南。"),
    ],
)
def test_factual_negative_offer_is_rejected_after_update_tool(
    sessions, language, text
):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if calls == 1:
            payload = json.loads(body["messages"][-1]["content"])
            reference = next(
                item["id"]
                for item in payload["field_references"]
                if item["field"] == "description" and item["source"] == "current_input"
            )
            return httpx.Response(
                200,
                json=completion(
                    "update_ticket_draft",
                    {"field_references": {"description": reference}},
                ),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Would you like human review?",
                    "citation_ids": [],
                    "next_prompt": (
                        {"purpose": "offer", "field": None}
                        if calls == 2
                        else None
                    ),
                },
                f"call-{calls}",
            ),
        )

    result = run(handler, sessions, text=text, language=language)

    assert calls == result.model_attempts == 3
    assert result.tool_calls == 1
    assert result.dialogue.draft is None
    assert result.dialogue.pending_offer is None


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "I have a missing receipt."),
        ("ms", "Saya mahu buat aduan tentang resit hilang."),
        ("zh", "我的收据不见了。"),
    ],
)
def test_positive_issue_update_tool_starts_consent_flow(sessions, language, text):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if calls == 1:
            payload = json.loads(body["messages"][-1]["content"])
            reference = next(
                item["id"]
                for item in payload["field_references"]
                if item["field"] == "description" and item["source"] == "current_input"
            )
            return httpx.Response(
                200,
                json=completion(
                    "update_ticket_draft",
                    {"field_references": {"description": reference}},
                ),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "May I collect your contact details?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "consent", "field": None},
                },
                f"call-{calls}",
            ),
        )

    result = run(handler, sessions, text=text, language=language)

    assert calls == result.model_attempts == 2
    assert result.tool_calls == 1
    assert result.dialogue.draft is not None
    assert result.dialogue.pending_prompt.purpose == "consent"


def test_new_draft_tool_keeps_an_actual_human_question_recordable(sessions):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if calls == 1:
            payload = json.loads(body["messages"][-1]["content"])
            description_reference = next(
                item["id"]
                for item in payload["field_references"]
                if item["field"] == "description" and item["source"] == "current_input"
            )
            return httpx.Response(
                200,
                json=completion(
                    "update_ticket_draft",
                    {"field_references": {"description": description_reference}},
                ),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "I can arrange human support.",
                    "citation_ids": [],
                    "next_prompt": None,
                },
                "call-2",
            ),
        )

    result = run(
        handler,
        sessions,
        text="Can I speak to a human?",
    )

    assert calls == result.model_attempts == 2
    assert result.dialogue.draft is not None
    assert result.dialogue.draft.fields.description == "Can I speak to a human?"


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("ms", "Berhenti dulu."),
        ("ms", "Jangan padam akaun saya. Saya cuma perlukan panduan log masuk."),
        ("en", "I do not want human support"),
        ("ms", "Saya tidak mahu pegawai"),
        ("zh", "我不需要人工"),
    ],
)
def test_new_draft_tool_rejects_a_control_without_starting_a_draft(sessions, language, text):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if calls == 1:
            payload = json.loads(body["messages"][-1]["content"])
            fields = {
                item["field"]: item["id"]
                for item in payload["field_references"]
                if item["field"] == "description" and item["source"] == "current_input"
            }
            return httpx.Response(
                200,
                json=completion(
                    "update_ticket_draft",
                    {"field_references": fields},
                ),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Understood.",
                    "citation_ids": [],
                    "next_prompt": None,
                },
                "call-2",
            ),
        )

    result = run(
        handler,
        sessions,
        text=text,
        language=language,
    )

    assert calls == result.model_attempts == 2
    assert result.dialogue.draft is None
    assert result.dialogue.pending_offer is None


@pytest.mark.parametrize("status", ["cancelled", "submitted"])
def test_new_draft_control_guard_covers_terminal_drafts(sessions, status):
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    dialogue.draft.status = status
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if calls == 1:
            payload = json.loads(body["messages"][-1]["content"])
            description_reference = next(
                item["id"]
                for item in payload["field_references"]
                if item["field"] == "description" and item["source"] == "current_input"
            )
            return httpx.Response(
                200,
                json=completion(
                    "update_ticket_draft",
                    {"field_references": {"description": description_reference}},
                ),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": "Understood.", "citation_ids": [], "next_prompt": None},
                "call-2",
            ),
        )

    result = run(
        handler,
        sessions,
        dialogue=dialogue,
        text="I do not want a refund",
    )

    assert calls == result.model_attempts == 2
    assert result.dialogue.draft.status == status
    assert result.dialogue.draft.fields.description is None


def test_question_only_offer_repairs_to_null_without_mutation(sessions):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            body = json.loads(request.content)
            assert "Current allowed next_prompt values: [null]" in body["messages"][-1][
                "content"
            ]
            return httpx.Response(
                200,
                json=completion(
                    "DialogueAnswer",
                    {
                        "answer": "I can help with support questions.",
                        "citation_ids": [],
                        "next_prompt": None,
                    },
                    "call-2",
                ),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Would you like human review?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "offer", "field": None},
                },
            ),
        )

    result = run(
        handler,
        sessions,
        text="这个机器人能签署合作协议吗？",
        language="zh",
    )

    assert calls == result.model_attempts == 2
    assert result.dialogue.draft is None
    assert result.dialogue.pending_offer is None
    assert result.dialogue.pending_prompt is None


@pytest.mark.parametrize(
    ("language", "text", "active"),
    [
        ("ms", "Perkataan penipuan ada dalam amaran; adakah panduan?", False),
        ("en", "What are human support hours?", True),
    ],
)
def test_general_question_cannot_become_a_ticket_issue(sessions, language, text, active):
    dialogue = DialogueData()
    if active:
        dialogue.draft = new_draft(expiry_minutes=60)
        dialogue = make_prompt(dialogue, "consent", "consent-turn")
        prompt = dialogue.pending_prompt
        dialogue = record_consent(
            dialogue,
            prompt_id=prompt.id,
            originating_turn=prompt.originating_turn,
            customer_input="Yes",
            language="en",
        ).dialogue
        dialogue.draft.fields.name = "Alex"
        dialogue.draft.fields.email = "alex@example.com"
        dialogue.draft.fields.phone_number = "+60123456789"
        dialogue = make_prompt(dialogue, "field", "issue-turn", field="description")
    before = dialogue.model_dump()
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if calls == 1:
            payload = json.loads(body["messages"][-1]["content"])
            reference = next(
                item["id"] for item in payload["field_references"]
                if item["field"] == "description" and item["source"] == "current_input"
            )
            return httpx.Response(200, json=completion("update_ticket_draft", {
                "field_references": {"description": reference},
                "issue_type": "fraud", "priority": "high",
            }))
        assert json.loads(body["messages"][-1]["content"])["status"] == "rejected"
        return httpx.Response(200, json=completion("DialogueAnswer", {
            "answer": "Please clarify.", "citation_ids": [], "next_prompt": None,
        }, "call-2"))

    result = run(handler, sessions, text=text, language=language, dialogue=dialogue,
                 request_fields={"channel": "whatsapp"})

    assert calls == result.model_attempts == 2
    assert result.dialogue.model_dump() == before


def test_ungrounded_number_is_repaired_and_input_over_budget_is_rejected(sessions):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": (
                        "Support replies in 7 days." if calls == 1 else "Please clarify."
                    ),
                    "citation_ids": [],
                    "next_prompt": None,
                },
                f"call-{calls}",
            ),
        )

    result = run(handler, sessions)

    assert calls == result.model_attempts == 2
    assert result.answer == "Please clarify."

    with pytest.raises(ValueError, match="invalid_dialogue_context|agent_input_limit"):
        run(
            handler,
            sessions,
            text=("What is the support policy? " * 200)[:4096],
            history=[{"role": "user", "content": "y" * 4000}],
        )


def test_consent_prompt_is_local_and_metadata_matches_text_sent(sessions):
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))

    def handler(request):
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "I created your ticket with 24/7 support.",
                    "citation_ids": [999],
                    "next_prompt": {"purpose": "consent", "field": None},
                },
            ),
        )

    result = run(handler, sessions, dialogue=dialogue, text="I need a human")

    assert result.dialogue.pending_prompt.purpose == "consent"
    assert result.dialogue.pending_prompt.originating_turn == "turn-1"
    assert "Privacy Notice" in result.answer
    assert "created your ticket" not in result.answer
    assert result.citation_ids == []


def test_side_question_preserves_paused_draft_and_explicit_resume_uses_shared_validator(sessions):
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    dialogue.draft.status = "paused"
    chunk = RetrievedChunk(
        content="Support can review receipt questions.",
        source_title="Receipts",
        language="en",
        score=1,
        document_key="receipts",
        version=1,
    )

    def side_question(request):
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": chunk.content,
                    "citation_ids": [1],
                    "next_prompt": None,
                },
            ),
        )

    side = run(
        side_question,
        sessions,
        dialogue=dialogue,
        text="Can support review receipt questions?",
        initial_chunks=[chunk],
    )
    assert side.dialogue.draft.status == "paused"

    calls = 0

    def resume(request):
        nonlocal calls
        calls += 1
        response = (
            completion("set_draft_status", {"status": "active"})
            if calls == 1
            else completion(
                "DialogueAnswer",
                {
                    "answer": "What happened?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "field", "field": "description"},
                },
                "call-2",
            )
        )
        return httpx.Response(200, json=response)

    resumed = run(resume, sessions, dialogue=side.dialogue, text="Continue")
    assert resumed.dialogue.draft.status == "active"


def test_correction_marks_review_required_and_invented_citation_is_rejected(sessions):
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    dialogue.draft.fields.email = "old@example.com"
    requests = []

    def correct(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            payload = json.loads(body["messages"][-1]["content"])
            reference = next(
                item["id"]
                for item in payload["field_references"]
                if item["field"] == "email" and item["source"] == "current_input"
            )
            return httpx.Response(
                200,
                json=completion(
                    "update_ticket_draft",
                    {"field_references": {"email": reference}},
                ),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "What happened?",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "field", "field": "description"},
                },
                "call-2",
            ),
        )

    corrected = run(
        correct,
        sessions,
        dialogue=dialogue,
        text="Use corrected@example.com",
    )
    assert corrected.dialogue.draft.fields.email == "corrected@example.com"
    assert corrected.dialogue.draft.review_required is True

    invented_calls = 0

    def invented_source(request):
        nonlocal invented_calls
        invented_calls += 1
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "This claim has no supplied source.",
                    "citation_ids": [2],
                    "next_prompt": None,
                },
            ),
        )

    with pytest.raises(Exception):
        run(invented_source, sessions, initial_chunks=[], max_input_chars=12000)
    assert invented_calls == 5


def test_case_tools_reject_unknown_reference_and_conflicting_update(sessions):
    with sessions.begin() as db:
        db.add(
            Ticket(
                id="case-1",
                public_id="DUDU-20260914-ABCDE",
                status="closed",
                urgency="normal",
                channel="web",
                external_user_id="customer-1",
                name="Alex",
                email="alex@example.com",
                phone_number="+60123456789",
                issue_type="complaint",
                language="en",
                description="Missing receipt",
                consent_given=True,
                extra={"customer_updates": []},
            )
        )
    requests = []
    case_reference = None
    refs = {}

    def handler(request):
        nonlocal case_reference
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            payload = json.loads(body["messages"][-1]["content"])
            refs.update(
                {
                    item["field"]: item["id"]
                    for item in payload["field_references"]
                    if item["source"] == "current_input"
                }
            )
            return httpx.Response(
                200,
                json=completion(
                    "get_owned_case", {"public_id": "DUDU-20260914-ABCDE"}
                ),
            )
        if len(requests) == 2:
            tool_result = json.loads(body["messages"][-1]["content"])
            case_reference = tool_result["case_reference"]
            calls = [
                (
                    "request_case_update",
                    {
                        "case_reference": case_reference,
                        "update_reference": refs["description"],
                    },
                ),
                (
                    "request_case_update",
                    {
                        "case_reference": case_reference,
                        "update_reference": refs["ride_details"],
                    },
                ),
            ]
            return httpx.Response(200, json=multi_completion(calls, "updates"))
        assert "conflicting_case_update" in json.dumps(body["messages"])
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Please confirm.",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "case_confirmation", "field": None},
                },
                "call-3",
            ),
        )

    result = run(
        handler,
        sessions,
        text="Please add that my receipt is missing",
        request_fields={"ride_details": "Trip was yesterday"},
    )

    assert case_reference
    assert result.case_update.update == "Please add that my receipt is missing"
    assert result.dialogue.pending_case_update.operation_id == result.case_update.operation_id
    assert "DUDU-20260914-ABCDE" in result.answer


def test_invented_case_reference_cannot_stage_an_update(sessions):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            payload = json.loads(body["messages"][-1]["content"])
            update_reference = next(
                item["id"]
                for item in payload["field_references"]
                if item["field"] == "description"
            )
            return httpx.Response(
                200,
                json=completion(
                    "request_case_update",
                    {
                        "case_reference": "invented-case",
                        "update_reference": update_reference,
                    },
                ),
            )
        assert "unknown_case_reference" in body["messages"][-1]["content"]
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": "Which case do you mean?", "citation_ids": []},
                "call-2",
            ),
        )

    result = run(handler, sessions, text="Please add this missing receipt update")

    assert result.case_update is None
    assert result.dialogue.pending_case_update is None


@pytest.mark.parametrize("repair_after_timeout", [False, True])
def test_timeout_retry_counts_actual_requests_across_schema_repairs(
    sessions, repair_after_timeout, monkeypatch
):
    calls = 0
    usage = dialogue_service.AgentUsage()
    monkeypatch.setattr(dialogue_service, "AgentUsage", lambda: usage)

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1 or (repair_after_timeout and calls == 3):
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        invalid = repair_after_timeout and calls in {2, 4}
        arguments = (
            {
                "answer": "Please clarify.",
                "citation_ids": [],
                "next_prompt": {"purpose": "none", "field": None},
            }
            if invalid
            else {"answer": "Please clarify.", "citation_ids": [], "next_prompt": None}
        )
        return httpx.Response(
            200, json=completion("DialogueAnswer", arguments, f"call-{calls}")
        )

    result = run(handler, sessions, max_input_chars=12000)
    assert calls == result.model_attempts == (5 if repair_after_timeout else 2)
    assert result.answer == "Please clarify."
    assert usage.model_timeouts == (2 if repair_after_timeout else 1)


def test_provider_request_limit_blocks_sixth_after_repairs_and_timeout_retries(
    sessions, monkeypatch
):
    calls = 0
    usage = dialogue_service.AgentUsage()
    monkeypatch.setattr(dialogue_service, "AgentUsage", lambda: usage)

    def handler(request):
        nonlocal calls
        calls += 1
        if calls in {1, 3, 5}:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {
                    "answer": "Please clarify.",
                    "citation_ids": [],
                    "next_prompt": {"purpose": "none", "field": None},
                },
                f"call-{calls}",
            ),
        )

    with pytest.raises(ValueError, match="provider_request_limit"):
        run(handler, sessions, max_input_chars=12000)

    assert calls == 5
    assert usage.model_attempts == 5
    assert usage.model_timeouts == 3


def test_remaining_deadline_caps_each_provider_timeout(sessions, monkeypatch):
    clock = [0.0]
    timeouts = []
    monkeypatch.setattr(dialogue_service.time, "monotonic", lambda: clock[0])

    def handler(request):
        timeouts.append(request.extensions["timeout"]["read"])
        if len(timeouts) == 1:
            clock[0] = 45.0
            return httpx.Response(
                200,
                json=completion("search_knowledge", {"query": "support"}),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": "Please clarify.", "citation_ids": [], "next_prompt": None},
                "call-2",
            ),
        )

    result = run(handler, sessions, max_input_chars=12000)

    assert result.answer == "Please clarify."
    assert timeouts == [30.0, 15.0]


def test_timeout_retry_rechecks_remaining_deadline(sessions, monkeypatch):
    clock = [0.0]
    calls = 0
    monkeypatch.setattr(dialogue_service.time, "monotonic", lambda: clock[0])

    def handler(request):
        nonlocal calls
        calls += 1
        clock[0] = dialogue_service.MAX_AGENT_SECONDS + 1
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with pytest.raises(TimeoutError, match="agent_deadline_exceeded"):
        run(handler, sessions)
    assert calls == 1


def test_tool_budget_and_provider_timeout_stop_without_mutating_input(
    sessions, monkeypatch
):
    def too_many_tools(request):
        calls = [("search_knowledge", {"query": f"query {index}"}) for index in range(11)]
        return httpx.Response(200, json=multi_completion(calls))

    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    with pytest.raises(Exception):
        run(too_many_tools, sessions, dialogue=dialogue, max_input_chars=15000)
    assert dialogue.draft.fields.model_dump(exclude_none=True) == {}

    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with pytest.raises(Exception):
        run(timeout, sessions, dialogue=dialogue)
    assert dialogue.draft.fields.model_dump(exclude_none=True) == {}

    monkeypatch.setattr(dialogue_service, "MAX_AGENT_SECONDS", 0.001)

    def slow_response(request):
        time.sleep(0.01)
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": "Still here.", "citation_ids": [], "next_prompt": None},
            ),
        )

    with pytest.raises(TimeoutError, match="agent_deadline_exceeded"):
        run(slow_response, sessions, dialogue=dialogue)


def test_ten_total_tool_calls_allow_repeated_business_calls_and_output(sessions):
    requests = []
    tool_calls = [("search_knowledge", {"query": "support"}) for _ in range(9)]
    tool_calls.append(
        (
            "DialogueAnswer",
            {"answer": "Please clarify.", "citation_ids": [], "next_prompt": None},
        )
    )

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=multi_completion(tool_calls),
            )
        return httpx.Response(
            200,
            json=completion(
                "DialogueAnswer",
                {"answer": "Please clarify.", "citation_ids": [], "next_prompt": None},
                "final",
            ),
        )

    result = run(handler, sessions, max_input_chars=15000)

    assert len(tool_calls) == 10
    assert len(requests) == result.model_attempts == 1
    assert result.tool_calls == 9


def test_eleventh_business_tool_call_is_blocked(sessions):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=multi_completion(
                [("search_knowledge", {"query": "support"}) for _ in range(11)]
            ),
        )

    with pytest.raises(Exception, match=r"run limit exceeded \(11/10 calls\)"):
        run(handler, sessions, max_input_chars=15000)

    assert len(requests) == 1


def test_provider_failure_after_staged_draft_operation_discards_working_copy(sessions):
    calls = 0

    def stage_then_fail(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
            description = next(
                item["id"]
                for item in payload["field_references"]
                if item["field"] == "description"
            )
            return httpx.Response(
                200,
                json=completion(
                    "update_ticket_draft",
                    {
                        "field_references": {"description": description},
                        "issue_type": "complaint",
                    },
                ),
            )
        raise httpx.ReadTimeout("synthetic failure after staging", request=request)

    dialogue = DialogueData()
    with pytest.raises(Exception):
        run(
            stage_then_fail,
            sessions,
            dialogue=dialogue,
            text="The driver was rude and I want support",
        )
    assert calls == 3
    assert dialogue.draft is None


def test_native_final_repairs_missing_field_update_and_consumed_prompt(sessions):
    dialogue = make_prompt(
        DialogueData(draft=new_draft(expiry_minutes=60)), "consent", "consent-turn"
    )
    dialogue = record_consent(
        dialogue, prompt_id=dialogue.pending_prompt.id, originating_turn="consent-turn",
        customer_input="Yes", language="en",
    ).dialogue
    dialogue.draft.fields.name = "Alex"
    dialogue.draft.fields.email = "alex@example.com"
    dialogue.draft.fields.phone_number = "+60123456789"
    dialogue = make_prompt(dialogue, "field", "description-turn", field="description")
    calls = []
    description_reference = None

    def handler(request):
        nonlocal description_reference
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            payload = json.loads(body["messages"][-1]["content"])
            description_reference = next(
                item["id"] for item in payload["field_references"]
                if item["field"] == "description" and item["source"] == "current_input"
            )
        if len(calls) == 2:
            assert "current_fields_not_applied" in body["messages"][-1]["content"]
            return httpx.Response(200, json=completion(
                "update_ticket_draft", {"field_references": {"description": description_reference}},
                "apply-description",
            ))
        if len(calls) == 4:
            assert "next_prompt_required_after_field_progress" in body["messages"][-1]["content"]
        return httpx.Response(200, json=completion(
            "DialogueAnswer",
            {
                "answer": "Would you like to add ride details?",
                "citation_ids": [],
                "next_prompt": {"purpose": "details", "field": None} if len(calls) == 4 else None,
            },
            f"final-{len(calls)}",
        ))

    result = run(
        handler, sessions, dialogue=dialogue, text="My ride receipt is missing",
        request_fields={"prompt_id": dialogue.pending_prompt.id}, max_input_chars=15000,
    )
    assert result.model_attempts == result.model_successes == 4
    assert result.tool_calls == 1
    assert result.dialogue.draft.fields.description == "My ride receipt is missing"
    assert result.dialogue.pending_prompt.purpose == "details"
    assert dialogue.draft.fields.description is None
