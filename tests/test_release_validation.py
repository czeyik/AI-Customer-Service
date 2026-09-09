import json

from app.config import Settings
from app.services.answer_generation import ProviderResponse
from scripts import release_eval
from scripts.release_eval import SCENARIOS, run


def test_release_evaluation_covers_required_trilingual_scenarios_and_outage() -> None:
    assert {scenario.name for scenario in SCENARIOS} == {
        "rider",
        "driver",
        "business_partner",
        "safety",
        "fraud",
        "payment",
        "account",
        "complaint",
        "human",
        "normal_faq",
        "prohibited_action",
        "uncertainty",
    }
    report = run("outage")
    assert report["scenarios"] == report["passed"] == 36
    assert report["provider_calls"] == 15
    assert report["provider_successes"] == 0
    assert report["thresholds"]["pass_rate_at_least_0_95"]
    assert report["thresholds"]["p95_at_most_30_seconds"]
    assert report["thresholds"]["estimated_cost_within_limit"]
    assert report["failures"] == []


def test_live_release_evaluation_requires_every_provider_call_to_succeed(monkeypatch) -> None:
    class Provider:
        name = "zai"
        model = "glm-5.3-flash"

        def __init__(self, api_key, model) -> None:
            assert api_key == "release-test-api-key"
            assert model == self.model

        def generate(self, messages, *, max_output_tokens, timeout_seconds):
            assert max_output_tokens == 300
            assert timeout_seconds == 8
            content = messages[-1]["content"].split("Content: ", 1)[1].split("\n\n[", 1)[0]
            return ProviderResponse(
                json.dumps({"answer": content, "citations": [1]}),
                prompt_tokens=100,
                completion_tokens=10,
            )

    def settings(_env_file="configured") -> Settings:
        return Settings(
            _env_file=None,
            zai_api_key="release-test-api-key" if _env_file == "configured" else "",
            llm_model="glm-5.3-flash",
            llm_timeout_seconds=8,
            llm_max_output_tokens=300,
        )

    monkeypatch.setattr(release_eval, "Settings", settings)
    monkeypatch.setattr(release_eval, "ZAIProvider", Provider)

    report = run("live", input_price=0.1, output_price=0.2)

    assert report["passed"] == report["scenarios"] == 36
    assert report["provider_calls"] == report["provider_successes"] == 15
    assert report["prompt_tokens"] == 1500
    assert report["completion_tokens"] == 150
    assert report["estimated_cost_usd"] == 0.00018
    assert report["failures"] == []
