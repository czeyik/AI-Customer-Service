import json
import os
import time

import httpx
import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from openai import APITimeoutError
from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.services.dialogue import MAX_MODEL_CALLS, MAX_MODEL_SECONDS


class CompatibilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    citation_ids: list[int]


class ModelAttempts(BaseCallbackHandler):
    def __init__(self) -> None:
        self.started = 0
        self.completed = 0
        self.failed = 0

    def on_chat_model_start(self, *args, **kwargs) -> None:
        self.started += 1

    def on_llm_end(self, *args, **kwargs) -> None:
        self.completed += 1

    def on_llm_error(self, *args, **kwargs) -> None:
        self.failed += 1


@tool
def compatibility_echo(value: str) -> str:
    """Return a value to the compatibility-test model."""
    return value


def _response(tool_name: str, arguments: dict, call_id: str) -> dict:
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
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


def _model(handler) -> ChatOpenAI:
    return ChatOpenAI(
        api_key="test-key",
        base_url="https://api.z.ai/api/paas/v4/",
        model="glm-5.3-flash",
        max_retries=0,
        timeout=0.01,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_create_agent_runs_tool_then_returns_structured_output() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        body = (
            _response("compatibility_echo", {"value": "ready"}, "call-1")
            if len(calls) == 1
            else _response(
                "CompatibilityResult",
                {"answer": "ready", "citation_ids": [1]},
                "call-2",
            )
        )
        return httpx.Response(200, json=body)

    attempts = ModelAttempts()
    agent = create_agent(
        _model(handler),
        tools=[compatibility_echo],
        response_format=ToolStrategy(CompatibilityResult, handle_errors=False),
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Run the compatibility check"}]},
        config={"callbacks": [attempts]},
    )

    assert result["structured_response"] == CompatibilityResult(
        answer="ready", citation_ids=[1]
    )
    assert attempts.started == attempts.completed == 2
    assert attempts.failed == 0
    assert [call["tools"][0]["function"]["name"] for call in calls] == [
        "compatibility_echo",
        "compatibility_echo",
    ]


def test_create_agent_rejects_invalid_provider_response_and_counts_attempt() -> None:
    attempts = ModelAttempts()
    model = _model(lambda request: httpx.Response(200, text="{"))

    with pytest.raises(ValueError, match="Unexpected response type"):
        create_agent(model, tools=[compatibility_echo]).invoke(
            {"messages": [{"role": "user", "content": "invalid"}]},
            config={"callbacks": [attempts]},
        )

    assert (attempts.started, attempts.completed, attempts.failed) == (1, 0, 1)


def test_create_agent_surfaces_provider_timeout_without_retry() -> None:
    attempts = ModelAttempts()

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with pytest.raises(APITimeoutError):
        create_agent(_model(timeout), tools=[compatibility_echo]).invoke(
            {"messages": [{"role": "user", "content": "timeout"}]},
            config={"callbacks": [attempts]},
        )

    assert (attempts.started, attempts.completed, attempts.failed) == (1, 0, 1)


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_DIALOGUE_COMPATIBILITY") != "1",
    reason="requires an explicitly enabled Z.AI compatibility call",
)
def test_configured_zai_model_supports_agent_tools_and_structured_output() -> None:
    settings = Settings()
    assert settings.zai_api_key
    attempts = ModelAttempts()
    started = time.monotonic()
    agent = create_agent(
        ChatOpenAI(
            api_key=settings.zai_api_key,
            base_url="https://api.z.ai/api/paas/v4/",
            model=settings.llm_model,
            max_retries=0,
            timeout=min(settings.llm_timeout_seconds, MAX_MODEL_SECONDS),
            max_tokens=settings.llm_max_output_tokens,
            extra_body={"thinking": {"type": "enabled"}},
        ),
        tools=[compatibility_echo],
        response_format=ToolStrategy(CompatibilityResult, handle_errors=False),
        middleware=[
            ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="error")
        ],
    )

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Call compatibility_echo once with value 'ready'. Then return the tool value "
                        "as answer and citation_ids [1] using the required structured response."
                    ),
                }
            ]
        },
        config={"callbacks": [attempts]},
    )

    assert result["structured_response"] == CompatibilityResult(
        answer="ready", citation_ids=[1]
    )
    assert attempts.started <= MAX_MODEL_CALLS
    assert any(getattr(message, "name", None) == "compatibility_echo" for message in result["messages"])
    usage = [
        message.usage_metadata
        for message in result["messages"]
        if getattr(message, "usage_metadata", None)
    ]
    provider_models = sorted(
        {
            message.response_metadata["model_name"]
            for message in result["messages"]
            if getattr(message, "response_metadata", {}).get("model_name")
        }
    )
    assert provider_models == [settings.llm_model]
    print(
        json.dumps(
            {
                "model": settings.llm_model,
                "provider_models": provider_models,
                "attempts": attempts.started,
                "completed": attempts.completed,
                "failed": attempts.failed,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": usage,
            },
            sort_keys=True,
        )
    )
