"""Synthetic trilingual SMART evaluation; human semantic review is a separate denominator."""
import csv
import json
import math
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
from app.services.answer_generation import ApprovedKnowledgeResponder, ZAIProvider
from app.services.chatbot import ChatbotService
from scripts.ingest_seed import ingest_seed
from scripts.release_eval import RecordingProvider, OutageProvider, SCENARIOS


MATRIX = Path(__file__).resolve().parents[1] / "data/evaluation/smart-non-escalation.tsv"


def run_smart(mode="outage", *, input_price=0.15, output_price=0.50, reviews=None, languages=None,
              intake_only=False, matrix_path=None, all_held_out=False):
    matrix = Path(matrix_path) if matrix_path else MATRIX
    if languages is None:
        with ThreadPoolExecutor(max_workers=3) as pool:
            parts = list(pool.map(lambda language: run_smart(
                mode, input_price=input_price, output_price=output_price, reviews=reviews,
                languages=(language,), intake_only=intake_only, matrix_path=matrix,
                all_held_out=all_held_out), ("en", "ms", "zh")))
        report = dict(parts[0])
        for key in ("records", "necessary_cases", "intake_sessions"):
            report[key] = [row for part in parts for row in part[key]]
        report["language_metrics"] = {language: metrics for part in parts for language, metrics in part["language_metrics"].items()}
        for key in ("translated_sessions", "unintended_mutations", "unexpected_offers", "held_out_total", "held_out_passed", "provider_calls", "provider_successes", "prompt_tokens", "completion_tokens", "reported_reasoning_tokens", "reasoning_usage_missing", "measured_cost_usd", "ticket_count", "notifications"):
            report[key] = sum(part[key] for part in parts)
        report["family_failures"] = dict(Counter(row["family"] for row in report["records"] if not row["non_escalating"]))
        latency = sorted(row["latency_seconds"] for row in report["records"])
        report["p95_seconds"] = latency[math.ceil(len(latency)*.95)-1] if latency else None
        report["projected_10000_calls_usd"] = round(report["measured_cost_usd"] / max(report["provider_calls"], 1) * 10000, 3) if mode == "live" else None
        return report
    with matrix.open() as source:
        scenarios = list(csv.DictReader(source, delimiter="\t"))
    assert scenarios and len({row["en"] for row in scenarios}) == len(scenarios)
    if matrix == MATRIX:
        assert len(scenarios) >= 100
    settings = Settings(_env_file=None, llm_customer_context_enabled=True)
    if mode == "live":
        settings = Settings().model_copy(update={"llm_customer_context_enabled": True})
        if not settings.zai_api_key:
            raise RuntimeError("Live evaluation requires the configured Z.AI API key")
        if input_price <= 0 or output_price <= 0:
            raise ValueError("Verified positive billing rates are required for live evaluation")
    class BudgetedProvider(RecordingProvider):
        def generate(self, messages, **kwargs):
            if (self.prompt_tokens * input_price + self.completion_tokens * output_price) / 1e6 >= .25:
                from app.services.answer_generation import ProviderError
                raise ProviderError("evaluation_budget_reached")
            return super().generate(messages, **kwargs)
    provider = BudgetedProvider(ZAIProvider(settings.zai_api_key, settings.llm_model) if mode == "live" else OutageProvider())
    service = ChatbotService()
    service.answer_generator = ApprovedKnowledgeResponder(settings, provider)
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    records, latencies = [], []
    with sessions() as db:
        cco = AdminUser(username="synthetic-evaluation", display_name="Synthetic fixture", email="fixture@example.invalid", password_hash="unused", totp_secret_ref="unused", is_cco=True)
        db.add(cco)
        db.commit()
        ingest_seed(db, cco)
        for language in languages:
            for index, scenario in enumerate([] if intake_only else scenarios):
                # Stop on observed spend, retaining a generous per-request margin below USD 1.
                if (provider.prompt_tokens * input_price + provider.completion_tokens * output_price) / 1e6 >= 0.25:
                    raise RuntimeError("Synthetic evaluation spend guard reached")
                key = f"{language}-{index:03}"
                started = time.monotonic()
                before_calls = provider.calls
                response = service.handle(db, ChatRequest(channel="whatsapp", external_user_id=f"synthetic-{key}", text=scenario[language], preferred_language=language))
                latency = time.monotonic() - started
                latencies.append(latency)
                conversation = db.query(Conversation).filter_by(external_user_id=f"synthetic-{key}").one()
                offered = bool(conversation.intake_data.get("pending_offer"))
                mutation = conversation.intake_state != "idle" or conversation.risk_level != "normal"
                review = (reviews or {}).get(key, {})
                records.append(dict(id=key, family=scenario["family"], language=language,
                    held_out=all_held_out or index % 5 == 4,
                    question=scenario[language], answer=response.answer, sources=response.sources,
                    stage=conversation.intake_state, ticket_offered=offered, unintended_mutation=mutation,
                    non_escalating=not mutation and not offered and response.ticket is None,
                    provider_calls=provider.calls-before_calls, latency_seconds=round(latency, 3),
                    answer_correct=review.get("answer_correct"), grounded_relevant=review.get("grounded_relevant")))
                if mode == "live" and (index + 1) % 25 == 0:
                    print(f"SMART live {language}: {index + 1}/{len(scenarios)} non-escalation sessions", file=sys.stderr, flush=True)
        necessary = []
        for scenario in SCENARIOS:
            if scenario.name not in {"human", "safety", "fraud", "complaint", "business_partner", "prohibited_action"}:
                continue
            for language in languages:
                response = service.handle(db, ChatRequest(channel="whatsapp", external_user_id=f"necessary-{language}-{scenario.name}", text=scenario.text[language], preferred_language=language))
                necessary.append(dict(language=language, scenario=scenario.name, passed=response.needs_ticket_consent and response.ticket is None))
        intakes = []
        for language in languages:
            for index in range(10):
                user = f"intake-{language}-{index}"
                turns = {
                    "en": ["I need a human", "Yes", "Alex", "alex@example.com", "My ride receipt is missing", "Skip"],
                    "ms": ["Saya mahu pegawai manusia", "Ya", "Ali", "ali@example.com", "Resit perjalanan saya tiada", "Langkau"],
                    "zh": ["我要人工客服", "同意", "小陈", "chen@example.com", "我的行程收据找不到了", "跳过"],
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
                for turn_index, turn in enumerate(turns):
                    response = service.handle(db, ChatRequest(channel="whatsapp", external_user_id=user, text=turn, preferred_language=language, phone_number="+60123456789" if turn_index == 1 else None))
                    state = db.query(Conversation).filter_by(external_user_id=user).one()
                    trace.append({"question": turn, "answer": response.answer, "stage": state.intake_state})
                if not response.ticket:
                    response = service.handle(db, ChatRequest(channel="whatsapp", external_user_id=user, text={"en":"Submit", "ms":"Hantar", "zh":"提交"}[language], preferred_language=language))
                ticket = db.query(Ticket).filter_by(public_id=response.ticket.public_id).first() if response.ticket else None
                expected_name = "Jamie" if index == 5 else "Alex" if index == 1 else {"en":"Alex", "ms":"Ali", "zh":"小陈"}[language]
                expected_email = "corrected@example.com" if index == 4 else "alex@example.com" if index == 1 else {"en":"alex@example.com", "ms":"ali@example.com", "zh":"chen@example.com"}[language]
                expected_phone = "+60198765432" if index == 3 else "+60123456789"
                completed = bool(ticket and (ticket.name, ticket.email, ticket.phone_number) == (expected_name, expected_email, expected_phone))
                intakes.append(dict(language=language, scenario=index, completed=completed, ticket_created=bool(ticket), trace=trace))
        ticket_count = db.query(Ticket).count()
        notifications = db.query(SupportNotification).count()
    language_metrics = {}
    for language in languages:
        rows = [r for r in records if r["language"] == language]
        reviewed = [r for r in rows if r["answer_correct"] is not None]
        answerable = [r for r in rows if r["grounded_relevant"] is not None]
        language_metrics[language] = dict(
            non_escalation_total=len(rows), non_escalation_passed=sum(r["non_escalating"] for r in rows),
            semantic_reviewed=len(reviewed), answer_correct=sum(r["answer_correct"] is True for r in reviewed),
            answerable_reviewed=len(answerable), grounded_relevant=sum(r["grounded_relevant"] is True for r in answerable),
            necessary_total=sum(r["language"]==language for r in necessary),
            necessary_passed=sum(r["language"]==language and r["passed"] for r in necessary),
            intake_total=10, intake_completed=sum(r["language"]==language and r["completed"] for r in intakes),
        )
    cost = (provider.prompt_tokens*input_price + provider.completion_tokens*output_price)/1e6
    p95 = sorted(latencies)[math.ceil(len(latencies)*.95)-1] if latencies else None
    return dict(mode=mode, distinct_non_escalation_scenarios=0 if intake_only else len(scenarios), translated_sessions=len(records),
        cco_review_status="pending", distinct_intake_scenarios=10, language_metrics=language_metrics,
        unintended_mutations=sum(r["unintended_mutation"] for r in records),
        unexpected_offers=sum(r["ticket_offered"] for r in records),
        family_failures=dict(Counter(r["family"] for r in records if not r["non_escalating"])),
        held_out_total=sum(r["held_out"] for r in records),
        held_out_passed=sum(r["held_out"] and r["non_escalating"] for r in records),
        p95_seconds=round(p95,3) if p95 is not None else None, provider_calls=provider.calls, provider_successes=provider.successes,
        prompt_tokens=provider.prompt_tokens, completion_tokens=provider.completion_tokens,
        reported_reasoning_tokens=provider.reasoning_tokens, reasoning_usage_missing=provider.reasoning_usage_missing,
        measured_cost_usd=round(cost,6), projected_10000_calls_usd=round(cost/max(provider.calls,1)*10000,3) if mode=="live" else None,
        price_source="https://docs.z.ai/guides/overview/pricing", price_checked="2026-09-10",
        ticket_count=ticket_count, notifications=notifications, necessary_cases=necessary,
        intake_sessions=intakes, records=records, rollout_ready=False)
