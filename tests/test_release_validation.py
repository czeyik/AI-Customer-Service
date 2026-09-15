import json
import sys
import threading
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.models import Conversation
from app.services import chatbot
from scripts import release_eval
from scripts.release_eval import (
    MAX_MODEL_CALLS,
    MAX_UTF8_BYTES_PER_CHARACTER,
    CostBudget,
    EvaluationBudgetExceeded,
    summarize_events,
)
from scripts import smart_eval
from scripts.smart_eval import apply_reviews, run_smart


def test_expanded_outage_evaluation_reports_distinct_scenarios_and_review_gaps():
    report = run_smart("outage")
    assert report["distinct_non_escalation_scenarios"] == 100
    assert report["translated_sessions"] == 300
    assert report["held_out_total"] == 60
    assert report["provider_calls"] == 0
    assert report["provider_successes"] == 0
    assert report["unintended_mutations"] == 0
    for language in ("en", "ms", "zh"):
        assert report["language_metrics"][language]["non_escalation_total"] == 100
        assert report["language_metrics"][language]["semantic_reviewed"] == 0
        side_question = next(
            row
            for row in report["intake_sessions"]
            if row["language"] == language and row["scenario"] == "intake_side_question"
        )
        assert side_question["side_question_preserved_fields"] is True
    assert not report["rollout_ready"]
    assert report["projected_10000_messages_usd"] is None
    assert report["inbound_turns"] > report["translated_sessions"]
    assert report["fallbacks"] == report["inbound_turns"]
    assert report["denominators"]["inbound_turns"] == report["inbound_turns"]


def test_live_evaluation_counts_failed_agent_usage_and_fallback(monkeypatch):
    def failed_agent(*args, usage, **kwargs):
        usage.model_attempts += 2
        usage.model_successes += 1
        usage.tool_calls += 1
        usage.prompt_tokens += 100
        usage.completion_tokens += 10
        usage.reasoning_usage_missing += 1
        raise TimeoutError("synthetic provider failure after a staged tool")

    monkeypatch.setattr(chatbot, "run_dialogue_agent", failed_agent)
    report = release_eval.run(
        "live",
        input_price=0.15,
        output_price=0.50,
        max_cost=1,
        settings=Settings(_env_file=None),
        model=object(),
    )
    assert report["provider_calls"] > report["provider_successes"] > 0
    assert report["business_tool_calls"] > 0
    assert report["fallbacks"] > 0
    assert report["prompt_tokens"] > 0
    assert report["estimated_cost_usd"] > 0


def test_agent_failure_fallback_rate_uses_agent_executions_not_local_controls():
    usage = release_eval.summarize_events(
        [
            {
                "inbound_turns": 1,
                "fallbacks": 1,
                "local_control": "mandatory_handoff",
                "latency_ms": 1,
            },
            {
                "inbound_turns": 1,
                "agent_executions": 20,
                "agent_failure_fallback": 1,
                "fallbacks": 1,
                "latency_ms": 1,
            },
        ],
        input_price=0,
        output_price=0,
    )
    assert usage["intentional_local_controls"] == 1
    assert usage["agent_failure_fallbacks"] == 1
    assert usage["agent_executions"] == 20
    assert usage["agent_failure_fallback_rate"] == 0.05


def test_recovered_provider_timeouts_remain_in_timeout_rate():
    usage = release_eval.summarize_events(
        [{"model_attempts": 2, "model_timeouts": 1, "agent_error": None, "latency_ms": 1}],
        input_price=0,
        output_price=0,
    )
    assert usage["model_timeouts"] == 1
    assert usage["provider_timeout_rate"] == 0.5


def _saved_live_report():
    record = {
        "id": "en-000",
        "language": "en",
        "question": "How do I book a ride?",
        "answer": "Choose your pickup and destination in the app.",
        "held_out": False,
        "non_escalating": True,
        "answer_correct": None,
        "grounded_relevant": None,
    }
    focused = {
        "id": "focused-en-history_reference",
        "language": "en",
        "question": "Please answer my first question again.",
        "answer": "Choose your pickup and destination in the app.",
        "behavior_passed": True,
        "answer_correct": None,
        "grounded_relevant": None,
    }
    return {
        "mode": "live",
        "records": [record],
        "focused_dialogues": [focused],
        "language_metrics": {
            "en": {
                "non_escalation_total": 1,
                "non_escalation_passed": 1,
                "semantic_reviewed": 0,
                "answer_correct": 0,
                "answerable_reviewed": 0,
                "grounded_relevant": 0,
                "held_out_total": 0,
                "held_out_passed": 0,
                "necessary_total": 1,
                "necessary_passed": 1,
                "intake_total": 1,
                "intake_completed": 1,
                "side_question_preserved_fields": True,
            }
        },
        "denominators": {},
        "acceptance": {},
        "cco_review_status": "pending",
        "unintended_mutations": 0,
        "unexpected_offers": 0,
        "measured_cost_usd": 0,
        "p95_seconds": 0,
        "provider_timeout_rate": 0,
        "agent_failure_fallback_rate": 0,
        "projected_10000_messages_usd": 0,
    }


def test_saved_report_reviews_require_both_literal_scores_and_exact_answers():
    report = _saved_live_report()
    reviews = {
        row["id"]: {**row, "answer_correct": True, "grounded_relevant": True}
        for row in [*report["records"], *report["focused_dialogues"]]
    }
    reviewed = apply_reviews(report, reviews, 15)
    assert reviewed["cco_review_status"] == "complete"
    assert reviewed["denominators"]["semantic_reviewed"] == 2
    assert reviewed["rollout_ready"]

    over_budget = {**report, "projected_10000_messages_usd": 15.001}
    assert not apply_reviews(over_budget, reviews, 15)["rollout_ready"]

    ungrounded_focused = {
        **reviews,
        "focused-en-history_reference": {
            **reviews["focused-en-history_reference"], "grounded_relevant": False
        },
    }
    assert not apply_reviews(report, ungrounded_focused, 15)["rollout_ready"]

    partial = {"en-000": {**report["records"][0], "answer_correct": True}}
    pending = apply_reviews(report, partial, 15)
    assert pending["cco_review_status"] == "pending"
    assert pending["records"][0]["answer_correct"] is None

    with pytest.raises(ValueError, match="literal booleans"):
        apply_reviews(
            report,
            {"en-000": {**report["records"][0], "answer_correct": 1, "grounded_relevant": True}},
            15,
        )


def test_side_question_field_mutation_fails_intake_gate_after_contacts_pass(monkeypatch):
    real_handle = release_eval.EvaluationRunner.handle

    def mutate_side_question(self, db, request, **kwargs):
        response, metrics = real_handle(self, db, request, **kwargs)
        if (
            request.external_user_id == "intake-en-6"
            and request.text == "What are human support hours?"
        ):
            conversation = db.query(Conversation).filter_by(
                external_user_id=request.external_user_id
            ).one()
            dialogue_data = dict(conversation.dialogue_data)
            draft = dict(dialogue_data["draft"])
            fields = dict(draft["fields"])
            fields["description"] = "What are human support hours?"
            draft["fields"] = fields
            dialogue_data["draft"] = draft
            conversation.dialogue_data = dialogue_data
            db.flush()
        return response, metrics

    monkeypatch.setattr(release_eval.EvaluationRunner, "handle", mutate_side_question)
    report = run_smart("outage", intake_only=True)

    side_question = next(
        row
        for row in report["intake_sessions"]
        if row["language"] == "en" and row["scenario"] == "intake_side_question"
    )
    assert side_question["side_question_preserved_fields"] is False
    assert side_question["ticket_created"] is True
    assert side_question["completed"] is False
    assert report["acceptance"]["mandatory_intakes_pass"] is False


def test_held_out_semantic_gate_requires_grounded_deflection():
    report = _saved_live_report()
    report["records"] = [
        {
            **report["records"][0],
            "id": f"en-{index:03}",
            "question": f"Question {index}",
            "held_out": index == 19,
        }
        for index in range(20)
    ]
    report["language_metrics"]["en"].update(
        non_escalation_total=20,
        non_escalation_passed=20,
        held_out_total=1,
        held_out_passed=1,
    )
    reviews = {
        row["id"]: {
            **row,
            "answer_correct": row["id"] != "en-019",
            "grounded_relevant": True,
        }
        for row in [*report["records"], *report["focused_dialogues"]]
    }
    reviewed = apply_reviews(report, reviews, 15)
    assert reviewed["acceptance"]["answer_correct"] == {
        "passed": 19,
        "total": 20,
        "status": "measured",
    }
    assert reviewed["acceptance"]["held_out_paraphrases"] == {
        "passed": 0,
        "total": 1,
        "status": "measured",
    }
    assert not reviewed["rollout_ready"]


def test_direct_reviews_keep_focused_grounding_score(tmp_path: Path):
    matrix = tmp_path / "one.tsv"
    matrix.write_text(
        "family\ten\tms\tzh\nbooking\tHow do I check a ride request?\tx\ty\n",
        encoding="utf-8",
    )
    first = run_smart("outage", matrix_path=matrix, languages=("en",))
    reviews = {
        row["id"]: {
            "question": row["question"],
            "answer": row["answer"],
            "answer_correct": True,
            "grounded_relevant": True,
        }
        for row in [*first["records"], *first["focused_dialogues"]]
    }
    reviewed = run_smart(
        "outage", matrix_path=matrix, languages=("en",), reviews=reviews
    )
    assert all(row["grounded_relevant"] is True for row in reviewed["focused_dialogues"])


def test_review_cli_uses_explicit_or_temporary_output_without_overwriting_source(
    tmp_path: Path, monkeypatch, capsys
):
    report = _saved_live_report()
    report_path = tmp_path / "saved.json"
    reviews_path = tmp_path / "reviews.json"
    source = json.dumps(report)
    report_path.write_text(source, encoding="utf-8")
    real_mkdtemp = release_eval.tempfile.mkdtemp
    monkeypatch.setattr(
        release_eval.tempfile,
        "mkdtemp",
        lambda prefix: real_mkdtemp(prefix=prefix, dir=tmp_path),
    )
    reviews_path.write_text(
        json.dumps(
            {
                "records": [
                    {**report["records"][0], "answer_correct": True, "grounded_relevant": True}
                ],
                "focused_dialogues": [
                    {
                        **report["focused_dialogues"][0],
                        "answer_correct": True,
                        "grounded_relevant": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_eval.py",
            "--suite",
            "smart",
            "--report",
            str(report_path),
            "--reviews",
            str(reviews_path),
        ],
    )
    release_eval.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out)["rollout_ready"]
    default_output = Path(
        next(
            line.split(": ", 1)[1]
            for line in captured.err.splitlines()
            if line.startswith("Evaluation report: ")
        )
    )
    assert default_output.name == "report.json"
    assert default_output.parent.name.startswith("dudu-evaluation-")
    assert json.loads(default_output.read_text(encoding="utf-8"))["rollout_ready"]
    assert report_path.read_text(encoding="utf-8") == source

    explicit_output = tmp_path / "reviewed.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_eval.py",
            "--suite",
            "smart",
            "--report",
            str(report_path),
            "--reviews",
            str(reviews_path),
            "--output",
            str(explicit_output),
        ],
    )
    release_eval.main()
    assert json.loads(explicit_output.read_text(encoding="utf-8"))["rollout_ready"]
    assert report_path.read_text(encoding="utf-8") == source


def test_fresh_default_output_is_temporary_and_resume_requires_a_path(
    tmp_path: Path, monkeypatch, capsys
):
    docs_evaluation = Path(__file__).resolve().parents[1] / "docs" / "evaluation"
    before = {path.name for path in docs_evaluation.iterdir()}
    real_mkdtemp = release_eval.tempfile.mkdtemp
    monkeypatch.setattr(
        release_eval.tempfile,
        "mkdtemp",
        lambda prefix: real_mkdtemp(prefix=prefix, dir=tmp_path),
    )

    temporary_entries = set(tmp_path.iterdir())
    calls = []
    captured_kwargs = {}

    def fake_run_smart(*args, **kwargs):
        calls.append(args)
        captured_kwargs.update(kwargs)
        return {"mode": "outage", "rollout_ready": False}

    monkeypatch.setattr(smart_eval, "run_smart", fake_run_smart)
    monkeypatch.setattr(
        sys, "argv", ["release_eval.py", "--suite", "smart", "--resume"]
    )
    with pytest.raises(SystemExit, match="--resume requires --checkpoint or --output"):
        release_eval.main()
    assert calls == []
    assert set(tmp_path.iterdir()) == temporary_entries
    assert {path.name for path in docs_evaluation.iterdir()} == before

    checkpoint = tmp_path / "resume.checkpoint.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_eval.py",
            "--suite",
            "smart",
            "--resume",
            "--checkpoint",
            str(checkpoint),
        ],
    )
    release_eval.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out)["mode"] == "outage"
    assert len(calls) == 1
    assert captured_kwargs["checkpoint_path"] == checkpoint
    assert captured_kwargs["resume"] is True
    assert {path.name for path in docs_evaluation.iterdir()} == before


def test_legacy_outage_cli_passes_without_provider_calls(tmp_path: Path, monkeypatch, capsys):
    real_mkdtemp = release_eval.tempfile.mkdtemp
    monkeypatch.setattr(
        release_eval.tempfile,
        "mkdtemp",
        lambda prefix: real_mkdtemp(prefix=prefix, dir=tmp_path),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["release_eval.py", "--suite", "legacy", "--mode", "outage"],
    )

    release_eval.main()

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["scenarios"] == report["passed"] == 36
    assert report["failures"] == []
    assert report["provider_calls"] == report["provider_successes"] == 0
    assert report["agent_failure_fallback_rate"] is None
    assert report["thresholds"]["agent_failure_fallback_rate_at_most_0_05"] is True
    report_path = Path(
        next(
            line.split(": ", 1)[1]
            for line in captured.err.splitlines()
            if line.startswith("Evaluation report: ")
        )
    )
    assert report_path.parent.name.startswith("dudu-evaluation-")
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


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


def test_checkpoint_keeps_language_workers_parallel(tmp_path, monkeypatch):
    matrix = tmp_path / "one.tsv"
    matrix.write_text(
        "family\ten\tms\tzh\nbooking\tHow do I check a ride request?\tx\ty\n",
        encoding="utf-8",
    )
    checkpoint = tmp_path / "run.checkpoint.json"
    original = release_eval.EvaluationRunner.handle
    lock = threading.Lock()
    active = 0
    peak = 0

    def tracked(self, db, request, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.05)
            return original(self, db, request, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(release_eval.EvaluationRunner, "handle", tracked)
    report = run_smart(
        "outage",
        matrix_path=matrix,
        case_ids=("en-000", "ms-000", "zh-000"),
        checkpoint_path=checkpoint,
    )

    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert peak >= 2
    assert report["coverage"]["complete"] is True
    assert saved["status"] == "complete"
    assert saved["in_progress"] == {}
    assert sorted(saved["cases"]) == ["en-000", "ms-000", "zh-000"]


def test_live_cost_reserve_stops_before_starting_an_over_budget_turn():
    budget = CostBudget(
        max_cost=0.001,
        input_price=100,
        output_price=100,
        settings=Settings(_env_file=None),
        enabled=True,
    )
    with pytest.raises(EvaluationBudgetExceeded, match="reserve would exceed"):
        budget.reserve()


def test_cost_reservation_uses_utf8_character_bound():
    settings = Settings(
        _env_file=None,
        llm_max_input_chars=1,
        llm_max_output_tokens=1,
    )
    budget = CostBudget(
        max_cost=1,
        input_price=1,
        output_price=1,
        settings=settings,
        enabled=True,
    )
    assert budget._turn_reserve == pytest.approx(
        MAX_MODEL_CALLS
        * (MAX_UTF8_BYTES_PER_CHARACTER + 1)
        / 1_000_000
    )


def test_retry_usage_keeps_full_reservation_then_settles_once():
    settings = Settings(_env_file=None)
    budget = CostBudget(
        max_cost=1,
        input_price=0.15,
        output_price=0.50,
        settings=settings,
        enabled=True,
    )
    assert not budget._usage_cost(
        {
            "model_attempts": 1,
            "model_successes": 1,
            "model_timeouts": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    )[1]
    reservation = budget.reserve()
    retry = {
        "inbound_turns": 1,
        "latency_ms": 1,
        "model_attempts": 2,
        "model_successes": 1,
        "model_timeouts": 1,
        "prompt_tokens": 100,
        "completion_tokens": 10,
    }
    budget.settle(reservation, retry)
    assert budget.spent == 0
    assert budget.reserved == pytest.approx(float(reservation))

    recovered = {**retry, "model_successes": 2, "model_timeouts": 0}
    budget.settle(reservation, recovered)
    budget.settle(reservation, recovered)
    expected = (100 * 0.15 + 10 * 0.50) / 1_000_000
    assert budget.spent == pytest.approx(expected)
    assert budget.reserved == 0
    usage = summarize_events(
        [{**retry, "reserved_cost_usd": float(reservation)}], 0.15, 0.50
    )
    assert usage["measured_cost_usd"] == pytest.approx(expected, abs=1e-6)
    assert usage["conservative_cost_usd"] == pytest.approx(float(reservation), abs=1e-6)
    assert usage["usage_bounded"] is True


def test_partial_usage_expands_reservation_and_enforces_cap():
    settings = Settings(
        _env_file=None,
        llm_max_input_chars=1,
        llm_max_output_tokens=1,
    )
    budget = CostBudget(
        max_cost=1,
        input_price=1,
        output_price=1,
        settings=settings,
        enabled=True,
    )
    reservation = budget.reserve()
    partial = {
        "model_attempts": 1,
        "model_successes": 0,
        "prompt_tokens": 100,
        "completion_tokens": 100,
    }
    actual = (100 + 100) / 1_000_000
    assert actual > float(reservation)
    budget.settle(reservation, partial)
    assert budget.snapshot()["reserved"] == pytest.approx(actual)
    assert summarize_events(
        [{**partial, "latency_ms": 1, "reserved_cost_usd": float(reservation)}],
        1,
        1,
    )["conservative_cost_usd"] == pytest.approx(actual, abs=1e-6)

    capped = CostBudget(
        max_cost=0.0001,
        input_price=1,
        output_price=1,
        settings=settings,
        enabled=True,
    )
    capped_reservation = capped.reserve()
    with pytest.raises(EvaluationBudgetExceeded, match="known provider usage"):
        capped.settle(capped_reservation, partial)


def test_unknown_usage_without_reservation_blocks_readiness():
    usage = summarize_events(
        [
            {
                "latency_ms": 1,
                "model_attempts": 1,
                "model_successes": 0,
                "prompt_tokens": None,
            }
        ],
        0.15,
        0.50,
    )
    assert usage["usage_bounded"] is False
    assert summarize_events(
        [
            {
                "latency_ms": 1,
                "model_attempts": 1,
                "model_successes": 0,
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "reserved_cost_usd": 0,
            }
        ],
        0.15,
        0.50,
    )["usage_bounded"] is False
    report = _saved_live_report()
    report.update(
        usage_bounded=False,
        untrusted_usage_events=1,
        conservative_cost_usd=0,
    )
    reviews = {
        row["id"]: {**row, "answer_correct": True, "grounded_relevant": True}
        for row in [*report["records"], *report["focused_dialogues"]]
    }
    assert not apply_reviews(report, reviews, 15)["rollout_ready"]


def test_bounded_retry_usage_can_pass_timeout_gate_with_reviews():
    report = _saved_live_report()
    report.update(
        usage_bounded=True,
        untrusted_usage_events=1,
        conservative_cost_usd=0,
        provider_timeout_rate=0.01,
    )
    reviews = {
        row["id"]: {**row, "answer_correct": True, "grounded_relevant": True}
        for row in [*report["records"], *report["focused_dialogues"]]
    }
    assert apply_reviews(report, reviews, 15)["rollout_ready"]


def test_10000_message_projection_uses_conservative_cost():
    usage = {
        "inbound_turns": 100,
        "measured_cost_usd": 0.10,
        "conservative_cost_usd": 0.20,
    }
    assert smart_eval._projected_10000_messages_cost(usage, "live") == 20.0


def test_checkpoint_budget_rejection_leaves_no_in_progress_case(tmp_path, monkeypatch):
    matrix = tmp_path / "one.tsv"
    matrix.write_text(
        "family\ten\tms\tzh\nbooking\tHow do I check a ride request?\tx\ty\n",
        encoding="utf-8",
    )
    checkpoint = tmp_path / "run.checkpoint.json"
    settings = Settings(_env_file=None)
    budget = CostBudget(
        max_cost=0,
        input_price=0.15,
        output_price=0.50,
        settings=settings,
        enabled=True,
    )
    provider_calls = []

    def provider_must_not_run(*args, **kwargs):
        provider_calls.append(True)
        raise AssertionError("provider work started before the budget check")

    monkeypatch.setattr(release_eval.ChatbotService, "handle", provider_must_not_run)
    with pytest.raises(EvaluationBudgetExceeded, match="reserve would exceed"):
        run_smart(
            "live",
            input_price=0.15,
            output_price=0.50,
            max_cost=0,
            settings=settings,
            model=object(),
            _budget=budget,
            languages=("en",),
            matrix_path=matrix,
            case_ids=("en-000",),
            checkpoint_path=checkpoint,
        )

    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert provider_calls == []
    assert saved["in_progress"] == {}
    assert saved["status"] == "running"


def test_live_cost_rejects_non_finite_pricing():
    with pytest.raises(ValueError, match="finite"):
        CostBudget(
            max_cost=1,
            input_price=float("nan"),
            output_price=0.50,
            settings=Settings(_env_file=None),
            enabled=True,
        )


def test_report_output_is_preflighted_and_written_atomically(tmp_path: Path):
    destination = tmp_path / "report.json"
    release_eval.atomic_write_json(destination, {"version": 1})
    assert json.loads(destination.read_text(encoding="utf-8")) == {"version": 1}
    release_eval.atomic_write_json(destination, {"version": 2})
    assert json.loads(destination.read_text(encoding="utf-8")) == {"version": 2}
    with pytest.raises(FileNotFoundError, match="output directory"):
        release_eval.preflight_output_path(tmp_path / "missing" / "report.json")


def test_semantic_review_must_match_the_new_answer(tmp_path: Path):
    matrix = tmp_path / "one.tsv"
    question = "How do I check a ride request?"
    matrix.write_text(
        f"family\ten\tms\tzh\nbooking\t{question}\tx\ty\n",
        encoding="utf-8",
    )
    reviews = {
        "en-000": {
            "question": question,
            "answer": "An answer from an older evaluation",
            "answer_correct": True,
            "grounded_relevant": True,
        }
    }
    with pytest.raises(ValueError, match="does not match current answer"):
        run_smart(
            "outage", matrix_path=matrix, languages=("en",), reviews=reviews
        )


def test_metrics_sink_failure_charges_captured_usage_once(monkeypatch):
    settings = Settings(_env_file=None)
    budget = CostBudget(
        max_cost=1,
        input_price=0.15,
        output_price=0.50,
        settings=settings,
        enabled=True,
    )
    runner = release_eval.EvaluationRunner(settings, budget, model=object())
    original_sink = runner.service.metrics_sink

    def failing_sink(metrics):
        original_sink(metrics)
        raise OSError("metrics persistence failed")

    def failed_turn(*args, **kwargs):
        runner.service.metrics_sink(
            {
                "inbound_turns": 1,
                "model_attempts": 1,
                "model_successes": 1,
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "latency_ms": 1,
            }
        )

    monkeypatch.setattr(runner.service, "metrics_sink", failing_sink)
    monkeypatch.setattr(runner.service, "handle", failed_turn)
    with pytest.raises(OSError, match="metrics persistence"):
        runner.handle(None, None)

    assert len(runner.events) == 1
    assert runner.events[0]["prompt_tokens"] == 100
    assert budget.spent == pytest.approx((100 * 0.15 + 10 * 0.50) / 1_000_000)
    assert budget.reserved == 0


def test_untrusted_usage_retains_reservation_and_checkpoint_resume_skips_cost(tmp_path, monkeypatch):
    settings = Settings(_env_file=None)
    budget = CostBudget(
        max_cost=1,
        input_price=0.15,
        output_price=0.50,
        settings=settings,
        enabled=True,
    )
    reservation = budget.reserve()
    budget.settle(reservation, {"prompt_tokens": 100})
    assert budget.reserved == pytest.approx(float(reservation))
    assert budget.spent == 0
    budget.settle(reservation, {"prompt_tokens": 100, "completion_tokens": 10})
    assert budget.reserved == 0
    assert budget.spent == pytest.approx((100 * 0.15 + 10 * 0.50) / 1_000_000)
    budget.settle(reservation, {"prompt_tokens": 100, "completion_tokens": 10})
    assert budget.spent == pytest.approx((100 * 0.15 + 10 * 0.50) / 1_000_000)

    missing_usage = budget.reserve()
    budget.settle(
        missing_usage,
        {"model_attempts": 1, "prompt_tokens": 0, "completion_tokens": 0},
    )
    assert budget.reserved == pytest.approx(float(missing_usage))
    budget.settle(
        missing_usage,
        {
            "model_attempts": 1,
            "model_successes": 1,
            "model_timeouts": 0,
            "prompt_tokens": 100,
            "completion_tokens": 10,
        },
    )
    assert budget.reserved == 0

    matrix = tmp_path / "one.tsv"
    matrix.write_text(
        "family\ten\tms\tzh\nbooking\tHow do I check a ride request?\tx\ty\n",
        encoding="utf-8",
    )
    checkpoint = tmp_path / "run.checkpoint.json"
    first = run_smart(
        "outage",
        matrix_path=matrix,
        languages=("en",),
        case_ids=("en-000",),
        checkpoint_path=checkpoint,
    )
    assert first["coverage"] == {
        "full_matrix": False,
        "complete": True,
        "status": "selected_subset",
        "selected_cases": 1,
        "completed_cases": 1,
    }

    def no_replay(*args, **kwargs):
        raise AssertionError("a completed case was dispatched again")

    monkeypatch.setattr(release_eval.EvaluationRunner, "handle", no_replay)
    resumed = run_smart(
        "outage",
        matrix_path=matrix,
        languages=("en",),
        case_ids=("en-000",),
        checkpoint_path=checkpoint,
        resume=True,
    )
    assert resumed["inbound_turns"] == first["inbound_turns"]
    assert resumed["coverage"]["status"] == "selected_subset"
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["status"] == "complete"


def test_resume_fails_closed_for_an_in_progress_case(tmp_path, monkeypatch):
    matrix = tmp_path / "one.tsv"
    matrix.write_text(
        "family\ten\tms\tzh\nbooking\tHow do I check a ride request?\tx\ty\n",
        encoding="utf-8",
    )
    checkpoint = tmp_path / "run.checkpoint.json"
    original = release_eval.EvaluationRunner.handle

    def interrupted(self, db, request, **kwargs):
        response = original(self, db, request, **kwargs)
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(release_eval.EvaluationRunner, "handle", interrupted)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        run_smart(
            "outage",
            matrix_path=matrix,
            languages=("en",),
            case_ids=("en-000",),
            checkpoint_path=checkpoint,
        )
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert "en-000" in saved["in_progress"]
    assert saved["in_progress"]["en-000"]["reserved_cost_usd"] > 0
    with pytest.raises(RuntimeError, match="unresolved in-progress"):
        run_smart(
            "outage",
            matrix_path=matrix,
            languages=("en",),
            case_ids=("en-000",),
            checkpoint_path=checkpoint,
            resume=True,
        )


def test_resume_rejects_changed_knowledge_corpus(tmp_path, monkeypatch):
    matrix = tmp_path / "one.tsv"
    matrix.write_text(
        "family\ten\tms\tzh\nbooking\tHow do I check a ride request?\tx\ty\n",
        encoding="utf-8",
    )
    corpus = tmp_path / "knowledge-corpus.md"
    corpus.write_text("# Original corpus\n", encoding="utf-8")
    monkeypatch.setattr(smart_eval, "CORPUS_PATH", corpus)
    settings = Settings(_env_file=None)
    manifest = smart_eval._selection_manifest(
        matrix,
        ("en",),
        settings=settings,
    )
    checkpoint = tmp_path / "run.checkpoint.json"
    smart_eval.EvaluationCheckpoint(checkpoint, manifest)

    corpus.write_text("# Changed corpus\n", encoding="utf-8")
    changed_manifest = smart_eval._selection_manifest(
        matrix,
        ("en",),
        settings=settings,
    )
    with pytest.raises(ValueError, match="evaluation_fingerprint"):
        smart_eval.EvaluationCheckpoint(checkpoint, changed_manifest, resume=True)
