"""Synthetic trilingual SMART evaluation; human semantic review is a separate denominator."""
import csv
import hashlib
import json
import threading
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.models import AdminUser, Base, Conversation, Ticket, SupportNotification
from app.schemas import ChatRequest
from app.services.dialogue import load_dialogue_data
from scripts.ingest_seed import CORPUS_PATH, ingest_seed
from scripts.release_eval import (
    AGENT_FAILURE_FALLBACK_RATE_MAX,
    BETA_PROJECTED_10000_MESSAGES_COST_MAX,
    SCENARIOS,
    CostBudget,
    EvaluationRunner,
    atomic_write_json,
    evaluation_metadata,
    preflight_output_path,
    summarize_events,
)


MATRIX = Path(__file__).resolve().parents[1] / "data/evaluation/smart-non-escalation.tsv"
NECESSARY_SCENARIOS = (
    "human",
    "safety",
    "fraud",
    "complaint",
    "business_partner",
    "prohibited_action",
)
CHECKPOINT_VERSION = 1

FOCUSED_DIALOGUES = {
    "history_reference": {
        "en": ["How do I book a ride?", "What limits can a voucher have?", "How can I log in?", "Please answer my first question again."],
        "ms": ["Bagaimana saya tempah perjalanan?", "Apakah had baucar?", "Bagaimana saya log masuk?", "Sila jawab soalan pertama saya semula."],
        "zh": ["如何预订行程？", "优惠券有哪些限制？", "如何登录？", "请再回答我的第一个问题。"],
    },
    "ambiguous_antecedent": {
        "en": ["How do I book a ride?", "What about that one?"],
        "ms": ["Bagaimana saya tempah perjalanan?", "Bagaimana dengan yang itu?"],
        "zh": ["如何预订行程？", "那个呢？"],
    },
    "unsupported_then_faq": {
        "en": ["What is the moon policy?", "How do I book a ride?"],
        "ms": ["Apakah polisi bulan?", "Bagaimana saya tempah perjalanan?"],
        "zh": ["月球政策是什么？", "如何预订行程？"],
    },
}


def _projected_10000_messages_cost(usage, mode):
    return (
        round(
            usage["conservative_cost_usd"]
            / max(usage["inbound_turns"], 1)
            * 10_000,
            3,
        )
        if mode == "live"
        else None
    )


def _matrix_rows(matrix):
    with Path(matrix).open(encoding="utf-8") as source:
        rows = list(csv.DictReader(source, delimiter="\t"))
    if not rows or len({row["en"] for row in rows}) != len(rows):
        raise ValueError("SMART matrix must contain unique non-empty English cases")
    return rows


def _case_catalog(matrix, languages, *, intake_only=False):
    rows = _matrix_rows(matrix)
    ids = []
    if not intake_only:
        ids.extend(f"{language}-{index:03}" for language in languages for index in range(len(rows)))
    ids.extend(
        f"necessary-{language}-{scenario}"
        for language in languages
        for scenario in NECESSARY_SCENARIOS
    )
    ids.extend(
        f"intake-{language}-{index}"
        for language in languages
        for index in range(10)
    )
    ids.extend(
        f"focused-{language}-{scenario}"
        for scenario in FOCUSED_DIALOGUES
        for language in languages
    )
    return rows, ids


def _selection_manifest(
    matrix,
    languages,
    *,
    mode="outage",
    input_price=0,
    output_price=0,
    max_cost=15,
    settings=None,
    model=None,
    intake_only=False,
    all_held_out=False,
    case_ids=None,
):
    matrix = Path(matrix)
    rows, all_case_ids = _case_catalog(matrix, languages, intake_only=intake_only)
    requested = sorted(set(case_ids or ()))
    unknown = sorted(set(requested) - set(all_case_ids))
    if unknown:
        raise ValueError(f"unknown SMART case ID(s): {', '.join(unknown)}")
    selected = requested or list(all_case_ids)
    full_matrix = (
        not intake_only
        and matrix.resolve() == MATRIX.resolve()
        and tuple(languages) == ("en", "ms", "zh")
        and not requested
    )
    digest = hashlib.sha256(matrix.read_bytes()).hexdigest()
    root = Path(__file__).resolve().parents[1]
    fingerprint_files = [
        path
        for pattern in ("app/**/*.py", "requirements.in", "requirements.txt", "scripts/ingest_seed.py")
        for path in root.glob(pattern)
        if path.is_file()
    ]
    fingerprint_files.append(Path(CORPUS_PATH))
    fingerprint = hashlib.sha256()
    for path in sorted(set(fingerprint_files + [Path(__file__).resolve(), root / "scripts/release_eval.py", matrix])):
        try:
            fingerprint_name = str(path.relative_to(root))
        except ValueError:
            fingerprint_name = str(path.resolve())
        fingerprint.update(fingerprint_name.encode())
        fingerprint.update(path.read_bytes())
    fingerprint_payload = {
        "mode": mode,
        "input_price": input_price,
        "output_price": output_price,
        "max_cost": max_cost,
        "settings": {
            key: getattr(settings, key, None)
            for key in (
                "llm_model",
                "llm_max_input_chars",
                "llm_max_output_tokens",
                "llm_timeout_seconds",
                "llm_enabled",
                "llm_customer_context_enabled",
                "intake_expiry_minutes",
            )
        },
        "model": getattr(model, "model_name", None),
        "model_type": type(model).__qualname__ if model is not None else None,
        "inputs": fingerprint.hexdigest(),
    }
    return {
        "schema_version": 1,
        "matrix": str(matrix.resolve()),
        "matrix_sha256": digest,
        "matrix_rows": len(rows),
        "languages": list(languages),
        "intake_only": bool(intake_only),
        "all_held_out": bool(all_held_out),
        "requested_case_ids": requested,
        "selected_case_ids": selected,
        "all_case_ids": all_case_ids,
        "full_matrix": full_matrix,
        "evaluation_fingerprint": hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, default=str).encode()
        ).hexdigest(),
    }


class EvaluationCheckpoint:
    """Atomic, process-local checkpoint for completed independent evaluation cases."""

    def __init__(self, path, manifest, *, resume=False):
        self.path = preflight_output_path(Path(path))
        self._lock = threading.RLock()
        if resume and not self.path.exists():
            raise FileNotFoundError(f"evaluation checkpoint does not exist: {self.path}")
        if self.path.exists():
            if not resume:
                raise FileExistsError(
                    f"checkpoint already exists; pass resume=True: {self.path}"
                )
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError(f"invalid evaluation checkpoint: {self.path}") from exc
            if data.get("schema_version") != CHECKPOINT_VERSION:
                raise ValueError("unsupported evaluation checkpoint version")
            stored = data.get("manifest", {})
            for key in (
                "matrix_sha256",
                "languages",
                "intake_only",
                "all_held_out",
                "requested_case_ids",
                "selected_case_ids",
                "all_case_ids",
                "evaluation_fingerprint",
            ):
                if stored.get(key) != manifest.get(key):
                    raise ValueError(f"checkpoint selection does not match current run: {key}")
            if data.get("in_progress"):
                active = sorted(data["in_progress"])
                raise RuntimeError(
                    "checkpoint has unresolved in-progress case(s); "
                    f"recover before resuming: {', '.join(active)}"
                )
            self.data = data
        else:
            self.data = {
                "schema_version": CHECKPOINT_VERSION,
                "status": "running",
                "manifest": manifest,
                "cases": {},
                "in_progress": {},
            }
            self._write()

    def _write(self):
        atomic_write_json(self.path, self.data)

    def cases_for(self, language, selected_ids):
        prefix = f"{language}-"
        selected = set(selected_ids)
        with self._lock:
            return {
                case_id: payload
                for case_id, payload in self.data.get("cases", {}).items()
                if case_id in selected and (
                    case_id.startswith(prefix)
                    or case_id.startswith(f"necessary-{language}-")
                    or case_id.startswith(f"intake-{language}-")
                    or case_id.startswith(f"focused-{language}-")
                )
            }

    def events(self, selected_ids):
        selected = set(selected_ids)
        result = []
        with self._lock:
            for case_id, payload in self.data.get("cases", {}).items():
                if case_id in selected:
                    result.extend(payload.get("events", []))
        return result

    def save_case(self, case_id, section, result, events):
        with self._lock:
            cases = self.data.setdefault("cases", {})
            if case_id in cases:
                return
            cases[case_id] = {
                "section": section,
                "result": result,
                "events": [dict(event) for event in events],
            }
            self.data.setdefault("in_progress", {}).pop(case_id, None)
            self._write()

    def start_case(self, case_id, section, reservation):
        with self._lock:
            active = self.data.setdefault("in_progress", {})
            if case_id in active:
                return
            active[case_id] = {
                "section": section,
                "reserved_cost_usd": round(float(reservation), 9),
                "events": [],
            }
            self._write()

    def record_event(self, case_id, section, event):
        with self._lock:
            active = self.data.setdefault("in_progress", {}).setdefault(
                case_id,
                {"section": section, "reserved_cost_usd": 0.0, "events": []},
            )
            active["events"].append(dict(event))
            self._write()

    def complete(self):
        with self._lock:
            self.data["status"] = "complete"
            self._write()

def _review_complete(row):
    return all(type(row.get(field)) is bool for field in ("answer_correct", "grounded_relevant"))


def _held_out_success(row):
    return row["non_escalating"] and row["answer_correct"] and row["grounded_relevant"]


def _refresh_review_metrics(report, max_cost):
    records = report["records"]
    focused = report["focused_dialogues"]
    for language, metrics in report["language_metrics"].items():
        rows = [row for row in records if row["language"] == language]
        reviewed = [row for row in rows if _review_complete(row)]
        metrics["semantic_reviewed"] = len(reviewed)
        metrics["answer_correct"] = sum(row["answer_correct"] for row in reviewed)
        metrics["answerable_reviewed"] = len(reviewed)
        metrics["grounded_relevant"] = sum(row["grounded_relevant"] for row in reviewed)
        held_out = [row for row in rows if row["held_out"]]
        held_out_reviewed = [row for row in held_out if _review_complete(row)]
        metrics["held_out_semantic_reviewed"] = len(held_out_reviewed)
        metrics["held_out_semantic_passed"] = sum(
            _held_out_success(row) for row in held_out_reviewed
        )
    report["cco_review_status"] = (
        "complete" if records and all(_review_complete(row) for row in [*records, *focused]) else "pending"
    )
    report["denominators"]["semantic_reviewed"] = sum(
        metrics["semantic_reviewed"] for metrics in report["language_metrics"].values()
    ) + sum(_review_complete(row) for row in focused)
    reviewed = [row for row in records if _review_complete(row)]
    held_out = [row for row in records if row["held_out"]]
    held_out_reviewed = [row for row in held_out if _review_complete(row)]
    acceptance = report["acceptance"]
    acceptance["answer_correct"] = {
        "passed": sum(row["answer_correct"] for row in reviewed),
        "total": len(reviewed),
        "status": "measured" if reviewed else "pending",
    }
    acceptance["grounded_relevant"] = {
        "passed": sum(row["grounded_relevant"] for row in reviewed),
        "total": len(reviewed),
        "status": "measured" if reviewed else "pending",
    }
    acceptance["held_out_paraphrases"] = {
        "passed": sum(
            _held_out_success(row) for row in held_out_reviewed
        ),
        "total": len(held_out),
        "status": "measured" if held_out and len(held_out_reviewed) == len(held_out) else "pending",
    }
    acceptance["focused_dialogues"] = {
        "behavior_passed": sum(row["behavior_passed"] for row in focused),
        "answer_correct": sum(
            row["answer_correct"] for row in focused if _review_complete(row)
        ),
        "total": len(focused),
        "semantic_status": "measured" if focused and all(_review_complete(row) for row in focused) else "pending",
    }
    report["rollout_ready"] = _rollout_ready(report, max_cost)


def apply_reviews(report, reviews, max_cost):
    """Apply literal human scores to saved answer pairs without running the provider."""
    report = dict(report)
    report["records"] = [dict(row) for row in report.get("records", [])]
    report["focused_dialogues"] = [dict(row) for row in report.get("focused_dialogues", [])]
    targets = {row["id"]: row for row in [*report["records"], *report["focused_dialogues"]]}
    if len(targets) != len(report["records"]) + len(report["focused_dialogues"]):
        raise ValueError("saved report has duplicate semantic-review IDs")
    for review_id, review in reviews.items():
        target = targets.get(review_id)
        if target is None:
            raise ValueError(f"semantic review ID is not in saved report: {review_id}")
        if review.get("question") != target["question"] or review.get("answer") != target["answer"]:
            raise ValueError(f"semantic review does not match saved answer: {review_id}")
        values = [review.get(field) for field in ("answer_correct", "grounded_relevant")]
        if any(value is not None and type(value) is not bool for value in values):
            raise ValueError(f"semantic review scores must be literal booleans: {review_id}")
        if all(type(value) is bool for value in values):
            target["answer_correct"], target["grounded_relevant"] = values
    _refresh_review_metrics(report, max_cost)
    return report


def _evaluation_settings(mode, settings, model):
    if mode == "outage":
        return settings or Settings(
            _env_file=None, llm_enabled=False, llm_customer_context_enabled=False
        )
    settings = settings or Settings()
    if model is None and (
        not settings.zai_api_key or settings.llm_model != "glm-5.3-flash"
    ):
        raise RuntimeError(
            "live mode requires the approved glm-5.3-flash API configuration"
        )
    return settings.model_copy(
        update={"llm_enabled": True, "llm_customer_context_enabled": True}
    )


def run_smart(
    mode="outage",
    *,
    input_price=0.15,
    output_price=0.50,
    max_cost=15,
    reviews=None,
    languages=None,
    intake_only=False,
    matrix_path=None,
    all_held_out=False,
    price_source=None,
    price_checked=None,
    settings=None,
    model=None,
    _budget=None,
    case_ids=None,
    checkpoint_path=None,
    resume=False,
    selection_manifest_path=None,
    _checkpoint=None,
    _selected_case_ids=None,
    _manifest=None,
):
    matrix = Path(matrix_path) if matrix_path else MATRIX
    if languages is None:
        if resume and not checkpoint_path and _checkpoint is None:
            raise ValueError("resume requires checkpoint_path")
        settings = _evaluation_settings(mode, settings, model)
        requested_case_ids = tuple(sorted(set(case_ids or ())))
        manifest = _manifest or _selection_manifest(
            matrix,
            ("en", "ms", "zh"),
            mode=mode,
            input_price=input_price,
            output_price=output_price,
            max_cost=max_cost,
            settings=settings,
            model=model,
            intake_only=intake_only,
            all_held_out=all_held_out,
            case_ids=requested_case_ids,
        )
        if selection_manifest_path:
            atomic_write_json(Path(selection_manifest_path), manifest)
        selected_case_ids = tuple(manifest["selected_case_ids"])
        checkpoint = _checkpoint
        if checkpoint is None and checkpoint_path:
            checkpoint = EvaluationCheckpoint(
                checkpoint_path, manifest, resume=resume
            )
        elif checkpoint is not None and resume is False and checkpoint_path:
            raise ValueError("checkpoint cannot be reused without resume=True")
        budget = CostBudget(
            max_cost=max_cost,
            input_price=input_price,
            output_price=output_price,
            settings=settings,
            enabled=mode == "live",
        )
        if checkpoint:
            budget.restore_events(checkpoint.events(selected_case_ids))
        selected_languages = tuple(
            language
            for language in ("en", "ms", "zh")
            if any(
                case_id.startswith(f"{language}-")
                or case_id.startswith(f"necessary-{language}-")
                or case_id.startswith(f"intake-{language}-")
                or case_id.startswith(f"focused-{language}-")
                for case_id in selected_case_ids
            )
        )
        # A selector is a diagnostic slice; avoid creating workers for languages
        # with no selected cases. The default remains the existing three-worker run.
        worker_languages = selected_languages or ("en", "ms", "zh")
        worker_args = dict(
            mode=mode,
            input_price=input_price,
            output_price=output_price,
            reviews=reviews,
            intake_only=intake_only,
            matrix_path=matrix,
            all_held_out=all_held_out,
            max_cost=max_cost,
            price_source=price_source,
            price_checked=price_checked,
            settings=settings,
            model=model,
            _budget=budget,
            case_ids=selected_case_ids,
            _checkpoint=checkpoint,
            _selected_case_ids=selected_case_ids,
            _manifest=manifest,
        )
        with ThreadPoolExecutor(max_workers=3) as pool:
            parts = list(
                pool.map(
                    lambda language: run_smart(
                        languages=(language,), **worker_args
                    ),
                    worker_languages,
                )
            )
        if not parts:
            raise ValueError("case selector did not select any supported language")
        report = dict(parts[0])
        for key in ("records", "necessary_cases", "intake_sessions", "focused_dialogues"):
            report[key] = [row for part in parts for row in part[key]]
        report["language_metrics"] = {language: metrics for part in parts for language, metrics in part["language_metrics"].items()}
        report["cco_review_status"] = (
            "complete"
            if all(part["cco_review_status"] == "complete" for part in parts)
            else "pending"
        )
        for key in ("translated_sessions", "unintended_mutations", "unexpected_offers", "held_out_total", "held_out_passed", "ticket_count", "notifications"):
            report[key] = sum(part[key] for part in parts)
        report["family_failures"] = dict(Counter(row["family"] for row in report["records"] if not row["non_escalating"]))
        completed_case_ids = [
            row["id"] for row in report["records"]
        ] + [
            row["id"] for row in report["necessary_cases"]
        ] + [
            row["id"] for row in report["intake_sessions"]
        ] + [
            row["id"] for row in report["focused_dialogues"]
        ]
        coverage_complete = set(completed_case_ids) == set(selected_case_ids)
        report["selection"] = {
            **manifest,
            "completed_case_ids": sorted(set(completed_case_ids)),
            "manifest_path": str(selection_manifest_path) if selection_manifest_path else None,
        }
        report["coverage"] = {
            "full_matrix": bool(manifest["full_matrix"]),
            "complete": coverage_complete,
            "status": (
                "full_matrix"
                if manifest["full_matrix"] and coverage_complete
                else "selected_subset"
                if coverage_complete
                else "incomplete"
            ),
            "selected_cases": len(selected_case_ids),
            "completed_cases": len(set(completed_case_ids)),
        }
        events = [event for part in parts for event in part.pop("_events")]
        usage = summarize_events(events, input_price, output_price)
        report.update(
            inbound_turns=usage["inbound_turns"],
            provider_calls=usage["model_attempts"],
            provider_successes=usage["model_successes"],
            provider_timeouts=usage["model_timeouts"],
            provider_timeout_rate=usage["provider_timeout_rate"],
            business_tool_calls=usage["tool_calls"],
            fallbacks=usage["fallbacks"],
            intentional_local_controls=usage["intentional_local_controls"],
            agent_executions=usage["agent_executions"],
            agent_failure_fallbacks=usage["agent_failure_fallbacks"],
            agent_failure_fallback_rate=usage["agent_failure_fallback_rate"],
            agent_errors=usage["agent_errors"],
            agent_error_codes=usage["agent_error_codes"],
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            reported_reasoning_tokens=usage["reasoning_tokens"],
            reasoning_usage_missing=usage["reasoning_usage_missing"],
            measured_cost_usd=usage["measured_cost_usd"],
            conservative_cost_usd=usage["conservative_cost_usd"],
            untrusted_usage_events=usage["untrusted_usage_events"],
            usage_bounded_events=usage["usage_bounded_events"],
            usage_unbounded_events=usage["usage_unbounded_events"],
            usage_bounded=usage["usage_bounded"],
            reserved_cost_usd=usage["reserved_cost_usd"],
            p95_seconds=usage["p95_seconds"],
            projected_10000_messages_usd=_projected_10000_messages_cost(usage, mode),
        )
        report["cost_per_inbound_turn_usd"] = round(
            usage["measured_cost_usd"] / max(usage["inbound_turns"], 1), 6
        )
        report["cost_per_completed_ticket_usd"] = round(
            usage["measured_cost_usd"] / max(report["ticket_count"], 1), 6
        )
        report["denominators"] = {
            "non_escalation_sessions": report["translated_sessions"],
            "mandatory_handoff_cases": len(report["necessary_cases"]),
            "intake_conversations": len(report["intake_sessions"]),
            "focused_dialogues": len(report["focused_dialogues"]),
            "semantic_reviewed": sum(
                metrics["semantic_reviewed"]
                for metrics in report["language_metrics"].values()
            ) + sum(
                row["answer_correct"] is not None for row in report["focused_dialogues"]
            ),
            "inbound_turns": usage["inbound_turns"],
            "agent_executions": usage["agent_executions"],
            "completed_tickets": report["ticket_count"],
            "selected_cases": len(selected_case_ids),
            "completed_cases": len(set(completed_case_ids)),
        }
        reviewed = [row for row in report["records"] if row["answer_correct"] is not None]
        answerable = [row for row in report["records"] if row["grounded_relevant"] is not None]
        held_out_reviewed = [row for row in reviewed if row["held_out"]]
        disposition_passed = (
            sum(row["non_escalating"] for row in report["records"])
            + sum(row["passed"] for row in report["necessary_cases"])
            + sum(row["completed"] for row in report["intake_sessions"])
        )
        disposition_total = (
            len(report["records"])
            + len(report["necessary_cases"])
            + len(report["intake_sessions"])
        )
        report["acceptance"] = {
            "scope": report["coverage"]["status"],
            "complete": report["coverage"]["complete"],
            "expected_disposition": {
                "passed": disposition_passed,
                "total": disposition_total,
                "rate": round(disposition_passed / max(disposition_total, 1), 4),
            },
            "answer_correct": {
                "passed": sum(row["answer_correct"] is True for row in reviewed),
                "total": len(reviewed),
                "status": "pending" if not reviewed else "measured",
            },
            "grounded_relevant": {
                "passed": sum(row["grounded_relevant"] is True for row in answerable),
                "total": len(answerable),
                "status": "pending" if not answerable else "measured",
            },
            "held_out_paraphrases": {
                "passed": sum(
                    row["answer_correct"] is True and row["non_escalating"]
                    for row in held_out_reviewed
                ),
                "total": len(held_out_reviewed),
                "status": "pending" if not held_out_reviewed else "measured",
            },
            "focused_dialogues": {
                "behavior_passed": sum(
                    row["behavior_passed"] for row in report["focused_dialogues"]
                ),
                "answer_correct": sum(
                    row["answer_correct"] is True for row in report["focused_dialogues"]
                ),
                "total": len(report["focused_dialogues"]),
                "semantic_status": (
                    "measured"
                    if all(
                        row["answer_correct"] is not None
                        for row in report["focused_dialogues"]
                    )
                    else "pending"
                ),
            },
            "mandatory_intakes_pass": all(
                row["passed"] for row in report["necessary_cases"]
            ) and all(
                row["completed"]
                and row.get("side_question_preserved_fields", False)
                for row in report["intake_sessions"]
            ),
            "unauthorized_mutations": report["unintended_mutations"],
            "latency_within_30_seconds": usage["p95_seconds"] <= 30,
            "provider_timeout_rate_below_0_05": usage["provider_timeout_rate"] < 0.05,
            "agent_failure_fallback_rate_at_most_0_05": (
                usage["agent_failure_fallback_rate"] is not None
                and usage["agent_failure_fallback_rate"]
                <= AGENT_FAILURE_FALLBACK_RATE_MAX
            ),
            "cost_within_limit": usage["measured_cost_usd"] <= max_cost,
            "conservative_cost_within_limit": usage["conservative_cost_usd"] <= max_cost,
            "usage_complete": usage["untrusted_usage_events"] == 0,
            "usage_bounded": usage["usage_bounded"],
            "projected_10000_messages_within_usd_15_beta": (
                mode != "live"
                or report["projected_10000_messages_usd"]
                <= BETA_PROJECTED_10000_MESSAGES_COST_MAX
            ),
        }
        _refresh_review_metrics(report, max_cost)
        report.pop("_events", None)
        if checkpoint and coverage_complete:
            checkpoint.complete()
        return report
    if settings is None:
        settings = _evaluation_settings(mode, settings, model)
        _budget = CostBudget(
            max_cost=max_cost,
            input_price=input_price,
            output_price=output_price,
            settings=settings,
            enabled=mode == "live",
        )
    manifest = _manifest or _selection_manifest(
        matrix,
        languages,
        mode=mode,
        input_price=input_price,
        output_price=output_price,
        max_cost=max_cost,
        settings=settings,
        model=model,
        intake_only=intake_only,
        all_held_out=all_held_out,
        case_ids=case_ids,
    )
    if resume and not checkpoint_path and _checkpoint is None:
        raise ValueError("resume requires checkpoint_path")
    if _checkpoint is None and checkpoint_path:
        _checkpoint = EvaluationCheckpoint(
            checkpoint_path, manifest, resume=resume
        )
        if selection_manifest_path:
            atomic_write_json(Path(selection_manifest_path), manifest)
    selected_case_ids = set(_selected_case_ids or manifest["selected_case_ids"])
    scenarios = _matrix_rows(matrix)
    if matrix == MATRIX:
        assert len(scenarios) >= 100
    assert _budget is not None
    active_case = {}

    def persist_event(event):
        if _checkpoint and active_case:
            _checkpoint.record_event(
                active_case["id"], active_case["section"], event
            )

    runner = EvaluationRunner(
        settings, _budget, model=model, event_sink=persist_event
    )

    def handle_case(case_id, section, request):
        # Reserve before recording an in-flight case. A cap rejection must not
        # leave a checkpoint that falsely claims provider work is underway.
        reservation = _budget.reserve()
        if _checkpoint:
            try:
                checkpoint_reserve = float(reservation) or _budget._turn_reserve
                _checkpoint.start_case(case_id, section, checkpoint_reserve)
            except BaseException:
                _budget.settle(
                    reservation,
                    {
                        "usage_trustworthy": True,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                    },
                )
                raise
        active_case.update(id=case_id, section=section)
        try:
            return runner.handle(db, request, reservation=reservation)
        except BaseException:
            # EvaluationRunner has already retained and emitted any partial
            # usage it could observe. The in-progress marker stays durable so
            # resume fails closed instead of replaying an uncertain case.
            raise
        finally:
            active_case.clear()
    checkpoint_cases = (
        _checkpoint.cases_for(languages[0], selected_case_ids)
        if _checkpoint
        else {}
    )
    if checkpoint_cases:
        for payload in checkpoint_cases.values():
            runner.events.extend(payload.get("events", []))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    records = []
    recovered = {
        case_id: payload
        for case_id, payload in checkpoint_cases.items()
        if payload.get("section") == "records"
    }
    with sessions() as db:
        cco = AdminUser(username="synthetic-evaluation", display_name="Synthetic fixture", email="fixture@example.invalid", password_hash="unused", totp_secret_ref="unused", is_cco=True)
        db.add(cco)
        db.commit()
        ingest_seed(db, cco)
        for language in languages:
            for index, scenario in enumerate([] if intake_only else scenarios):
                key = f"{language}-{index:03}"
                if key not in selected_case_ids:
                    continue
                if key in recovered:
                    records.append(dict(recovered[key]["result"]))
                    continue
                started = time.monotonic()
                response, metrics = handle_case(
                    key,
                    "records",
                    ChatRequest(
                        channel="whatsapp",
                        external_user_id=f"synthetic-{key}",
                        text=scenario[language],
                        preferred_language=language,
                    ),
                )
                latency = time.monotonic() - started
                conversation = db.query(Conversation).filter_by(external_user_id=f"synthetic-{key}").one()
                dialogue = load_dialogue_data(conversation)
                offered = dialogue.pending_offer is not None
                mutation = dialogue.draft is not None or conversation.risk_level != "normal"
                stage = (
                    f"{dialogue.pending_prompt.purpose}:{dialogue.pending_prompt.field or ''}"
                    if dialogue.pending_prompt else dialogue.draft.status if dialogue.draft else "idle"
                )
                review = (reviews or {}).get(key, {})
                if review and (
                    review.get("question") != scenario[language]
                    or review.get("answer") != response.answer
                ):
                    raise ValueError(f"semantic review does not match current answer: {key}")
                record = dict(id=key, family=scenario["family"], language=language,
                    held_out=all_held_out or index % 5 == 4,
                    question=scenario[language], answer=response.answer, sources=response.sources,
                    stage=stage, ticket_offered=offered, unintended_mutation=mutation,
                    non_escalating=not mutation and not offered and response.ticket is None,
                    provider_calls=metrics["model_attempts"],
                    business_tool_calls=metrics["tool_calls"],
                    fallback=bool(metrics["fallbacks"]), metrics=metrics, latency_seconds=round(latency, 3),
                    answer_correct=review.get("answer_correct"), grounded_relevant=review.get("grounded_relevant"))
                records.append(record)
                if _checkpoint:
                    _checkpoint.save_case(key, "records", record, [metrics])
                if mode == "live" and (index + 1) % 25 == 0:
                    print(f"SMART live {language}: {index + 1}/{len(scenarios)} non-escalation sessions", file=sys.stderr, flush=True)
        necessary = []
        recovered_necessary = {
            case_id: payload
            for case_id, payload in checkpoint_cases.items()
            if payload.get("section") == "necessary"
        }
        for scenario in SCENARIOS:
            if scenario.name not in NECESSARY_SCENARIOS:
                continue
            for language in languages:
                key = f"necessary-{language}-{scenario.name}"
                if key not in selected_case_ids:
                    continue
                if key in recovered_necessary:
                    necessary.append(dict(recovered_necessary[key]["result"]))
                    continue
                response, metrics = handle_case(
                    key,
                    "necessary",
                    ChatRequest(
                        channel="whatsapp",
                        external_user_id=key,
                        text=scenario.text[language],
                        preferred_language=language,
                    ),
                )
                result = dict(
                    id=key,
                    language=language,
                    scenario=scenario.name,
                    passed=response.needs_ticket_consent and response.ticket is None,
                    metrics=metrics,
                )
                necessary.append(result)
                if _checkpoint:
                    _checkpoint.save_case(key, "necessary", result, [metrics])
        intakes = []
        recovered_intakes = {
            case_id: payload
            for case_id, payload in checkpoint_cases.items()
            if payload.get("section") == "intakes"
        }
        for language in languages:
            for index in range(10):
                user = f"intake-{language}-{index}"
                if user not in selected_case_ids:
                    continue
                if user in recovered_intakes:
                    intake = dict(recovered_intakes[user]["result"])
                    intake.setdefault("id", user)
                    intakes.append(intake)
                    continue
                tickets_before = db.query(Ticket).count()
                notifications_before = db.query(SupportNotification).count()
                turns = {
                    "en": ["I need a human", "Yes", "My name is Alex", "alex@example.com", "My ride receipt is missing", "Skip"],
                    "ms": ["Saya mahu pegawai manusia", "Ya", "Nama saya Ali", "ali@example.com", "Resit perjalanan saya tiada", "Langkau"],
                    "zh": ["我要人工客服", "同意", "我叫小陈", "chen@example.com", "我的行程收据找不到了", "跳过"],
                }[language]
                if index == 1:
                    turns[2:4] = ["My name is Alex, alex@example.com, +60123456789"]
                elif index == 2:
                    turns[2], turns[3] = turns[3], turns[2]
                elif index == 3:
                    turns.insert(-1, "Phone: +60198765432")
                elif index == 4:
                    turns.insert(-1, "My email is corrected@example.com")
                elif index == 5:
                    turns.insert(-1, "My name is Jamie")
                elif index == 6:
                    turns.insert(4, "What are human support hours?")
                elif index == 7:
                    turns[-1:-1] = ["The receipt did not arrive after the ride", "How do I book a car?"]
                elif index == 8:
                    turns[-1:-1] = ["The ride was yesterday", "The receipt is still missing today"]
                elif index == 9:
                    turns.insert(2, {"en":"Speak English", "ms":"Sila guna Bahasa Malaysia", "zh":"请用中文"}[language])
                trace = []
                before_side_question_fields = None
                side_question_preserved_fields = True
                for turn_index, turn in enumerate(turns):
                    response, metrics = handle_case(
                        user,
                        "intakes",
                        ChatRequest(
                            channel="whatsapp",
                            external_user_id=user,
                            text=turn,
                            preferred_language=language,
                            phone_number="+60123456789" if turn_index == 1 else None,
                        ),
                    )
                    state = db.query(Conversation).filter_by(external_user_id=user).one()
                    dialogue = load_dialogue_data(state)
                    if index == 6 and turn_index == 3:
                        before_side_question_fields = (
                            dialogue.draft.fields.model_dump(mode="json")
                            if dialogue.draft
                            else None
                        )
                    elif index == 6 and turn_index == 4:
                        after_side_question_fields = (
                            dialogue.draft.fields.model_dump(mode="json")
                            if dialogue.draft
                            else None
                        )
                        side_question_preserved_fields = (
                            before_side_question_fields is not None
                            and before_side_question_fields == after_side_question_fields
                        )
                    stage = f"{dialogue.pending_prompt.purpose}:{dialogue.pending_prompt.field or ''}" if dialogue.pending_prompt else dialogue.draft.status if dialogue.draft else "idle"
                    trace.append({"question": turn, "answer": response.answer, "stage": stage, "metrics": metrics})
                if not response.ticket:
                    completion = {"en":"Submit", "ms":"Hantar", "zh":"提交"}[language]
                    response, metrics = handle_case(
                        user,
                        "intakes",
                        ChatRequest(
                            channel="whatsapp",
                            external_user_id=user,
                            text=completion,
                            preferred_language=language,
                        ),
                    )
                    trace.append({"question": completion, "answer": response.answer, "metrics": metrics})
                ticket = db.query(Ticket).filter_by(public_id=response.ticket.public_id).first() if response.ticket else None
                expected_name = "Jamie" if index == 5 else "Alex" if index == 1 else {"en":"Alex", "ms":"Ali", "zh":"小陈"}[language]
                expected_email = "corrected@example.com" if index == 4 else "alex@example.com" if index == 1 else {"en":"alex@example.com", "ms":"ali@example.com", "zh":"chen@example.com"}[language]
                expected_phone = "+60198765432" if index == 3 else "+60123456789"
                completed = bool(
                    ticket
                    and (ticket.name, ticket.email, ticket.phone_number)
                    == (expected_name, expected_email, expected_phone)
                    and side_question_preserved_fields
                )
                focus = {
                    4: "corrected_historical_contact",
                    6: "intake_side_question",
                    7: "topic_switch_and_return",
                    8: "reference_after_three_exchanges",
                    9: "language_switch",
                }.get(index, f"intake_variant_{index}")
                intake = dict(
                    id=user,
                    language=language,
                    scenario=focus,
                    completed=completed,
                    side_question_preserved_fields=side_question_preserved_fields,
                    ticket_created=bool(ticket),
                    ticket_count_delta=db.query(Ticket).count() - tickets_before,
                    notification_count_delta=(
                        db.query(SupportNotification).count() - notifications_before
                    ),
                    trace=trace,
                )
                intakes.append(intake)
                if _checkpoint:
                    _checkpoint.save_case(
                        user,
                        "intakes",
                        intake,
                        [
                            turn["metrics"]
                            for turn in trace
                            if isinstance(turn.get("metrics"), dict)
                        ],
                    )
                if mode == "live":
                    print(f"SMART live {language}: {index + 1}/10 intakes", file=sys.stderr, flush=True)
        focused = []
        recovered_focused = {
            case_id: payload
            for case_id, payload in checkpoint_cases.items()
            if payload.get("section") == "focused"
        }
        for scenario, translations in FOCUSED_DIALOGUES.items():
            for language in languages:
                user = f"focused-{language}-{scenario}"
                if user not in selected_case_ids:
                    continue
                if user in recovered_focused:
                    focused_result = dict(recovered_focused[user]["result"])
                    focused_result.setdefault("id", user)
                    focused.append(focused_result)
                    continue
                trace = []
                for turn in translations[language]:
                    response, metrics = handle_case(
                        user,
                        "focused",
                        ChatRequest(
                            channel="whatsapp",
                            external_user_id=user,
                            text=turn,
                            preferred_language=language,
                        ),
                    )
                    trace.append(
                        {
                            "question": turn,
                            "answer": response.answer,
                            "sources": response.sources,
                            "model_attempts": metrics["model_attempts"],
                            "tool_calls": metrics["tool_calls"],
                            "metrics": metrics,
                        }
                    )
                state = db.query(Conversation).filter_by(external_user_id=user).one()
                dialogue = load_dialogue_data(state)
                review_id = f"focused-{language}-{scenario}"
                review = (reviews or {}).get(review_id, {})
                if review and (
                    review.get("question") != translations[language][-1]
                    or review.get("answer") != response.answer
                ):
                    raise ValueError(
                        f"semantic review does not match current answer: {review_id}"
                    )
                no_mutation = dialogue.draft is None and state.risk_level == "normal"
                focused_result = {
                        "id": review_id,
                        "language": language,
                        "scenario": scenario,
                        "question": translations[language][-1],
                        "answer": response.answer,
                        "no_mutation": no_mutation,
                        "final_answer_grounded": bool(response.sources),
                        "behavior_passed": no_mutation
                        and (
                            mode == "outage"
                            or scenario == "ambiguous_antecedent"
                            or (
                                bool(response.sources)
                                and (
                                    scenario != "history_reference"
                                    or bool(set(response.sources) & set(trace[0]["sources"]))
                                )
                            )
                        ),
                        "answer_correct": review.get("answer_correct"),
                        "grounded_relevant": review.get("grounded_relevant"),
                        "trace": trace,
                    }
                focused.append(focused_result)
                if _checkpoint:
                    _checkpoint.save_case(
                        review_id,
                        "focused",
                        focused_result,
                        [
                            turn["metrics"]
                            for turn in trace
                            if isinstance(turn.get("metrics"), dict)
                        ],
                    )
        ticket_count = db.query(Ticket).count() + sum(
            int(row.get("ticket_count_delta", 0) or 0)
            for row in intakes
            if row.get("id") in recovered_intakes
        )
        notifications = db.query(SupportNotification).count() + sum(
            int(row.get("notification_count_delta", 0) or 0)
            for row in intakes
            if row.get("id") in recovered_intakes
        )
    language_metrics = {}
    for language in languages:
        rows = [r for r in records if r["language"] == language]
        reviewed = [r for r in rows if r["answer_correct"] is not None]
        answerable = [r for r in rows if r["grounded_relevant"] is not None]
        language_metrics[language] = dict(
            non_escalation_total=len(rows), non_escalation_passed=sum(r["non_escalating"] for r in rows),
            semantic_reviewed=len(reviewed), answer_correct=sum(r["answer_correct"] is True for r in reviewed),
            answerable_reviewed=len(answerable), grounded_relevant=sum(r["grounded_relevant"] is True for r in answerable),
            held_out_total=sum(r["held_out"] for r in rows),
            held_out_passed=sum(r["held_out"] and r["non_escalating"] for r in rows),
            held_out_semantic_reviewed=sum(r["held_out"] and _review_complete(r) for r in rows),
            held_out_semantic_passed=sum(r["held_out"] and _review_complete(r) and _held_out_success(r) for r in rows),
            necessary_total=sum(r["language"]==language for r in necessary),
            necessary_passed=sum(r["language"]==language and r["passed"] for r in necessary),
            intake_total=sum(r["language"] == language for r in intakes),
            intake_completed=sum(r["language"]==language and r["completed"] for r in intakes),
            side_question_preserved_fields=all(
                r["side_question_preserved_fields"]
                for r in intakes
                if r["language"] == language and r["scenario"] == "intake_side_question"
            ),
        )
    usage = summarize_events(runner.events, input_price, output_price)
    reviewed_all = bool(records) and all(
        _review_complete(row) for row in [*records, *focused]
    )
    report = dict(**evaluation_metadata(settings), mode=mode, distinct_non_escalation_scenarios=len(records), translated_sessions=len(records),
        cco_review_status="complete" if reviewed_all else "pending", distinct_intake_scenarios=len({r["scenario"] for r in intakes}), language_metrics=language_metrics,
        unintended_mutations=sum(r["unintended_mutation"] for r in records),
        unexpected_offers=sum(r["ticket_offered"] for r in records),
        family_failures=dict(Counter(r["family"] for r in records if not r["non_escalating"])),
        held_out_total=sum(r["held_out"] for r in records),
        held_out_passed=sum(r["held_out"] and r["non_escalating"] for r in records),
        p95_seconds=usage["p95_seconds"], inbound_turns=usage["inbound_turns"],
        provider_calls=usage["model_attempts"], provider_successes=usage["model_successes"],
        provider_timeouts=usage["model_timeouts"],
        provider_timeout_rate=usage["provider_timeout_rate"],
        business_tool_calls=usage["tool_calls"], fallbacks=usage["fallbacks"],
        intentional_local_controls=usage["intentional_local_controls"],
        agent_executions=usage["agent_executions"],
        agent_failure_fallbacks=usage["agent_failure_fallbacks"],
        agent_failure_fallback_rate=usage["agent_failure_fallback_rate"],
        agent_errors=usage["agent_errors"],
        agent_error_codes=usage["agent_error_codes"],
        prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
        reported_reasoning_tokens=usage["reasoning_tokens"], reasoning_usage_missing=usage["reasoning_usage_missing"],
        measured_cost_usd=usage["measured_cost_usd"],
        conservative_cost_usd=usage["conservative_cost_usd"],
        untrusted_usage_events=usage["untrusted_usage_events"],
        usage_bounded_events=usage["usage_bounded_events"],
        usage_unbounded_events=usage["usage_unbounded_events"],
        usage_bounded=usage["usage_bounded"],
        reserved_cost_usd=usage["reserved_cost_usd"],
        projected_10000_messages_usd=None,
        input_price_per_million_usd=input_price,
        output_price_per_million_usd=output_price,
        price_source=price_source, price_checked=price_checked,
        ticket_count=ticket_count, notifications=notifications, necessary_cases=necessary,
        intake_sessions=intakes, focused_dialogues=focused, records=records,
        rollout_ready=False, _events=runner.events)
    report["model"] = settings.llm_model if mode == "live" else "disabled"
    completed_case_ids = [
        row["id"] for row in records + necessary + intakes + focused
    ]
    coverage_complete = set(completed_case_ids) == selected_case_ids
    report["selection"] = {
        **manifest,
        "completed_case_ids": sorted(set(completed_case_ids)),
        "manifest_path": str(selection_manifest_path) if selection_manifest_path else None,
    }
    report["coverage"] = {
        "full_matrix": bool(manifest["full_matrix"]),
        "complete": coverage_complete,
        "status": (
            "full_matrix"
            if manifest["full_matrix"] and coverage_complete
            else "selected_subset"
            if coverage_complete
            else "incomplete"
        ),
        "selected_cases": len(selected_case_ids),
        "completed_cases": len(set(completed_case_ids)),
    }
    if _checkpoint and coverage_complete:
        _checkpoint.complete()
    return report


def _rollout_ready(report, max_cost):
    metrics = report["language_metrics"].values()
    coverage = report.get("coverage", {})
    return bool(
        coverage.get("full_matrix", True)
        and coverage.get("complete", True)
        and report["cco_review_status"] == "complete"
        and report["unintended_mutations"] == 0
        and report["unexpected_offers"] == 0
        and all(row["behavior_passed"] for row in report["focused_dialogues"])
        and all(row["answer_correct"] is True for row in report["focused_dialogues"])
        and all(row["grounded_relevant"] is True for row in report["focused_dialogues"])
        and report["measured_cost_usd"] <= max_cost
        and report.get("conservative_cost_usd", report["measured_cost_usd"]) <= max_cost
        and report.get(
            "usage_bounded",
            report.get("untrusted_usage_events", 0) == 0,
        )
        and report["p95_seconds"] is not None
        and report["p95_seconds"] <= 30
        and report["provider_timeout_rate"] < 0.05
        and report["agent_failure_fallback_rate"] is not None
        and report["agent_failure_fallback_rate"] <= AGENT_FAILURE_FALLBACK_RATE_MAX
        and (
            not report["acceptance"]["held_out_paraphrases"]["total"]
            or (
                report["acceptance"]["held_out_paraphrases"]["status"] == "measured"
                and report["acceptance"]["held_out_paraphrases"]["passed"]
                / report["acceptance"]["held_out_paraphrases"]["total"] >= 0.95
            )
        )
        and (
            report["mode"] != "live"
            or report.get("projected_10000_messages_usd")
            is not None
            and report["projected_10000_messages_usd"]
            <= BETA_PROJECTED_10000_MESSAGES_COST_MAX
        )
        and all(
            item["non_escalation_passed"] / max(item["non_escalation_total"], 1) >= 0.95
            and item["necessary_passed"] == item["necessary_total"]
            and item["intake_completed"] == item["intake_total"]
            and item.get("side_question_preserved_fields", False)
            and item["semantic_reviewed"] > 0
            and item["answer_correct"] / item["semantic_reviewed"] >= 0.95
            and item["answerable_reviewed"] > 0
            and item["grounded_relevant"] / item["answerable_reviewed"] >= 0.95
            and (
                item["held_out_total"] == 0
                or (
                    item["held_out_semantic_reviewed"] == item["held_out_total"]
                    and item["held_out_semantic_passed"]
                    / item["held_out_semantic_reviewed"] >= 0.95
                )
            )
            for item in metrics
        )
    )
