from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db
from app.routers import admin, chat, health, knowledge, webhooks_meta

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(webhooks_meta.router)
app.include_router(admin.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()

