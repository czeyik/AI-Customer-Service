import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
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
from app.services.answer_generation import (
    ApprovedKnowledgeResponder,
    ProviderError,
    ProviderResponse,
    ZAIProvider,
)
from app.services.chatbot import ChatbotService
from app.services.retrieval import search_knowledge
from scripts.ingest_seed import ingest_seed


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


class RecordingProvider:
    def __init__(self, provider) -> None:
        self.provider = provider
        self.name = provider.name
        self.model = provider.model
        self.calls = 0
        self.successes = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def generate(self, messages, *, max_output_tokens, timeout_seconds) -> ProviderResponse:
        self.calls += 1
        response = self.provider.generate(
            messages,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        self.successes += 1
        self.prompt_tokens += response.prompt_tokens
        self.completion_tokens += response.completion_tokens
        return response


class OutageProvider:
    name = "outage"
    model = "unavailable"

    def generate(self, messages, *, max_output_tokens, timeout_seconds) -> ProviderResponse:
        raise ProviderError("release_evaluation_outage")


def run(
    mode: str, *, input_price: float = 0, output_price: float = 0, max_cost: float = 15
) -> dict:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    provider = None
    try:
        cco = AdminUser(
            username="wave12-cco",
            display_name="Wave 12 CCO",
            email="wave12-cco@example.invalid",
            password_hash="not-used",
            totp_secret_ref="release-evaluation/not-used",
            is_cco=True,
        )
        db.add(cco)
        db.commit()
        ingest_seed(db, cco)

        service = ChatbotService()
        settings = Settings(_env_file=None)
        if mode == "live":
            live = Settings()
            if not live.zai_api_key or live.llm_model != "glm-5.3-flash":
                raise RuntimeError(
                    "live mode requires the approved glm-5.3-flash API configuration"
                )
            provider = RecordingProvider(ZAIProvider(live.zai_api_key, live.llm_model))
            service.answer_generator = ApprovedKnowledgeResponder(live, provider)
        else:
            provider = RecordingProvider(OutageProvider())
            service.answer_generator = ApprovedKnowledgeResponder(settings, provider)

        failures = []
        latencies = []
        for language in ("en", "ms", "zh"):
            for scenario in SCENARIOS:
                started = time.monotonic()
                response = service.handle(
                    db,
                    ChatRequest(
                        channel="whatsapp",
                        external_user_id=f"wave12-{mode}-{language}-{scenario.name}",
                        text=scenario.text[language],
                        preferred_language=language,
                        user_role=scenario.role,
                        phone_number="+60182935060",
                    ),
                )
                latencies.append(time.monotonic() - started)
                conversation = (
                    db.query(Conversation)
                    .filter_by(external_user_id=f"wave12-{mode}-{language}-{scenario.name}")
                    .one()
                )
                errors = []
                if response.language != language:
                    errors.append(f"language={response.language}")
                if scenario.source:
                    if not response.sources or conversation.intake_state != "idle":
                        errors.append("grounded_answer_missing")
                    result = search_knowledge(db, scenario.text[language], language)
                    if not result.chunks or result.chunks[0].document_key != scenario.source:
                        errors.append("wrong_source")
                else:
                    data = conversation.intake_data or {}
                    if not response.needs_ticket_consent:
                        errors.append("consent_not_requested")
                    if data.get("issue_type") != scenario.issue_type:
                        errors.append(f"issue_type={data.get('issue_type')}")
                    if data.get("urgency") != scenario.urgency:
                        errors.append(f"urgency={data.get('urgency')}")
                if "24/7 human" in response.answer.lower():
                    errors.append("claims_24_7_human_support")
                if errors:
                    failures.append(
                        {"language": language, "scenario": scenario.name, "errors": errors}
                    )

        expected_provider_calls = sum(scenario.source is not None for scenario in SCENARIOS) * 3
        expected_successes = expected_provider_calls if mode == "live" else 0
        if provider.calls != expected_provider_calls or provider.successes != expected_successes:
            failures.append(
                {
                    "language": "all",
                    "scenario": "provider_path",
                    "errors": [
                        f"calls={provider.calls}/{expected_provider_calls}",
                        f"successes={provider.successes}/{expected_successes}",
                    ],
                }
            )
        passed = len(SCENARIOS) * 3 - sum(
            failure["scenario"] != "provider_path" for failure in failures
        )
        latencies.sort()
        estimated_cost = (
            provider.prompt_tokens * input_price + provider.completion_tokens * output_price
        ) / 1_000_000
        return {
            "mode": mode,
            "model": provider.model,
            "scenarios": len(SCENARIOS) * 3,
            "passed": passed,
            "pass_rate": round(passed / (len(SCENARIOS) * 3), 4),
            "p95_seconds": round(latencies[math.ceil(len(latencies) * 0.95) - 1], 3),
            "provider_calls": provider.calls,
            "provider_successes": provider.successes,
            "prompt_tokens": provider.prompt_tokens,
            "completion_tokens": provider.completion_tokens,
            "estimated_cost_usd": round(estimated_cost, 6),
            "thresholds": {
                "pass_rate_at_least_0_95": passed / (len(SCENARIOS) * 3) >= 0.95,
                "p95_at_most_30_seconds": latencies[math.ceil(len(latencies) * 0.95) - 1]
                <= 30,
                "estimated_cost_at_most_usd": max_cost,
                "estimated_cost_within_limit": estimated_cost <= max_cost,
            },
            "failures": failures,
        }
    finally:
        db.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Wave 12 trilingual release evaluation")
    parser.add_argument("--mode", choices=("outage", "live"), default="outage")
    parser.add_argument(
        "--input-price", type=float, default=0, help="USD per million input tokens"
    )
    parser.add_argument(
        "--output-price", type=float, default=0, help="USD per million output tokens"
    )
    parser.add_argument(
        "--max-cost", type=float, default=15, help="Maximum evaluation cost in USD"
    )
    args = parser.parse_args()
    if min(args.input_price, args.output_price, args.max_cost) < 0:
        raise SystemExit("prices cannot be negative")
    if args.mode == "live" and min(args.input_price, args.output_price) <= 0:
        raise SystemExit("live mode requires the current positive input and output prices")
    report = run(
        args.mode,
        input_price=args.input_price,
        output_price=args.output_price,
        max_cost=args.max_cost,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    thresholds = report["thresholds"]
    if report["failures"] or not all(
        (
            thresholds["pass_rate_at_least_0_95"],
            thresholds["p95_at_most_30_seconds"],
            thresholds["estimated_cost_within_limit"],
        )
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
