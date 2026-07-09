from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import AdminUser, Base

settings = get_settings()

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        ensure_admin_user(db)


def ensure_admin_user(db: Session) -> None:
    from app.security import hash_password

    existing = db.query(AdminUser).filter(AdminUser.username == settings.admin_username).first()
    if existing:
        return
    db.add(
        AdminUser(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_initial_password),
        )
    )
    db.commit()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

