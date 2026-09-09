import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AdminUser, AuditLog, MediaAttachment, SupportNotification, Ticket
from app.security import make_session_token, read_session_token, verify_password, verify_totp
from app.services.ticket_operations import add_ticket_note, assign_ticket, update_ticket_status
from app.services.media import PrivateObjectStore
from app.services.rate_limit import rate_limiter

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("dudu_admin_session")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    data = read_session_token(token)
    if not data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    admin = db.get(AdminUser, data["admin_id"])
    if not admin or not admin.is_active or admin.auth_version != data["auth_version"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive admin")
    return admin


def require_csrf(request: Request, csrf_token: str) -> None:
    data = read_session_token(request.cookies.get("dudu_admin_session", ""))
    if not data or not secrets.compare_digest(data["csrf"], csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "admin_login.html", {"request": request, "error": None}
    )


@router.post("/login", response_model=None)
def login(
    request: Request,
    username: Annotated[str, Form(min_length=1, max_length=120)],
    password: Annotated[str, Form(min_length=1, max_length=1024)],
    totp_code: Annotated[str, Form(pattern=r"^\d{6}$")],
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    limiter_keys = (f"admin-login-ip:{client_ip}", f"admin-login-user:{username.lower()}")
    if not all(
        rate_limiter.allow(
            db,
            key,
            settings.rate_limit_admin_attempts,
            window_seconds=settings.rate_limit_admin_window_seconds,
            max_keys=settings.rate_limit_max_keys,
        )
        for key in limiter_keys
    ):
        db.add(
            AuditLog(
                actor=username[:120],
                event_type="admin_login_rate_limited",
                ip_address=request.client.host if request.client else None,
                details={},
            )
        )
        db.commit()
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {"request": request, "error": "Too many sign-in attempts. Try again later."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    admin = db.query(AdminUser).filter(AdminUser.username == username).first()
    valid = bool(
        admin
        and admin.is_active
        and verify_password(password, admin.password_hash)
        and verify_totp(admin.totp_secret_ref, totp_code)
    )
    db.add(
        AuditLog(
            actor=admin.username if admin else username[:120],
            event_type="admin_login_succeeded" if valid else "admin_login_failed",
            ip_address=request.client.host if request.client else None,
            details={"admin_id": admin.id} if admin else {},
        )
    )
    db.commit()
    if not valid:
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {"request": request, "error": "Invalid username, password, or 2FA code."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        "dudu_admin_session",
        make_session_token(admin.id, admin.auth_version, secrets.token_urlsafe(32)),
        httponly=True,
        samesite="lax",
        secure=get_settings().is_production,
        max_age=60 * 60 * 12,
    )
    return response


@router.post("/logout", response_model=None)
def logout(
    request: Request,
    csrf_token: str = Form(...),
    admin: AdminUser = Depends(get_current_admin),
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(
        "dudu_admin_session",
        httponly=True,
        samesite="lax",
        secure=get_settings().is_production,
    )
    return response


@router.get("", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> HTMLResponse:
    session = read_session_token(request.cookies["dudu_admin_session"])
    tickets = db.query(Ticket).order_by(Ticket.created_at.desc()).limit(100).all()
    notifications = (
        db.query(SupportNotification)
        .filter_by(recipient_admin_id=admin.id, status="pending")
        .order_by(SupportNotification.created_at.desc())
        .limit(20)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "request": request,
            "tickets": tickets,
            "notifications": notifications,
            "csrf": session["csrf"],
        },
    )


@router.get("/tickets/{public_id}", response_class=HTMLResponse)
def ticket_detail(
    public_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> HTMLResponse:
    ticket = db.query(Ticket).filter(Ticket.public_id == public_id).first()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    session = read_session_token(request.cookies["dudu_admin_session"])
    admins = db.query(AdminUser).filter_by(is_active=True).order_by(AdminUser.display_name).all()
    return templates.TemplateResponse(
        request,
        "admin_ticket_detail.html",
        {
            "request": request,
            "ticket": ticket,
            "admins": admins,
            "admin": admin,
            "csrf": session["csrf"],
        },
    )


@router.get("/media/{attachment_id}", response_model=None)
def review_media(
    attachment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RedirectResponse:
    attachment = db.get(MediaAttachment, attachment_id)
    if (
        not attachment
        or attachment.status != "approved"
        or not attachment.ticket_id
        or not attachment.object_key
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    url = PrivateObjectStore().signed_url(attachment.object_key)
    db.add(
        AuditLog(
            actor=admin.username,
            event_type="media_review_link_issued",
            ip_address=request.client.host if request.client else None,
            subject_type="attachment",
            subject_id=attachment.id,
            details={"attachment_id": attachment.id, "ticket_id": attachment.ticket_id},
        )
    )
    db.commit()
    return RedirectResponse(url, status_code=303)


def _ticket_or_404(db: Session, public_id: str) -> Ticket:
    ticket = db.query(Ticket).filter_by(public_id=public_id).first()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    return ticket


@router.post("/tickets/{public_id}/assign", response_model=None)
def assign(
    public_id: str,
    request: Request,
    assignee_id: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    assignee = db.get(AdminUser, assignee_id)
    if not assignee:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid assignee")
    assign_ticket(db, ticket=_ticket_or_404(db, public_id), assignee=assignee, actor=admin)
    return RedirectResponse(f"/admin/tickets/{public_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/tickets/{public_id}/status", response_model=None)
def change_status(
    public_id: str,
    request: Request,
    new_status: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    try:
        update_ticket_status(
            db, ticket=_ticket_or_404(db, public_id), new_status=new_status, actor=admin
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RedirectResponse(f"/admin/tickets/{public_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/tickets/{public_id}/notes", response_model=None)
def add_note(
    public_id: str,
    request: Request,
    body: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    try:
        add_ticket_note(db, ticket=_ticket_or_404(db, public_id), body=body, actor=admin)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RedirectResponse(f"/admin/tickets/{public_id}", status_code=status.HTTP_303_SEE_OTHER)
