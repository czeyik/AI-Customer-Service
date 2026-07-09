from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AdminUser, Ticket
from app.security import make_session_token, read_session_token, verify_password, verify_totp

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("dudu_admin_session")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    data = read_session_token(token)
    if not data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    admin = db.query(AdminUser).filter(AdminUser.username == data["username"]).first()
    if not admin or not admin.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive admin")
    return admin


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})


@router.post("/login", response_model=None)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    totp_code: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    admin = db.query(AdminUser).filter(AdminUser.username == username).first()
    if not admin or not verify_password(password, admin.password_hash) or not verify_totp(totp_code):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": "Invalid username, password, or 2FA code."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        "dudu_admin_session",
        make_session_token(username),
        httponly=True,
        samesite="lax",
        secure=get_settings().is_production,
        max_age=60 * 60 * 12,
    )
    return response


@router.get("/logout", response_model=None)
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("dudu_admin_session")
    return response


@router.get("", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
) -> HTMLResponse:
    tickets = db.query(Ticket).order_by(Ticket.created_at.desc()).limit(100).all()
    return templates.TemplateResponse("admin_dashboard.html", {"request": request, "tickets": tickets})


@router.get("/tickets/{public_id}", response_class=HTMLResponse)
def ticket_detail(
    public_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
) -> HTMLResponse:
    ticket = db.query(Ticket).filter(Ticket.public_id == public_id).first()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    return templates.TemplateResponse("admin_ticket_detail.html", {"request": request, "ticket": ticket})

