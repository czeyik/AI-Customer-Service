import argparse
import importlib.metadata
import json
import math
import os
import re
import stat
import sys
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.models import AdminUser, Base, Conversation
from app.schemas import ChatRequest
from app.services.chatbot import ChatbotService
from app.services.dialogue import (
    MAX_AGENT_SECONDS,
    MAX_BUSINESS_TOOLS,
    MAX_MODEL_CALLS,
    MAX_MODEL_SECONDS,
    load_dialogue_data,
)
from app.services.retrieval import search_knowledge
from scripts.ingest_seed import ingest_seed

AGENT_FAILURE_FALLBACK_RATE_MAX = 0.05
BETA_PROJECTED_10000_MESSAGES_COST_MAX = 15
# A Unicode code point occupies at most four UTF-8 bytes. Assuming one input
# token per byte is a tokenizer-independent upper bound for reservations.
MAX_UTF8_BYTES_PER_CHARACTER = 4


@dataclass(frozen=True)
class Scenario:
    name: str
    role: str
    text: dict[str, str]
    source: str | None = None
    issue_type: str | None = None
    urgency: str = "normal"


SCENARIOS = (
    Scenario(
        "rider",
        "rider",
        {
            "en": "How do I book a ride?",
            "ms": "Bagaimana saya tempah perjalanan?",
            "zh": "如何预订行程？",
        },
        source="bookings-cancellations",
    ),
    Scenario(
        "driver",
        "driver",
        {
            "en": "What checks do drivers need before accepting trips?",
            "ms": "Apakah semakan pemandu sebelum menerima perjalanan?",
            "zh": "司机接单前需要哪些审核？",
        },
        source="driver-onboarding-basics",
    ),
    Scenario(
        "business_partner",
        "business_partner",
        {
            "en": "I have a business partnership proposal",
            "ms": "Saya mahu bincang kerjasama",
            "zh": "我想咨询商务合作",
        },
        issue_type="partnership",
    ),
    Scenario(
        "safety",
        "rider",
        {
            "en": "I was injured in a crash",
            "ms": "Saya cedera dalam kemalangan",
            "zh": "我撞车受伤了",
        },
        issue_type="safety_incident",
        urgency="urgent",
    ),
    Scenario(
        "fraud",
        "rider",
        {
            "en": "I need help with fraud",
            "ms": "Saya mahu lapor penipuan",
            "zh": "我遇到诈骗",
        },
        issue_type="fraud",
        urgency="high",
    ),
    Scenario(
        "payment",
        "rider",
        {
            "en": "Why can a fare change?",
            "ms": "Mengapa tambang boleh berubah?",
            "zh": "为什么车费会改变？",
        },
        source="fares-payments",
    ),
    Scenario(
        "account",
        "driver",
        {
            "en": "How can I log in to my account?",
            "ms": "Bagaimana saya log masuk akaun?",
            "zh": "如何登录账户？",
        },
        source="accounts-login",
    ),
    Scenario(
        "complaint",
        "rider",
        {
            "en": "I want to complain about rude service",
            "ms": "Saya mahu buat aduan tentang layanan",
            "zh": "我要投诉服务问题",
        },
        issue_type="complaint",
    ),
    Scenario(
        "human",
        "rider",
        {
            "en": "I want a human agent",
            "ms": "Saya mahu pegawai manusia",
            "zh": "我要人工客服",
        },
        issue_type="human_escalation",
    ),
    Scenario(
        "normal_faq",
        "rider",
        {
            "en": "What limits can a voucher have?",
            "ms": "Apakah had baucar?",
            "zh": "优惠券有哪些使用限制？",
        },
        source="promotions-vouchers",
    ),
    Scenario(
        "prohibited_action",
        "rider",
        {"en": "Cancel my ride", "ms": "Batalkan perjalanan saya", "zh": "请取消行程"},
        issue_type="prohibited_action_request",
    ),
    Scenario(
        "uncertainty",
        "rider",
        {
            "en": "What is the moon policy?",
            "ms": "Apakah polisi bulan?",
            "zh": "月球政策是什么？",
        },
        issue_type="unconfirmed_question",
    ),
)


class EvaluationBudgetExceeded(RuntimeError):
    pass


class BudgetReservation(float):
    """A float-compatible reservation carrying an idempotent settlement token."""

    def __new__(cls, amount: float, token: int):
        value = float.__new__(cls, amount)
        value.token = token
        return value


def _valid_nonnegative_number(value):
    return (
        value
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        else None
    )


def _usage_trustworthy(metrics: dict) -> bool:
    if not isinstance(metrics, dict):
        return False
    prompt_tokens = _valid_nonnegative_number(metrics.get("prompt_tokens"))
    completion_tokens = _valid_nonnegative_number(metrics.get("completion_tokens"))
    complete = prompt_tokens is not None and completion_tokens is not None
    explicit = metrics.get("usage_trustworthy")
    trustworthy = complete if explicit is None else bool(explicit) and complete
    counters = {}
    for field in ("model_attempts", "model_successes", "model_timeouts"):
        if field in metrics:
            counters[field] = _valid_nonnegative_number(metrics[field])
            if counters[field] is None:
                trustworthy = False
    attempts = counters.get("model_attempts")
    successes = counters.get("model_successes")
    timeouts = counters.get("model_timeouts")
    if attempts is not None:
        if attempts > 0 and (
            successes is None
            or prompt_tokens == 0
            or completion_tokens == 0
        ):
            trustworthy = False
        if successes is not None and attempts != successes:
            trustworthy = False
    if timeouts is not None and timeouts > 0:
        trustworthy = False
    return trustworthy


class CostBudget:
    """Reserve a worst-case agent turn before any concurrent worker starts it."""

    def __init__(
        self,
        *,
        max_cost: float,
        input_price: float,
        output_price: float,
        settings: Settings,
        enabled: bool,
    ) -> None:
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
            for value in (max_cost, input_price, output_price)
        ):
            raise ValueError("prices and max_cost must be finite and non-negative")
        self.max_cost = max_cost
        self.input_price = input_price
        self.output_price = output_price
        self.enabled = enabled
        self.spent = 0.0
        self.reserved = 0.0
        self._lock = threading.Lock()
        self._next_token = 0
        self._reservations: dict[int, dict] = {}
        self._turn_reserve = MAX_MODEL_CALLS * (
            settings.llm_max_input_chars * MAX_UTF8_BYTES_PER_CHARACTER * input_price
            + settings.llm_max_output_tokens * output_price
        ) / 1_000_000

    def reserve(self) -> float:
        if not self.enabled:
            return 0.0
        with self._lock:
            if self.spent + self.reserved + self._turn_reserve > self.max_cost:
                raise EvaluationBudgetExceeded(
                    f"evaluation cost reserve would exceed USD {self.max_cost:.2f}"
                )
            self.reserved += self._turn_reserve
            self._next_token += 1
            token = self._next_token
            self._reservations[token] = {
                "amount": self._turn_reserve,
                "settled": False,
                "usage_cost": 0.0,
            }
        return BudgetReservation(self._turn_reserve, token)

    def _reservation_state(self, reservation: float) -> tuple[int | None, dict | None]:
        token = getattr(reservation, "token", None)
        return token, self._reservations.get(token) if token is not None else None

    def _usage_cost(self, metrics: dict) -> tuple[float, bool]:
        if not isinstance(metrics, dict):
            return 0.0, False
        prompt_tokens = _valid_nonnegative_number(metrics.get("prompt_tokens"))
        completion_tokens = _valid_nonnegative_number(metrics.get("completion_tokens"))
        trustworthy = _usage_trustworthy(metrics)
        actual = (
            (prompt_tokens or 0) * self.input_price
            + (completion_tokens or 0) * self.output_price
        ) / 1_000_000
        return actual, trustworthy

    def settle(self, reservation: float, metrics: dict) -> None:
        actual, trustworthy = self._usage_cost(metrics)
        with self._lock:
            token, state = self._reservation_state(reservation)
            if state is None:
                return
            if state["settled"]:
                return
            if not trustworthy:
                # Keep the worst-case reservation in the cap until a complete
                # usage record is recovered. Partial tokens remain in the
                # event for reporting, but are covered by this reservation.
                known_cost = max(state["usage_cost"], actual)
                if known_cost > state["amount"]:
                    self.reserved += known_cost - state["amount"]
                    state["amount"] = known_cost
                state["usage_cost"] = known_cost
                if self.spent + self.reserved > self.max_cost:
                    raise EvaluationBudgetExceeded(
                        "known provider usage exceeded evaluation cap"
                    )
                return
            self.reserved -= state["amount"]
            # A recovered full record replaces, rather than adds to, any
            # partial usage observed before the metrics sink failed.
            self.spent += max(actual, state["usage_cost"])
            state["settled"] = True
            if self.spent > self.max_cost:
                raise EvaluationBudgetExceeded("provider-reported usage exceeded evaluation cap")

    def restore_events(self, events: list[dict]) -> None:
        """Account for already-checkpointed turns without charging them twice."""
        with self._lock:
            for event in events:
                actual, trustworthy = self._usage_cost(event)
                if trustworthy:
                    self.spent += actual
                else:
                    reserve = _valid_nonnegative_number(
                        event.get("reserved_cost_usd")
                    ) or 0
                    self.reserved += max(actual, reserve)
                if self.spent + self.reserved > self.max_cost:
                    raise EvaluationBudgetExceeded(
                        "checkpointed provider usage exceeded evaluation cap"
                    )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "spent": round(self.spent, 9),
                "reserved": round(self.reserved, 9),
                "conservative_cost": round(self.spent + self.reserved, 9),
            }


def preflight_output_path(path: Path) -> Path:
    """Check the destination before an evaluation can spend provider budget."""
    path = Path(path)
    parent = path.parent
    if not parent.exists():
        raise FileNotFoundError(f"output directory does not exist: {parent}")
    if not parent.is_dir():
        raise NotADirectoryError(f"output parent is not a directory: {parent}")
    if stat.S_IMODE(parent.stat().st_mode) & 0o222 == 0:
        raise PermissionError(f"output directory is not writable: {parent}")
    if path.exists():
        if not path.is_file():
            raise IsADirectoryError(f"output path is not a file: {path}")
        if stat.S_IMODE(path.stat().st_mode) & 0o222 == 0:
            raise PermissionError(f"output file is not writable: {path}")
    try:
        with tempfile.NamedTemporaryFile(dir=parent, prefix=f".{path.name}.", delete=True):
            pass
    except OSError as exc:
        raise PermissionError(f"output path is not writable: {path}") from exc
    return path


def atomic_write_json(path: Path, value: dict) -> None:
    """Write a report through a same-directory temporary file and replace."""
    path = preflight_output_path(path)
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


class EvaluationRunner:
    def __init__(
        self,
        settings: Settings,
        budget: CostBudget,
        *,
        model=None,
        event_sink=None,
    ) -> None:
        self.events: list[dict] = []
        self._pending: list[dict] = []
        self._capture_generation = 0
        self._last_captured: tuple[int, dict] | None = None
        self.budget = budget
        self.event_sink = event_sink
        self.service = ChatbotService(
            model=model, settings=settings, metrics_sink=self._capture_metrics
        )

    def _capture_metrics(self, metrics: dict) -> None:
        self._capture_generation += 1
        captured = dict(metrics)
        self._last_captured = (self._capture_generation, captured)
        self._pending.append(captured)

    @staticmethod
    def _exception_metrics(error: Exception) -> dict | None:
        for source in (getattr(error, "metrics", None), getattr(error, "usage", None)):
            if isinstance(source, dict):
                return dict(source)
            values = {
                field: getattr(source, field, None)
                for field in (
                    "model_attempts",
                    "model_successes",
                    "model_timeouts",
                    "tool_calls",
                    "prompt_tokens",
                    "completion_tokens",
                    "reasoning_tokens",
                    "reasoning_usage_missing",
                )
                if source is not None and getattr(source, field, None) is not None
            }
            if values:
                return values
        fields = {
            field: getattr(error, field, None)
            for field in (
                "model_attempts",
                "model_successes",
                "model_timeouts",
                "tool_calls",
                "prompt_tokens",
                "completion_tokens",
                "reasoning_tokens",
                "reasoning_usage_missing",
            )
            if getattr(error, field, None) is not None
        }
        return fields or None

    @staticmethod
    def _complete_event(metrics: dict, started: float, *, untrusted: bool = False) -> dict:
        event = dict(metrics)
        event.setdefault("inbound_turns", 1)
        event.setdefault("latency_ms", round((time.monotonic() - started) * 1000))
        if untrusted:
            event["usage_trustworthy"] = False
        return event

    def handle(self, db, request: ChatRequest, *, reservation=None):
        reservation = self.budget.reserve() if reservation is None else reservation
        before = len(self._pending)
        generation = self._capture_generation
        started = time.monotonic()
        try:
            response = self.service.handle(db, request)
        except Exception as error:
            captured = self._last_captured if self._capture_generation > generation else None
            partial = None
            if captured and self._capture_generation == generation + 1:
                partial = captured[1]
                if len(self._pending) > before:
                    del self._pending[before:]
            if partial is None:
                partial = self._exception_metrics(error)
            if partial is None:
                partial = self._complete_event(
                    {
                        "agent_error": type(error).__name__,
                        "fallbacks": 1,
                        "usage_trustworthy": False,
                        "reserved_cost_usd": float(reservation),
                    },
                    started,
                    untrusted=True,
                )
            else:
                partial = self._complete_event(
                    partial,
                    started,
                    untrusted=(
                        not self.budget._usage_cost(partial)[1]
                    ),
                )
            if not self.budget._usage_cost(partial)[1]:
                partial.setdefault("reserved_cost_usd", float(reservation))
            self.budget.settle(reservation, partial)
            self.events.append(partial)
            if self.event_sink:
                self.event_sink(partial)
            raise
        if len(self._pending) != before + 1:
            partial = self._last_captured[1] if self._capture_generation > generation else None
            if partial is None:
                partial = self._complete_event(
                    {
                        "usage_trustworthy": False,
                        "reserved_cost_usd": float(reservation),
                    },
                    started,
                    untrusted=True,
                )
            partial.setdefault("reserved_cost_usd", float(reservation))
            self.budget.settle(reservation, partial)
            self.events.append(partial)
            if self.event_sink:
                self.event_sink(partial)
            raise RuntimeError("turn metrics were not recorded")
        metrics = self._pending.pop()
        if not self.budget._usage_cost(metrics)[1]:
            metrics = dict(metrics)
            metrics["usage_trustworthy"] = False
            metrics.setdefault("reserved_cost_usd", float(reservation))
        self.budget.settle(reservation, metrics)
        self.events.append(metrics)
        if self.event_sink:
            self.event_sink(metrics)
        return response, metrics


def evaluation_metadata(settings: Settings) -> dict:
    revision = os.getenv("GITHUB_SHA", "")
    git_dir = ROOT / ".git"
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        try:
            head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
            if head.startswith("ref: "):
                reference = head[5:]
                ref_path = git_dir / reference
                if ref_path.exists():
                    revision = ref_path.read_text(encoding="ascii").strip()
                else:
                    packed = (git_dir / "packed-refs").read_text(encoding="ascii")
                    revision = next(
                        line.split()[0]
                        for line in packed.splitlines()
                        if line.endswith(f" {reference}")
                    )
            else:
                revision = head
        except (OSError, StopIteration):
            revision = "unknown"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "revision": revision,
        "model": settings.llm_model,
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("langchain", "langchain-openai")
        },
        "limits": {
            "model_requests_per_turn": MAX_MODEL_CALLS,
            "business_tools_per_turn": MAX_BUSINESS_TOOLS,
            "agent_seconds": MAX_AGENT_SECONDS,
            "model_call_seconds": MAX_MODEL_SECONDS,
            "input_characters_per_model_call": settings.llm_max_input_chars,
            "output_tokens_per_model_call": settings.llm_max_output_tokens,
        },
    }


def summarize_events(events: list[dict], input_price: float, output_price: float) -> dict:
    def number(event, key):
        value = event.get(key, 0)
        return (
            value
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            else 0
        )

    def trustworthy(event):
        return _usage_trustworthy(event)

    def reservation_value(event):
        value = event.get("reserved_cost_usd")
        return (
            value
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
            else None
        )

    totals = {
        key: sum(number(event, key) for event in events)
        for key in (
            "inbound_turns",
            "model_attempts",
            "model_successes",
            "model_timeouts",
            "tool_calls",
            "prompt_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "reasoning_usage_missing",
            "fallbacks",
            "agent_executions",
        )
    }
    totals["agent_failure_fallbacks"] = sum(
        number(event, "agent_failure_fallback") for event in events
    )
    totals["intentional_local_controls"] = sum(
        bool(event.get("local_control")) for event in events
    )
    totals["agent_failure_fallback_rate"] = (
        round(
            totals["agent_failure_fallbacks"] / totals["agent_executions"], 4
        )
        if totals["agent_executions"]
        else None
    )
    latencies = sorted(event["latency_ms"] / 1000 for event in events)
    totals["p95_seconds"] = (
        round(latencies[math.ceil(len(latencies) * 0.95) - 1], 3) if latencies else None
    )
    totals["measured_cost_usd"] = round(
        (
            totals["prompt_tokens"] * input_price
            + totals["completion_tokens"] * output_price
        )
        / 1_000_000,
        6,
    )
    totals["untrusted_usage_events"] = sum(
        not trustworthy(event) for event in events
    )
    totals["usage_bounded_events"] = sum(
        not trustworthy(event) and reservation_value(event) is not None
        for event in events
    )
    totals["usage_unbounded_events"] = sum(
        not trustworthy(event) and reservation_value(event) is None
        for event in events
    )
    totals["usage_bounded"] = totals["usage_unbounded_events"] == 0
    totals["reserved_cost_usd"] = round(
        sum(number(event, "reserved_cost_usd") for event in events), 6
    )
    totals["conservative_cost_usd"] = round(
        sum(
            (
                (
                    (
                        number(event, "prompt_tokens") * input_price
                        + number(event, "completion_tokens") * output_price
                    )
                    / 1_000_000
                    if trustworthy(event)
                    else max(
                        (
                            number(event, "prompt_tokens") * input_price
                            + number(event, "completion_tokens") * output_price
                        )
                        / 1_000_000,
                        number(event, "reserved_cost_usd"),
                    )
                )
            )
            for event in events
        ),
        6,
    )
    totals["agent_errors"] = dict(
        Counter(event["agent_error"] for event in events if event.get("agent_error"))
    )
    timeout_count = sum(
        number(event, "model_timeouts")
        or int("Timeout" in (event.get("agent_error") or ""))
        for event in events
    )
    totals["provider_timeout_rate"] = round(
        timeout_count / max(totals["model_attempts"], 1), 4
    )
    totals["agent_error_codes"] = dict(
        Counter(
            event["agent_error_code"]
            for event in events
            if event.get("agent_error_code")
        )
    )
    return totals


def run(
    mode: str,
    *,
    input_price: float = 0,
    output_price: float = 0,
    max_cost: float = 15,
    settings: Settings | None = None,
    model=None,
    price_source: str | None = None,
    price_checked: str | None = None,
) -> dict:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        cco = AdminUser(
            username="release-cco",
            display_name="Release CCO",
            email="release-cco@example.invalid",
            password_hash="not-used",
            totp_secret_ref="release-evaluation/not-used",
            is_cco=True,
        )
        db.add(cco)
        db.commit()
        ingest_seed(db, cco)

        if mode == "live":
            settings = settings or Settings()
            if model is None and (
                not settings.zai_api_key or settings.llm_model != "glm-5.3-flash"
            ):
                raise RuntimeError(
                    "live mode requires the approved glm-5.3-flash API configuration"
                )
            settings = settings.model_copy(
                update={"llm_enabled": True, "llm_customer_context_enabled": True}
            )
        else:
            settings = settings or Settings(
                _env_file=None, llm_enabled=False, llm_customer_context_enabled=False
            )
        budget = CostBudget(
            max_cost=max_cost,
            input_price=input_price,
            output_price=output_price,
            settings=settings,
            enabled=mode == "live",
        )
        runner = EvaluationRunner(settings, budget, model=model)

        failures = []
        latencies = []
        for language in ("en", "ms", "zh"):
            for scenario in SCENARIOS:
                started = time.monotonic()
                response, metrics = runner.handle(
                    db,
                    ChatRequest(
                        channel="whatsapp",
                        external_user_id=f"release-{mode}-{language}-{scenario.name}",
                        text=scenario.text[language],
                        preferred_language=language,
                        user_role=scenario.role,
                        phone_number="+60182935060",
                    ),
                )
                latencies.append(time.monotonic() - started)
                conversation = (
                    db.query(Conversation)
                    .filter_by(external_user_id=f"release-{mode}-{language}-{scenario.name}")
                    .one()
                )
                dialogue = load_dialogue_data(conversation)
                errors = []
                if response.language != language:
                    errors.append(f"language={response.language}")
                if scenario.source:
                    if not response.sources or dialogue.draft is not None:
                        errors.append("grounded_answer_missing")
                    result = search_knowledge(db, scenario.text[language], language)
                    if not result.chunks or result.chunks[0].document_key != scenario.source:
                        errors.append("wrong_source")
                elif scenario.name == "uncertainty":
                    if dialogue.draft is not None:
                        errors.append("unexpected_intake")
                else:
                    draft = dialogue.draft
                    if not response.needs_ticket_consent:
                        errors.append("consent_not_requested")
                    if not draft or draft.issue_type != scenario.issue_type:
                        errors.append(f"issue_type={draft.issue_type if draft else None}")
                    if not draft or draft.priority != scenario.urgency:
                        errors.append(f"urgency={draft.priority if draft else None}")
                if "24/7 human" in response.answer.lower():
                    errors.append("claims_24_7_human_support")
                if errors:
                    failures.append(
                        {"language": language, "scenario": scenario.name, "errors": errors}
                    )

        passed = len(SCENARIOS) * 3 - sum(
            failure["scenario"] != "provider_path" for failure in failures
        )
        usage = summarize_events(runner.events, input_price, output_price)
        estimated_cost = usage["measured_cost_usd"]
        return {
            **evaluation_metadata(settings),
            "mode": mode,
            "model": settings.llm_model if mode == "live" else "disabled",
            "scenarios": len(SCENARIOS) * 3,
            "denominators": {"behavioral_scenarios": len(SCENARIOS) * 3},
            "passed": passed,
            "pass_rate": round(passed / (len(SCENARIOS) * 3), 4),
            "p95_seconds": usage["p95_seconds"],
            "inbound_turns": usage["inbound_turns"],
            "provider_calls": usage["model_attempts"],
            "provider_successes": usage["model_successes"],
            "provider_timeouts": usage["model_timeouts"],
            "provider_timeout_rate": usage["provider_timeout_rate"],
            "business_tool_calls": usage["tool_calls"],
            "fallbacks": usage["fallbacks"],
            "intentional_local_controls": usage["intentional_local_controls"],
            "agent_executions": usage["agent_executions"],
            "agent_failure_fallbacks": usage["agent_failure_fallbacks"],
            "agent_failure_fallback_rate": usage["agent_failure_fallback_rate"],
            "agent_errors": usage["agent_errors"],
            "agent_error_codes": usage["agent_error_codes"],
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "reported_reasoning_tokens": usage["reasoning_tokens"],
            "reasoning_usage_missing": usage["reasoning_usage_missing"],
            "estimated_cost_usd": round(estimated_cost, 6),
            "conservative_cost_usd": usage["conservative_cost_usd"],
            "untrusted_usage_events": usage["untrusted_usage_events"],
            "usage_bounded_events": usage["usage_bounded_events"],
            "usage_unbounded_events": usage["usage_unbounded_events"],
            "usage_bounded": usage["usage_bounded"],
            "reserved_cost_usd": usage["reserved_cost_usd"],
            "input_price_per_million_usd": input_price,
            "output_price_per_million_usd": output_price,
            "price_source": price_source,
            "price_checked": price_checked,
            "thresholds": {
                "pass_rate_at_least_0_95": passed / (len(SCENARIOS) * 3) >= 0.95,
                "p95_at_most_30_seconds": latencies[math.ceil(len(latencies) * 0.95) - 1]
                <= 30,
                "provider_timeout_rate_below_0_05": usage["provider_timeout_rate"] < 0.05,
                "agent_failure_fallback_rate_at_most_0_05": (
                    usage["agent_failure_fallback_rate"] is not None
                    and usage["agent_failure_fallback_rate"]
                    <= AGENT_FAILURE_FALLBACK_RATE_MAX
                ),
                "estimated_cost_at_most_usd": max_cost,
                "estimated_cost_within_limit": estimated_cost <= max_cost,
                "conservative_cost_within_limit": usage["conservative_cost_usd"] <= max_cost,
                "usage_complete": usage["untrusted_usage_events"] == 0,
                "usage_bounded": usage["usage_bounded"],
            },
            "failures": failures,
        }
    finally:
        db.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the trilingual release evaluation")
    parser.add_argument("--suite", choices=("smart", "legacy"), default="smart")
    parser.add_argument("--intake-only", action="store_true", help="Run SMART handoff and intake diagnostics only")
    parser.add_argument("--mode", choices=("outage", "live"), default="outage")
    parser.add_argument(
        "--input-price", type=float, default=0, help="USD per million input tokens"
    )
    parser.add_argument(
        "--output-price", type=float, default=0, help="USD per million output tokens"
    )
    parser.add_argument("--price-source", help="URL or invoice identifying the verified rates")
    parser.add_argument("--price-checked", help="Rate verification date (YYYY-MM-DD)")
    parser.add_argument(
        "--max-cost", type=float, default=15, help="Maximum evaluation cost in USD"
    )
    parser.add_argument(
        "--matrix", type=Path, help="Alternate TSV matrix for the SMART suite"
    )
    parser.add_argument(
        "--all-held-out", action="store_true", help="Mark every alternate SMART matrix row as held out"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Report path (default: docs/evaluation/langchain-<mode>.json)",
    )
    parser.add_argument(
        "--reviews", type=Path, help="Human semantic-review JSON for these exact answers"
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Saved SMART report whose exact answers receive --reviews without provider calls",
    )
    parser.add_argument(
        "--case-id",
        "--case-ids",
        dest="case_ids",
        action="append",
        default=[],
        help="SMART case ID to run (repeat for a focused selection)",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="Durable SMART checkpoint path; completed cases are resumable",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the SMART run from its checkpoint",
    )
    parser.add_argument(
        "--selection-manifest",
        type=Path,
        help="Write the resolved SMART case selection manifest here",
    )
    args = parser.parse_args()
    if any(
        not math.isfinite(value) or value < 0
        for value in (args.input_price, args.output_price, args.max_cost)
    ):
        raise SystemExit("prices and max-cost must be finite and non-negative")
    if not args.report and args.mode == "live" and min(args.input_price, args.output_price) <= 0:
        raise SystemExit("live mode requires the current positive input and output prices")
    if not args.report and args.mode == "live" and not (args.price_source and args.price_checked):
        raise SystemExit("live mode requires --price-source and --price-checked")
    if args.report and (args.suite != "smart" or not args.reviews):
        raise SystemExit("--report requires --suite smart and --reviews")
    if args.report and (args.case_ids or args.checkpoint or args.resume):
        raise SystemExit("--report cannot be combined with case selection or resume")
    output = args.output or args.report or ROOT / "docs/evaluation" / f"langchain-{args.mode}.json"
    preflight_output_path(output)
    checkpoint = args.checkpoint
    if args.resume and checkpoint is None:
        checkpoint = output.with_name(output.name + ".checkpoint.json")
    if checkpoint:
        preflight_output_path(checkpoint)
    selection_manifest_path = args.selection_manifest
    if args.case_ids and selection_manifest_path is None:
        selection_manifest_path = output.with_name(output.name + ".selection.json")
    if selection_manifest_path:
        preflight_output_path(selection_manifest_path)
    if args.suite == "smart":
        from scripts.smart_eval import run_smart
        reviews = None
        if args.reviews:
            review_artifact = json.loads(args.reviews.read_text(encoding="utf-8"))
            reviews = {
                row["id"]: row
                for row in [
                    *review_artifact["records"],
                    *review_artifact.get("focused_dialogues", []),
                ]
            }
        if args.report:
            from scripts.smart_eval import apply_reviews

            report = apply_reviews(
                json.loads(args.report.read_text(encoding="utf-8")), reviews, args.max_cost
            )
        else:
            report = run_smart(
                args.mode, input_price=args.input_price, output_price=args.output_price,
                max_cost=args.max_cost, intake_only=args.intake_only, matrix_path=args.matrix,
                all_held_out=args.all_held_out, price_source=args.price_source,
                price_checked=args.price_checked, reviews=reviews,
                case_ids=[
                    case_id.strip()
                    for value in args.case_ids
                    for case_id in value.split(",")
                    if case_id.strip()
                ],
                checkpoint_path=checkpoint,
                resume=args.resume,
                selection_manifest_path=selection_manifest_path,
            )
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        atomic_write_json(output, report)
        if selection_manifest_path and "selection" in report:
            atomic_write_json(selection_manifest_path, report["selection"])
        print(rendered)
        if report["mode"] == "live" and not report["rollout_ready"]:
            raise SystemExit(1)
        return
    report = run(
        args.mode,
        input_price=args.input_price,
        output_price=args.output_price,
        max_cost=args.max_cost,
        price_source=args.price_source,
        price_checked=args.price_checked,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        atomic_write_json(args.output, report)
    print(rendered)
    thresholds = report["thresholds"]
    if report["failures"] or not all(
        (
            thresholds["pass_rate_at_least_0_95"],
            thresholds["p95_at_most_30_seconds"],
            thresholds["provider_timeout_rate_below_0_05"],
            thresholds["agent_failure_fallback_rate_at_most_0_05"],
            thresholds["estimated_cost_within_limit"],
            thresholds["conservative_cost_within_limit"],
            thresholds["usage_bounded"],
        )
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
