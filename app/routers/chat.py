from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ChatRequest, ChatResponse
from app.services.chatbot import chatbot_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(request_data: ChatRequest, request: Request, db: Session = Depends(get_db)) -> ChatResponse:
    client_ip = request.client.host if request.client else None
    return chatbot_service.handle(db, request_data, client_ip)

