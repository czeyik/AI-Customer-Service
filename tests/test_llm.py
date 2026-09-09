import io
import json
import logging

from app.config import Settings
from app.services import answer_generation
from app.services.answer_generation import (
    ApprovedKnowledgeResponder,
    ProviderError,
    ProviderResponse,
    ZAIProvider,
)
from app.services.retrieval import RetrievedChunk


CHUNKS = [
    RetrievedChunk(
        content="Fare estimates can change because of distance and traffic.",
        source_title="Fares",
        language="en",
        score=1.0,
    )
]


class FakeProvider:
    name = "fake"
    model = "test-model"

    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.messages = None

    def generate(self, messages, *, max_output_tokens, timeout_seconds):
        self.messages = messages
        if self.text is None:
            raise ProviderError("outage")
        return ProviderResponse(self.text, 20, 10, "safe-request-id")


def settings() -> Settings:
    return Settings(_env_file=None, llm_enabled=True, zai_api_key="test-api-key-value")


def test_grounded_hosted_answer_uses_only_approved_chunks(caplog) -> None:
    provider = FakeProvider(
        json.dumps(
            {
                "answer": "Fare estimates can change because of distance and traffic.",
                "citations": [1],
            }
        )
    )
    responder = ApprovedKnowledgeResponder(settings(), provider)

    with caplog.at_level(logging.INFO):
        answer = responder.generate("en", CHUNKS)

    assert answer == "Fare estimates can change because of distance and traffic."
    sent = json.dumps(provider.messages)
    assert "Fare estimates" in sent
    assert "customer" not in sent.lower()
    record = next(record for record in caplog.records if record.message == "llm_generation")
    assert (record.outcome, record.prompt_tokens, record.completion_tokens) == ("success", 20, 10)
    assert answer not in record.getMessage()


def test_provider_outage_uses_deterministic_approved_answer() -> None:
    answer = ApprovedKnowledgeResponder(settings(), FakeProvider()).generate("en", CHUNKS)

    assert "Fare estimates can change because of distance and traffic." in answer


def test_ungrounded_number_or_citation_uses_deterministic_answer() -> None:
    for response in (
        {"answer": "The fare is guaranteed for 30 days.", "citations": [1]},
        {"answer": "We guarantee fare estimates.", "citations": [1]},
        {"answer": "Fare estimates change.", "citations": [2]},
        {"answer": "Fare estimates change.", "citations": [True]},
        [],
    ):
        answer = ApprovedKnowledgeResponder(
            settings(), FakeProvider(json.dumps(response))
        ).generate("en", CHUNKS)
        assert answer.startswith("Here’s what I found")


def test_zai_provider_sends_bounded_tool_free_contract(monkeypatch) -> None:
    captured = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_urlopen(request, timeout):
        captured.update(
            timeout=timeout,
            authorization=request.headers["Authorization"],
            body=json.loads(request.data),
        )
        return Response(
            json.dumps(
                {
                    "id": "request-1",
                    "model": "glm-5.3-flash",
                    "choices": [
                        {
                            "message": {"content": '{"answer":"Grounded","citations":[1]}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 4},
                }
            ).encode()
        )

    monkeypatch.setattr(answer_generation, "urlopen", fake_urlopen)
    result = ZAIProvider("secret-key", "glm-5.3-flash").generate(
        [{"role": "user", "content": "approved knowledge"}],
        max_output_tokens=300,
        timeout_seconds=8,
    )

    assert (result.prompt_tokens, result.completion_tokens) == (5, 4)
    assert captured["timeout"] == 8
    assert captured["authorization"] == "Bearer secret-key"
    assert captured["body"]["max_tokens"] == 300
    assert "tools" not in captured["body"]


def test_total_prompt_limit_falls_back_without_calling_provider() -> None:
    provider = FakeProvider('{"answer":"unused","citations":[1]}')
    limited = Settings(
        _env_file=None,
        llm_enabled=True,
        zai_api_key="test-api-key-value",
        llm_max_input_chars=100,
    )

    answer = ApprovedKnowledgeResponder(limited, provider).generate("en", CHUNKS)

    assert answer.startswith("Here’s what I found")
    assert provider.messages is None
