import json
from pathlib import Path

from app.config import Settings
from app.services.answer_generation import ProviderResponse
from scripts import release_eval
from scripts.smart_eval import run_smart


def test_expanded_outage_evaluation_reports_distinct_scenarios_and_review_gaps():
    report = run_smart("outage")
    assert report["distinct_non_escalation_scenarios"] == 100
    assert report["translated_sessions"] == 300
    assert report["held_out_total"] == 60
    assert report["provider_calls"] > 0
    assert report["provider_successes"] == 0
    assert report["unintended_mutations"] == 0
    for language in ("en", "ms", "zh"):
        assert report["language_metrics"][language]["non_escalation_total"] == 100
        assert report["language_metrics"][language]["semantic_reviewed"] == 0
    assert not report["rollout_ready"]
    assert report["projected_10000_calls_usd"] is None


def test_live_evaluation_counts_provider_usage_without_equating_it_to_correctness(monkeypatch):
    class Provider:
        name = "zai"
        model = "glm-5.3-flash"

        def __init__(self, api_key, model):
            assert api_key == "synthetic-key"

        def generate(self, messages, **kwargs):
            return ProviderResponse(json.dumps({
                "disposition": "clarify", "answer": "Which service do you mean?", "citations": [],
            }), prompt_tokens=100, completion_tokens=10)

    def settings(_env_file="configured"):
        return Settings(_env_file=None, zai_api_key="synthetic-key")

    monkeypatch.setattr(release_eval, "Settings", settings)
    monkeypatch.setattr(release_eval, "ZAIProvider", Provider)
    report = release_eval.run("live", input_price=0.15, output_price=0.50)
    assert 0 < report["provider_calls"] <= report["scenarios"]
    assert report["provider_successes"] == report["provider_calls"]
    assert report["prompt_tokens"] == report["provider_calls"] * 100
    assert report["passed"] < report["scenarios"]
    assert report["failures"]


def test_smart_evaluation_accepts_an_independent_holdout_matrix(tmp_path: Path):
    matrix = tmp_path / "holdout.tsv"
    matrix.write_text(
        "family\ten\tms\tzh\n"
        "booking\tHow do I check a ride request?\tBagaimana saya menyemak permintaan perjalanan?\t如何查看叫车请求？\n",
        encoding="utf-8",
    )
    report = run_smart("outage", matrix_path=matrix, all_held_out=True)
    assert report["distinct_non_escalation_scenarios"] == 1
    assert report["translated_sessions"] == 3
    assert report["held_out_total"] == 3
    assert report["held_out_passed"] == 3
