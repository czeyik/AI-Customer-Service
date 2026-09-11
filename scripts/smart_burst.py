"""Run a synthetic eight-sender burst against a disposable PostgreSQL database."""
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.config import Settings
from app.models import WhatsAppInboundMessage, WhatsAppOutboundMessage
from app.services import inbound
from app.services.answer_generation import ApprovedKnowledgeResponder, ProviderResponse
from app.services.chatbot import ChatbotService


def main():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    sessions = sessionmaker(bind=engine, autoflush=False)
    prefix = "synthetic-burst-" + uuid.uuid4().hex
    class Provider:
        name = "synthetic-latency"
        model = "two-second-response"
        def generate(self, messages, **kwargs):
            time.sleep(2)
            return ProviderResponse(json.dumps({"disposition":"clarify", "answer":"Which service do you mean?", "citations":[]}))
    service = ChatbotService()
    service.answer_generator = ApprovedKnowledgeResponder(Settings(_env_file=None, llm_customer_context_enabled=True), Provider())
    inbound.chatbot_service = service
    with sessions() as db:
        for index in range(8):
            db.add(WhatsAppInboundMessage(provider_message_id=f"{prefix}-{index}", sender=f"99{uuid.uuid4().int % 10**12:012d}", phone_number_id="synthetic", message_type="text", payload={"text":"Which service can help me?"}))
        db.commit()
    started=time.monotonic()
    while inbound.process_inbox(sessions):
        pass
    elapsed=time.monotonic()-started
    with sessions() as db:
        rows=db.query(WhatsAppInboundMessage, WhatsAppOutboundMessage).join(WhatsAppOutboundMessage, WhatsAppOutboundMessage.inbound_message_id==WhatsAppInboundMessage.id).filter(WhatsAppInboundMessage.provider_message_id.like(prefix+"%")).all()
        latency=sorted((outgoing.created_at-incoming.created_at).total_seconds() for incoming,outgoing in rows)
        assert len(rows)==8 and latency[-1]<30
        print(json.dumps({"senders":8,"workers":4,"simulated_provider_seconds":2,"reply_queued_p95_seconds":latency[-1],"total_seconds":elapsed,"duplicate_replies":len(rows)-8,"external_delivery":"not part of this synthetic queue check"}))


if __name__ == "__main__":
    main()
