"""Ledgerwise's protected reviewer dashboard."""
import asyncio
import logging
import os
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ai_reply import generate_reply, recoverable_handoff
from zoneinfo import ZoneInfo
from calcom_booking import Calcom, acceptance, offer_reply, handoff as booking_handoff, instant, reply as scheduling_reply, scheduling_request, slot_label
from gmail_inbox import sync_known_threads
from initial_outreach import generate_initial_draft
from outreach_delivery import deliver_test_email, gmail_settings, verify_edited_copy
from research_pilot import COMPANY_LIMIT, DEFAULT_COMPANY_COUNT, SIZE_TARGETS, is_social_host, run_pilot

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")
templates.env.globals["dashboard_css_version"] = sha256((ROOT / "static/dashboard.css").read_bytes()).hexdigest()[:12]
templates.env.globals["email_drafts_js_version"] = sha256((ROOT / "static/email_drafts.js").read_bytes()).hexdigest()[:12]
COOKIE = "ledgerwise_session"
SECTIONS = {
    "overview": "Overview",
    "companies": "Companies",
    "drafts": "Email drafts",
    "conversations": "Conversations",
    "meetings": "Meetings",
    "settings": "Settings",
}


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError):
    fields = [".".join(map(str, item["loc"])) for item in error.errors()]
    return JSONResponse({"detail": "Check the submitted fields.", "fields": fields}, status_code=422)

async def supabase(path, key_name, *, method="GET", token=None, headers_extra=None, **kwargs):
    base = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv(key_name, "").strip()
    if urlsplit(base).scheme != "https" or not key:
        raise HTTPException(503, "Dashboard configuration is incomplete.")
    headers = {"apikey": key, **(headers_extra or {})}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            return await client.request(method, f"{base}/{path}", headers=headers, **kwargs)
    except httpx.RequestError as error:
        raise HTTPException(503, "Supabase is temporarily unavailable.") from error


async def reviewer_profile(user_id):
    if not user_id:
        return None
    result = await supabase(
        "rest/v1/reviewers", "SUPABASE_SECRET_KEY",
        params={"select": "user_id,full_name,role", "user_id": f"eq.{user_id}", "active": "eq.true"},
    )
    if not result.is_success:
        raise HTTPException(503, "Reviewer access cannot be checked. Verify the reviewers migration.")
    rows = result.json()
    if len(rows) == 1 and rows[0]["role"] in {"admin", "reviewer"}:
        return rows[0]
    return None


def render(request, template, *, status=200, **context):
    return templates.TemplateResponse(
        request=request, name=template, context=context, status_code=status,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin",
                 "Content-Security-Policy": "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"},
    )


def app_origin():
    return os.getenv("APP_BASE_URL", "").strip().rstrip("/") or "http://127.0.0.1:8000"


def check_form_origin(request):
    if request.headers.get("origin") != app_origin():
        raise HTTPException(403, "Submit the form from Ledgerwise.")


@app.get("/")
def home():
    return RedirectResponse("/dashboard/overview", status_code=303)


@app.get("/about")
def about_page(request: Request):
    verification = os.getenv("GOOGLE_SITE_VERIFICATION", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", verification):
        verification = ""
    return render(request, "public_branding.html", page="About", verification=verification)


@app.get("/privacy")
def privacy_page(request: Request):
    return render(request, "public_branding.html", page="Privacy Policy")


@app.get("/terms")
def terms_page(request: Request):
    return render(request, "public_branding.html", page="Terms of Service")


@app.get("/login")
def login_page(request: Request, password_saved: bool = False):
    return render(request, "login.html", password_saved=password_saved)


@app.post("/login")
async def login(request: Request, email: str = Form(), password: str = Form()):
    check_form_origin(request)
    result = await supabase(
        "auth/v1/token", "SUPABASE_PUBLISHABLE_KEY", method="POST",
        params={"grant_type": "password"}, json={"email": email.strip(), "password": password},
    )
    if result.status_code in (400, 401, 403):
        return render(request, "login.html", status=401, error="Sign-in failed. Check your reviewer credentials.")
    if result.status_code == 429:
        return render(request, "login.html", status=429, error="Too many sign-in attempts. Try again shortly.")
    if not result.is_success:
        raise HTTPException(503, "Sign-in is temporarily unavailable.")
    session = result.json()
    user_id = (session.get("user") or {}).get("id")
    if not await reviewer_profile(user_id) and not (
        user_id and await employee_invitation(user_id) and await complete_employee_invitation(user_id)
    ):
        return render(request, "login.html", status=403, error="This account does not have active Ledgerwise access.")
    response = RedirectResponse("/dashboard/overview", status_code=303)
    response.set_cookie(
        COOKIE, session["access_token"], max_age=session["expires_in"],
        httponly=True, secure=app_origin().startswith("https://"), samesite="strict",
    )
    return response


@app.post("/logout")
def logout(request: Request):
    check_form_origin(request)
    return sign_in_redirect()


def sign_in_redirect(location="/login"):
    response = RedirectResponse(location, status_code=303)
    response.delete_cookie(COOKIE, httponly=True, secure=app_origin().startswith("https://"), samesite="strict")
    return response


async def signed_in_account(request):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    result = await supabase("auth/v1/user", "SUPABASE_PUBLISHABLE_KEY", token=token)
    if result.status_code in (401, 403):
        return None
    if not result.is_success:
        raise HTTPException(503, "Reviewer verification is temporarily unavailable.")
    user = result.json()
    profile = await reviewer_profile(user.get("id"))
    return {**profile, "email": user["email"]} if profile else None


SETTINGS_NOTICES = {
    "invited": "Invitation sent.",
    "saved": "Employee changes saved.",
    "reset": "Password reset link requested.",
    "cancelled": "Invitation cancelled.",
    "deleted": "Employee account deleted.",
}


async def require_account(request, *, admin=False):
    account = await signed_in_account(request)
    if account is None:
        raise HTTPException(401, "Sign in to continue.")
    if admin and account["role"] != "admin":
        raise HTTPException(403, "Only admins can manage employees.")
    return account


async def employee_email(user_id):
    result = await supabase(f"auth/v1/admin/users/{user_id}", "SUPABASE_SECRET_KEY")
    if not result.is_success or not result.json().get("email"):
        raise HTTPException(503, "The employee's Auth email could not be loaded.")
    return result.json()["email"]


async def employee_list():
    result = await supabase(
        "rest/v1/reviewers", "SUPABASE_SECRET_KEY",
        params={"select": "user_id,full_name,phone,role,active", "order": "full_name.asc"},
    )
    if not result.is_success:
        raise HTTPException(503, "Employee access records are unavailable.")
    employees = result.json()
    for employee in employees:
        employee["email"] = await employee_email(employee["user_id"])
    return employees


def callback_params():
    return {"redirect_to": f"{app_origin()}/account/accept"}


def settings_redirect(notice):
    return RedirectResponse(f"/dashboard/settings?notice={notice}", status_code=303)


async def settings_page(request, account, *, edit=None, error=None, notice=None, status=200):
    employees = await employee_list() if account["role"] == "admin" else []
    invitations = await pending_invitations() if account["role"] == "admin" else []
    if edit and not any(str(edit) == employee["user_id"] for employee in employees):
        raise HTTPException(404, "Employee not found.")
    return render(request, "dashboard.html", status=status, section="settings", sections=SECTIONS,
                  account=account, employees=employees, invitations=invitations, edit=str(edit) if edit else None,
                  error=error, notice=SETTINGS_NOTICES.get(notice))


INVITATIONS_PATH = "rest/v1/pending_invitations"
INVITATION_HOURS = 1
SEND_RETRY_AFTER = timedelta(minutes=5)


async def pending_invitations():
    result = await supabase(
        INVITATIONS_PATH, "SUPABASE_SECRET_KEY",
        params={"status": "neq.accepted", "order": "last_attempt_at.desc",
                "select": "id,email,role,status,last_attempt_at,expires_at"},
    )
    if not result.is_success:
        raise HTTPException(503, "Pending invitations are unavailable. Check the employee lifecycle migration.")
    now = datetime.now(timezone.utc)
    invitations = result.json()
    for invitation in invitations:
        status = invitation["status"]
        if status == "awaiting" and datetime.fromisoformat(invitation["expires_at"]) <= now:
            status = "expired"
        if status == "sending" and datetime.fromisoformat(invitation["last_attempt_at"]) <= now - SEND_RETRY_AFTER:
            status = "interrupted"
        invitation["display_status"] = status
        invitation["can_resend"] = status in {"expired", "failed", "interrupted"}
    return invitations


async def change_invitation(filters, values):
    result = await supabase(
        INVITATIONS_PATH, "SUPABASE_SECRET_KEY", method="PATCH",
        params=filters, headers_extra={"Prefer": "return=representation"}, json=values,
    )
    if not result.is_success:
        raise HTTPException(503, "The invitation status could not be saved.")
    return result.json()


async def send_employee_invitation(invitation):
    filters = {"id": f"eq.{invitation['id']}", "status": "eq.sending",
               "last_attempt_at": f"eq.{invitation['last_attempt_at']}"}
    user_id = invitation.get("user_id")
    try:
        if user_id:
            account = await supabase(f"auth/v1/admin/users/{user_id}", "SUPABASE_SECRET_KEY")
            if not account.is_success or account.json().get("email", "").lower() != invitation["email"]:
                await change_invitation(filters, {"status": "failed"})
                return False
            confirmed = bool(account.json().get("email_confirmed_at"))
        else:
            confirmed = False

        path = "auth/v1/recover" if confirmed else "auth/v1/invite"
        key = "SUPABASE_PUBLISHABLE_KEY" if confirmed else "SUPABASE_SECRET_KEY"
        result = await supabase(path, key, method="POST", params=callback_params(),
                                json={"email": invitation["email"]})
        if not result.is_success:
            await change_invitation(filters, {"status": "failed"})
            return False
        if not confirmed:
            returned_id = result.json().get("id")
            if not returned_id or (user_id and returned_id != user_id):
                await change_invitation(filters, {"status": "failed"})
                return False
            user_id = returned_id
    except (HTTPException, ValueError, AttributeError):
        await change_invitation(filters, {"status": "failed"})
        return False

    expiry = datetime.now(timezone.utc) + timedelta(hours=INVITATION_HOURS)
    saved = await change_invitation(filters, {
        "user_id": user_id, "status": "awaiting", "expires_at": expiry.isoformat(),
    })
    return bool(saved)


@app.post("/settings/employees/invite")
async def invite_employee(
    request: Request, email: str = Form(max_length=254), full_name: str = Form(max_length=120),
    role: Literal["admin", "reviewer"] = Form(), phone: str = Form("", max_length=40),
):
    check_form_origin(request)
    admin = await require_account(request, admin=True)
    if not full_name.strip() or "@" not in email.strip():
        return await settings_page(request, admin, error="Enter a name and a valid email address.", status=400)
    result = await supabase(
        INVITATIONS_PATH, "SUPABASE_SECRET_KEY", method="POST",
        headers_extra={"Prefer": "return=representation"},
        json={"email": email.strip().lower(), "full_name": full_name.strip(),
              "phone": phone.strip() or None, "role": role, "status": "sending",
              "last_attempt_at": datetime.now(timezone.utc).isoformat()},
    )
    if result.status_code == 409:
        return await settings_page(request, admin, status=409,
                                   error="An invitation already exists for this address.")
    if not result.is_success:
        raise HTTPException(503, "The pending invitation could not be saved.")
    if not await send_employee_invitation(result.json()[0]):
        return await settings_page(request, admin, status=502,
                                   error="The invitation could not be completed. Check its status before retrying.")
    return settings_redirect("invited")


@app.post("/settings/invitations/{invitation_id}/resend")
async def resend_invitation(request: Request, invitation_id: UUID):
    check_form_origin(request)
    admin = await require_account(request, admin=True)
    now = datetime.now(timezone.utc)
    rows = await change_invitation({
        "id": f"eq.{invitation_id}",
        "or": f"(status.eq.failed,and(status.eq.awaiting,expires_at.lte.{now.isoformat()}),"
              f"and(status.eq.sending,last_attempt_at.lte.{(now - SEND_RETRY_AFTER).isoformat()}))",
    }, {"status": "sending", "last_attempt_at": now.isoformat(), "expires_at": None})
    if not rows:
        return await settings_page(request, admin, status=409,
                                   error="Only failed, expired, or interrupted invitations can be resent.")
    if not await send_employee_invitation(rows[0]):
        return await settings_page(request, admin, status=502,
                                   error="Resending failed. Check the invitation status and Auth email settings.")
    return settings_redirect("invited")


@app.post("/settings/invitations/{invitation_id}/cancel")
async def cancel_invitation(request: Request, invitation_id: UUID):
    check_form_origin(request)
    admin = await require_account(request, admin=True)
    rows = await change_invitation(
        {"id": f"eq.{invitation_id}", "status": "in.(sending,awaiting,failed)"},
        {"status": "cancelled"},
    )
    if not rows:
        return await settings_page(request, admin, status=409,
                                   error="This invitation can no longer be cancelled.")
    return settings_redirect("cancelled")


async def employee_invitation(user_id):
    result = await supabase(
        INVITATIONS_PATH, "SUPABASE_SECRET_KEY",
        params={"user_id": f"eq.{user_id}", "select": "status,expires_at"},
    )
    if not result.is_success:
        raise HTTPException(503, "The invitation could not be checked.")
    rows = result.json()
    if rows and rows[0]["status"] == "awaiting":
        expiry = rows[0]["expires_at"]
        return expiry is not None and datetime.fromisoformat(expiry) > datetime.now(timezone.utc)
    return False


async def complete_employee_invitation(user_id):
    result = await supabase(
        "rest/v1/rpc/complete_employee_invitation", "SUPABASE_SECRET_KEY",
        method="POST", json={"p_user": user_id},
    )
    if not result.is_success:
        raise HTTPException(503, "Employee setup could not be saved. Try signing in or contact your admin.")
    return result.json() in {"accepted", "already_accepted"}


@app.post("/settings/employees/{user_id}/delete")
async def delete_employee(request: Request, user_id: UUID, email: str = Form(max_length=254)):
    check_form_origin(request)
    admin = await require_account(request, admin=True)
    result = await supabase(
        "rest/v1/rpc/delete_employee", "SUPABASE_SECRET_KEY", method="POST",
        json={"p_actor": admin["user_id"], "p_user": str(user_id), "p_email": email.strip()},
    )
    if not result.is_success:
        return await settings_page(request, admin, edit=user_id, status=503,
                                   error="Deletion could not be confirmed. Refresh Settings before trying again.")
    outcome = result.json()
    if outcome == "forbidden":
        raise HTTPException(403, "Only active admins can delete employees.")
    if outcome == "not_found":
        raise HTTPException(404, "Employee not found.")
    if outcome != "deleted":
        error = {"last_admin": "The last usable admin cannot be deleted.",
                 "email_mismatch": "Enter the employee's current email to confirm deletion."}
        return await settings_page(request, admin, edit=user_id, status=409,
                                   error=error.get(outcome, "The employee could not be deleted."))
    if admin["user_id"] == str(user_id):
        return sign_in_redirect()
    return settings_redirect("deleted")


@app.post("/settings/employees/{user_id}/edit")
async def edit_employee(
    request: Request, user_id: UUID, full_name: str = Form(max_length=120),
    role: Literal["admin", "reviewer"] = Form(), active: bool = Form(),
    phone: str = Form("", max_length=40), confirm_deactivation: bool = Form(False),
):
    check_form_origin(request)
    admin = await require_account(request, admin=True)
    if not full_name.strip():
        return await settings_page(request, admin, edit=user_id, error="Enter a full name.", status=400)
    result = await supabase(
        "rest/v1/rpc/update_employee", "SUPABASE_SECRET_KEY", method="POST",
        json={"p_actor": admin["user_id"], "p_user": str(user_id), "p_name": full_name.strip(),
              "p_phone": phone.strip() or None, "p_role": role, "p_active": active,
              "p_confirm_deactivation": confirm_deactivation},
    )
    if not result.is_success:
        return await settings_page(request, admin, edit=user_id, error="The change could not be saved.", status=503)
    outcome = result.json()
    if outcome == "forbidden":
        raise HTTPException(403, "Only active admins can manage employees.")
    if outcome == "not_found":
        raise HTTPException(404, "Employee not found.")
    if outcome != "saved":
        error = {"last_admin": "The last active admin must keep admin access.",
                 "confirm_deactivation": "Confirm that you want to deactivate this employee."}
        return await settings_page(request, admin, edit=user_id,
                                   error=error.get(outcome, "The change could not be saved."), status=409)
    return settings_redirect("saved")


@app.post("/settings/employees/{user_id}/reset")
async def reset_employee(request: Request, user_id: UUID, confirmed: bool = Form(False)):
    check_form_origin(request)
    admin = await require_account(request, admin=True)
    if not confirmed:
        return await settings_page(request, admin, edit=user_id, error="Confirm the employee's identity first.", status=400)
    if not await reviewer_profile(str(user_id)):
        raise HTTPException(404, "Active employee not found.")
    email = await employee_email(user_id)
    result = await supabase("auth/v1/recover", "SUPABASE_PUBLISHABLE_KEY", method="POST",
                            params=callback_params(), json={"email": email})
    if not result.is_success:
        return await settings_page(request, admin, edit=user_id,
                                   error="Supabase could not send the password reset link.", status=502)
    return settings_redirect("reset")


def password_form(request, *, signed_in=False, token_hash=None, kind=None, error=None, status=200):
    title = "Change password" if signed_in else ("Choose your password" if kind == "invite" else "Reset password")
    return render(request, "account.html", title=title, status=status, signed_in=signed_in,
                  token_hash=token_hash, kind=kind, error=error)


def password_error(password, confirmation):
    if len(password) < 12:
        return "Use at least 12 characters."
    if password != confirmation:
        return "The passwords do not match."
    return None


async def update_password(token, password):
    return await supabase("auth/v1/user", "SUPABASE_PUBLISHABLE_KEY", method="PUT",
                          token=token, json={"password": password})


@app.get("/account/accept")
def accept_page(request: Request, token_hash: str = "", type: str = ""):
    if type not in {"invite", "recovery"} or not token_hash or len(token_hash) > 256:
        return password_form(request, status=400, error="Open a valid invitation or reset link.")
    return password_form(request, token_hash=token_hash, kind=type)


@app.post("/account/accept")
async def accept_password(
    request: Request, token_hash: str = Form(max_length=256), kind: Literal["invite", "recovery"] = Form(),
    password: str = Form(max_length=256), confirmation: str = Form(max_length=256),
):
    check_form_origin(request)
    if error := password_error(password, confirmation):
        return password_form(request, token_hash=token_hash, kind=kind, error=error, status=400)
    result = await supabase("auth/v1/verify", "SUPABASE_PUBLISHABLE_KEY", method="POST",
                            json={"token_hash": token_hash, "type": kind})
    if not result.is_success:
        return password_form(request, kind=kind, status=400,
                             error="This link is invalid or expired. Ask your admin for a new one.")
    session = result.json()
    user_id = (session.get("user") or {}).get("id")
    profile = await reviewer_profile(user_id)
    if not profile and not (user_id and await employee_invitation(user_id)):
        return password_form(request, kind=kind, status=403,
                             error="This invitation is cancelled, expired, or unavailable. Contact your admin.")
    updated = await update_password(session["access_token"], password)
    if not updated.is_success:
        return password_form(request, kind=kind, status=400,
                             error="Supabase rejected the password. Ask your admin for a fresh link before trying again.")
    if not profile and not await complete_employee_invitation(user_id):
        return password_form(request, kind=kind, status=403,
                             error="Your password was saved, but the invitation no longer grants access. Contact your admin.")
    return sign_in_redirect("/login?password_saved=1")


@app.get("/account/password")
async def change_password_page(request: Request):
    if not await signed_in_account(request):
        return sign_in_redirect()
    return password_form(request, signed_in=True)


@app.post("/account/password")
async def change_password(
    request: Request, current_password: str = Form(max_length=256),
    password: str = Form(max_length=256), confirmation: str = Form(max_length=256),
):
    check_form_origin(request)
    account = await require_account(request)
    if error := password_error(password, confirmation):
        return password_form(request, signed_in=True, error=error, status=400)
    verified = await supabase(
        "auth/v1/token", "SUPABASE_PUBLISHABLE_KEY", method="POST", params={"grant_type": "password"},
        json={"email": account["email"], "password": current_password},
    )
    if not verified.is_success or (verified.json().get("user") or {}).get("id") != account["user_id"]:
        return password_form(request, signed_in=True, error="Could not verify your current password.", status=400)
    result = await update_password(verified.json()["access_token"], password)
    if not result.is_success:
        return password_form(request, signed_in=True, error="Supabase rejected the new password.", status=400)
    return sign_in_redirect("/login?password_saved=1")


RESEARCH_LOCK = Lock()
RESEARCH_STATE = {"running": False, "message": "", "completed": 0,
                  "requested": 0, "saved": 0, "existing": 0, "failed": 0,
                  "screened": 0, "incomplete": 0, "issues": 0, "skip_counts": {}, "missing_counts": {}, "attempts": [],
                  "stop_reason": "", "usage": {}, "model_attempts": []}
QUALIFICATION_LABELS = {
    "needs_more_research": "Needs more research",
    "poor_fit": "Poor fit",
    "potential_fit": "Potential fit",
    "fits_profile_no_recent_signal_found": "Fits profile, no recent signal found",
    "qualified_for_review": "Qualified for review",
}
MISSING_LABELS = {
    "location": "US base", "ecommerce": "Online product sales", "revenue": "Annual revenue",
    "headcount": "Headcount", "recent_outreach_signal": "Recent outreach signal",
    "current_contact": "Current contact",
    "public_contact_email": "Published business email",
}


def research_state():
    with RESEARCH_LOCK:
        return deepcopy(RESEARCH_STATE)


def research_progress(message):
    with RESEARCH_LOCK:
        RESEARCH_STATE["message"] = message


CONTACT_EMAIL_LABELS = {
    "named_person": "Published named contact email",
    "general_inbox": "Published company inbox",
}


CONTACT_TYPE_LABELS = {
    "finance": "Finance contact", "founder": "Founder", "coo": "COO",
    "general_inbox": "General company inbox", "needs_review": "Ownership needs review",
}
NAMED_CONTACT_TYPES = {"finance", "founder", "coo"}


def contact_readiness(company):
    contacts = company["company_contacts"]
    if any(contact["contact_type"] in NAMED_CONTACT_TYPES for contact in contacts):
        return "Named email found"
    if any(contact["contact_type"] == "general_inbox" for contact in contacts):
        return "General inbox found"
    if company["email_evidence"]:
        if company["email_evidence"]["finding"] == CONTACT_EMAIL_LABELS["general_inbox"]:
            return "General inbox found"
        return "Named email found"
    if contacts or company["contact_email"]:
        return "Email needs review"
    return "No email saved"


def research_payload(company):
    contact = company["contact"] or {}
    row = {
        "name": company["name"], "website": company["website"], "score": company["score"],
        "qualification_status": company["qualification_status"],
        "missing_information": company["missing_information"],
        "research_errors": company["errors"],
        "contact_name": contact.get("name"), "contact_role": contact.get("role"),
    }
    reasons = []
    awarded = set()
    for reason in company["reasons"]:
        item = {key: reason[key] for key in ("criterion", "finding", "source_url", "quote", "evidence_status")}
        category = reason["criterion"]
        points = 0
        if category in {"location", "ecommerce"} and reason.get("fit") == "meets":
            if reason["evidence_status"] == "verified":
                points = company["score_breakdown"][category]["points"]
        elif category == "size":
            category = reason["size_metric"]
            breakdown = company["score_breakdown"][category]
            low, high = SIZE_TARGETS[category]
            start, end = reason.get("size_low"), reason.get("size_high")
            if (breakdown["evidence"] == reason["evidence_status"]
                    and type(start) in (int, float) and type(end) in (int, float)
                    and low <= start <= end <= high):
                points = breakdown["points"]
        elif reason["recent_outreach_signal"]:
            category = "timing"
            points = company["timing_score"]
        item["points"] = points if category not in awarded else 0
        if item["points"]:
            awarded.add(category)
        reasons.append(item)
    if contact:
        finding = f"{contact['name']} — {contact['role']}"
        if company["contact_status"] != "current_role_evidence_found":
            finding += " (current role unconfirmed)"
        reasons.append({"criterion": "contact", "finding": finding, "points": 0,
                        "source_url": contact["source_url"], "quote": contact["quote"],
                        "evidence_status": "verified"})
    row["research_summary"] = {
        "overview": company.get("overview", {}),
        "score_breakdown": company["score_breakdown"],
        "sizes": {
            metric: [{key: reason[key] for key in ("finding", "source_url", "evidence_status")}
                     for reason in company["reasons"] if reason.get("size_metric") == metric]
            for metric in SIZE_TARGETS
        },
    }
    return {"p_company": row, "p_reasons": reasons}


async def save_researched_company(company):
    result = await supabase(
        "rest/v1/rpc/save_researched_company", "SUPABASE_SECRET_KEY", method="POST",
        json=research_payload(company),
    )
    if not result.is_success or result.json() not in {"saved", "existing"}:
        raise HTTPException(503, "Research could not be saved. Verify the database migration.")
    return result.json()


def run_dashboard_research(count, keys, websites, manual_domains=()):
    def save_company(company):
        outcome = "skipped"
        save_error = None
        if company["research_savable"]:
            research_progress(f"Saving results for {company['name']}")
            try:
                outcome = asyncio.run(save_researched_company(company))
            except Exception:
                outcome = "failed"
                save_error = "Could not save to Supabase. Check the backend terminal."
                logging.getLogger(__name__).exception("Could not save a researched company")
        with RESEARCH_LOCK:
            RESEARCH_STATE["completed"] += 1
            if outcome != "skipped":
                RESEARCH_STATE[outcome] += 1
            if not company["research_complete"]:
                RESEARCH_STATE["incomplete"] += 1
            for criterion in company["unresolved_criteria"]:
                missing = RESEARCH_STATE["missing_counts"]
                missing[criterion] = missing.get(criterion, 0) + 1
            RESEARCH_STATE["attempts"].append({
                "name": company["name"], "website": company["website"], "outcome": outcome,
                "qualification": company["qualification_status"], "missing": company["missing_information"],
                "unresolved": company["unresolved_criteria"],
                "reason": "Online product sales could not be supported." if outcome == "skipped" else save_error,
                "errors": company["errors"],
            })

    message = "Research finished."
    stop_reason = "Unexpected error. Check the backend terminal."
    try:
        pilot, _, _, error = run_pilot(
            *keys, count=count, on_progress=research_progress, on_company=save_company,
            exclude_websites=websites, manual_domains=manual_domains,
        )
        stop_reason = pilot.stop_reason
        with RESEARCH_LOCK:
            RESEARCH_STATE["screened"] = sum(batch["proposed"] for batch in pilot.discovery_batches)
            RESEARCH_STATE["issues"] = len(pilot.issues)
            RESEARCH_STATE["usage"] = {"Tavily searches": pilot.searches, "Distinct page URLs": len(pilot.pages),
                                       "Model requests": pilot.model_requests,
                                       "Gemini requests": sum(a["provider"] == "Gemini" for a in pilot.model_attempts),
                                       "Groq requests": sum(a["provider"] == "Groq" for a in pilot.model_attempts)}
            RESEARCH_STATE["model_attempts"] = pilot.model_attempts
            skips = RESEARCH_STATE["skip_counts"]
            for batch in pilot.discovery_batches:
                for item in batch["skipped"]:
                    reason = item["reason"]
                    skips[reason] = skips.get(reason, 0) + 1
                    if reason == "already saved domain":
                        RESEARCH_STATE["attempts"].append({
                            "website": item["website"], "outcome": "existing",
                            "reason": "Already saved. No research was run for this domain.",
                        })
        if error:
            message = "Research stopped early."
        elif research_state()["saved"] < count:
            message = "Research finished with fewer saved companies than requested."
    except Exception:
        logging.getLogger(__name__).exception("Research run failed")
        message = "Research stopped unexpectedly."
    finally:
        with RESEARCH_LOCK:
            RESEARCH_STATE.update(running=False, message=message, stop_reason=stop_reason)


def normalize_research_domains(raw_domains):
    domains = []
    for line in raw_domains.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            url = urlsplit(line if "://" in line else f"https://{line}")
            domain = (url.hostname or "").lower().removeprefix("www.").rstrip(".")
            if (url.scheme not in {"http", "https"} or url.username or url.password or url.port
                    or not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", domain)
                    or domain.endswith((".local", ".localhost", ".internal", ".test"))
                    or is_social_host(domain)):
                raise ValueError
        except ValueError:
            raise HTTPException(400, "Enter valid public company domains, one per line.") from None
        if domain not in domains:
            domains.append(domain)
    if len(domains) > COMPANY_LIMIT:
        raise HTTPException(400, f"Enter no more than {COMPANY_LIMIT} distinct company domains.")
    return domains


@app.post("/companies/research")
async def start_research(request: Request, background: BackgroundTasks,
                         count: int = Form(DEFAULT_COMPANY_COUNT, ge=1, le=COMPANY_LIMIT),
                         domains: str = Form("", max_length=4096)):
    check_form_origin(request)
    await require_account(request, admin=True)
    manual_domains = normalize_research_domains(domains)
    if manual_domains:
        count = len(manual_domains)
    keys = tuple(os.getenv(name, "").strip() for name in ("TAVILY_API_KEY", "GEMINI_API_KEY"))
    if not all(keys):
        raise HTTPException(503, "Set TAVILY_API_KEY and GEMINI_API_KEY before starting research.")
    known = await supabase(
        "rest/v1/companies", "SUPABASE_SECRET_KEY",
        params={"select": "website"},
    )
    if not known.is_success:
        raise HTTPException(503, "Existing company websites could not be checked.")
    websites = {company["website"] for company in known.json()}
    with RESEARCH_LOCK:
        if RESEARCH_STATE["running"]:
            raise HTTPException(409, "A research batch is already running.")
        RESEARCH_STATE.update(running=True, message="Starting research", completed=0,
                              requested=count, saved=0, existing=0, failed=0,
                              screened=0, incomplete=0, issues=0, skip_counts={}, missing_counts={},
                              attempts=[], stop_reason="", usage={}, model_attempts=[])
    background.add_task(run_dashboard_research, count, keys, websites, manual_domains)
    return RedirectResponse("/dashboard/companies", status_code=303)


@app.get("/companies/research/status")
async def get_research_status(request: Request, download: bool = False):
    await require_account(request, admin=True)
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="ledgerwise-research-run.json"'
    return JSONResponse(research_state(), headers=headers)


CORE_REVIEW_CRITERIA = {
    "location": "US location",
    "ecommerce": "Online product sales",
    "revenue": "Annual revenue ($1M–$10M)",
    "headcount": "Employees (5–50)",
}
REVIEW_LABELS = {
    "pending": "Pending",
    "confirmed_fit": "Confirmed fit",
    "demo_fit": "Demo fit (test only)",
    "rejected": "Rejected",
    "needs_more_research": "Needs more research",
}
DRAFT_ELIGIBLE_DECISIONS = {"confirmed_fit", "demo_fit"}


def approved_demo_recipient(thread, message):
    """Resolve the only allowed recipient for an approved demo message."""
    if not thread["demo_only"]:
        raise ValueError("This gate requires a demo thread.")
    return approved_test_recipient(thread, message)


def approved_test_recipient(thread, message):
    if (message["thread_id"] != thread["id"] or message["direction"] != "out"
            or message["status"] != "approved" or message["approved_version"] is None
            or message["version"] != message["approved_version"]):
        raise ValueError("Approve the current message version before sending.")
    return configured_test_email()


def configured_test_email():
    recipient = os.getenv("TEST_EMAIL", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_+%-]+(?:\.[A-Za-z0-9_+%-]+)*@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}", recipient):
        raise ValueError("Configure one valid TEST_EMAIL before sending.")
    return recipient


CRITERION_REVIEW_LABELS = {
    "unverified": "Still to verify",
    "validated": "Validated",
    "concern": "Concern or mismatch",
}
SALES_STATUSES = ("new", "qualified", "contacted", "replied", "booked", "opted_out")
AI_FILTERS = {
    "potential_fit": "Potential fit",
    "poor_fit": "Poor fit",
    "needs_more_research": "Needs more research",
    "unavailable": "AI decision unavailable",
}


TOMBA_LIMIT = 23
DRAFT_STATUS_LABELS = {"draft": "Pending approval", "approved": "Pending approval",
                       "rejected": "Rejected", "sent": "Sent", "failed": "Failed"}
TOMBA_LOOKUP_LABELS = {
    "reserved": "Reserved or interrupted. No automatic retry.",
    "completed": "Lookup completed.",
    "no_results": "No usable contacts returned.",
    "failed": "Lookup failed. The reservation remains counted.",
    "uncertain": "Lookup outcome is uncertain. The reservation remains counted.",
}


def tomba_contact_type(result):
    role = result.get("position") or ""
    provider_type = result.get("type")
    if provider_type == "generic":
        return "general_inbox"
    if re.search(r"\b(?:cfo|chief financial officer|controller|(?:head|vp|director|vice president) of finance|"
                 r"finance (?:director|lead|head|vp|manager))\b", role, re.I):
        return "finance"
    if re.search(r"\b(?:co[- ]?)?founder\b", role, re.I):
        return "founder"
    if re.search(r"\b(?:coo|chief operating officer)\b", role, re.I):
        return "coo"
    return "needs_review"


def parse_tomba_contacts(payload, domain):
    records = payload["data"]["emails"]
    if not isinstance(records, list) or len(records) > 10:
        raise ValueError("Unexpected Tomba results")
    contacts = {}
    for result in records:
        email = result.get("email")
        if not isinstance(email, str) or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
            continue
        local, host = email.rsplit("@", 1)
        if (host.casefold() != domain and not host.casefold().endswith("." + domain)
                or re.fullmatch(r"(?:first[._-]?last|firstl|first|"
                                r"example|test|no[-_.]?reply)(?:[+._-].*)?", local, re.I)):
            continue
        verification = result.get("verification") or {}
        status = str(verification.get("status") or "unknown").strip().casefold()
        confidence = result.get("score")
        confidence = confidence if type(confidence) is int and 0 <= confidence <= 100 else None
        kind = tomba_contact_type(result)
        name = result.get("full_name") or " ".join(
            part for part in (result.get("first_name"), result.get("last_name")) if isinstance(part, str)
        )
        role = result.get("position")
        if kind in {"finance", "founder", "coo"} and not (name and role):
            kind = "needs_review"
        sources = [
            {"url": source["uri"], "method": "tomba", "evidence": "Source reported by Tomba",
             "still_on_page": source.get("still_on_page")}
            for source in result.get("sources") or []
            if isinstance(source, dict) and public_link(source.get("uri"))
        ]
        contact = {
            "email": email, "name": name or None, "role": role or None, "contact_type": kind,
            "sources": sources, "provider_status": status, "provider_confidence": confidence,
            "delivery_status": {"valid": "verified", "invalid": "undeliverable"}.get(status, "unverified"),
        }
        contacts.setdefault(email.casefold(), contact)

    return list(contacts.values())


async def request_tomba(domain, key, secret):
    async with httpx.AsyncClient(timeout=12) as client:
        result = await client.get(
            "https://api.tomba.io/v1/domain-search",
            headers={"X-Tomba-Key": key, "X-Tomba-Secret": secret},
            params={"domain": domain, "limit": 10, "page": 1, "live": "false", "enrich_mobile": "false"},
        )
    result.raise_for_status()
    return result.json()


async def email_drafts_page(request, account, *, lookup_results=None, contact_error=None):
    all_companies = await read_companies()
    companies = [company for company in all_companies if company["human_status"] in DRAFT_ELIGIBLE_DECISIONS]
    result = await supabase(
        "rest/v1/tomba_lookups", "SUPABASE_SECRET_KEY",
        params={"select": "company_id,domain,status"},
    )
    if not result.is_success:
        raise HTTPException(503, "Contact lookup records are unavailable. Check the Tomba migration.")
    lookups = result.json()
    draft_rows = []
    threads = {}
    if all_companies:
        result = await supabase(
            "rest/v1/messages", "SUPABASE_SECRET_KEY",
            params={"select": "id,thread_id,subject,body,status,version,facts_used,initial_outreach,review_events,send_state,actual_recipient,send_error,sent_at,threads!inner(company_id,demo_only)",
                    "direction": "eq.out", "initial_outreach": "not.is.null", "order": "id.desc"},
        )
        if not result.is_success:
            raise HTTPException(503, "Saved email drafts could not be loaded.")
        by_id = {company["id"]: company for company in all_companies}
        for message in result.json():
            company = by_id.get(message["threads"]["company_id"])
            if company and message.get("initial_outreach") and message["status"] in DRAFT_STATUS_LABELS:
                for fact in message["facts_used"]:
                    fact["safe_url"] = public_link(fact.get("url") or fact.get("source_url"))
                threads.setdefault(message["thread_id"], []).append({"company": company, "message": message,
                                   "test_only": message["threads"].get("demo_only") or company["human_status"] == "demo_fit",
                                   "status": {"sending": "Sending", "unknown": "Delivery unknown", "failed": "Failed"}.get(
                                       message.get("send_state"), DRAFT_STATUS_LABELS[message["status"]])})

    for versions in threads.values():
        versions.sort(key=lambda row: (row["message"]["version"], row["message"]["id"]))
        draft_rows.append({**versions[-1], "versions": versions})

    companies_with_current_draft = {
        row["company"]["id"] for row in draft_rows
        if row["message"]["status"] == "draft"
        and row["message"]["initial_outreach"].get("contact_id") == row["company"].get("primary_contact_id")
    }
    jobs = {}
    if companies:
        result = await supabase("rest/v1/initial_outreach_jobs", "SUPABASE_SECRET_KEY",
                                params={"select": "company_id,contact_id,state,started_at,failure_reason"})
        if not result.is_success:
            raise HTTPException(503, "Draft progress could not be loaded. Check the initial outreach migration.")
        jobs = {job["company_id"]: job for job in result.json()}

    for company in all_companies:
        contacts = company["company_contacts"]
        for contact in contacts:
            contact["selectable"] = (contact.get("delivery_status") != "undeliverable"
                                     and contact.get("provider_status") != "invalid")
        company["company_contacts"] = sorted(contacts, key=lambda contact: (
            contact_role_priority(contact), -(contact["provider_confidence"] or 0)))
        company["primary_contact"] = next(
            (contact for contact in contacts if contact["id"] == company.get("primary_contact_id")), None
        )
        company["usable_contacts"] = [contact for contact in contacts if contact["selectable"]]
        company["backup_contact"] = next(
            (contact for contact in contacts if contact["id"] == company.get("backup_contact_id")), None
        )
        job = jobs.get(company["id"])
        company["has_current_draft"] = company["id"] in companies_with_current_draft
        company["draft_progress"] = None
        company["draft_error"] = None
        if company["primary_contact"]:
            company["draft_progress"] = "not_started"
            if job and job["contact_id"] == company["primary_contact"]["id"]:
                company["draft_progress"] = job["state"]
                company["draft_error"] = job.get("failure_reason")
                if job["state"] == "generating" and datetime.fromisoformat(job["started_at"]) <= (
                    datetime.now(timezone.utc) - timedelta(minutes=3)
                ):
                    company["draft_progress"] = "failed"
                    company["draft_error"] = "The previous attempt was interrupted. Retry when ready."
    generation_rows = [
        company for company in companies
        if not company["has_current_draft"] and company["draft_progress"] in {"generating", "failed"}
    ]
    return render(request, "dashboard.html", sections=SECTIONS, section="drafts", account=account,
                  eligible_companies=companies, lookups={item["domain"]: item for item in lookups},
                  lookup_available=max(0, TOMBA_LIMIT - len(lookups)), lookup_limit=TOMBA_LIMIT,
                  lookup_labels=TOMBA_LOOKUP_LABELS, contact_type_labels=CONTACT_TYPE_LABELS,
                  review_labels=REVIEW_LABELS, draft_rows=draft_rows, generation_rows=generation_rows, lookup_results=lookup_results,
                  contact_error=contact_error,
                  test_recipient=test_recipient_for_display(), draft_statuses=[
                      *dict.fromkeys(DRAFT_STATUS_LABELS.values()), "Generating", "Generation failed", "Sending", "Delivery unknown"])


def contact_role_priority(contact):
    role = contact["role"] or ""
    if re.search(r"\b(cfo|chief financial officer|head of finance|finance director|director of finance)\b", role, re.I):
        return 0
    if re.search(r"\b(founder|ceo|chief executive officer|owner|controller|accounting manager|coo|chief operating officer)\b", role, re.I):
        return 1
    return 2


def tomba_credentials():
    credentials = tuple(os.getenv(name, "").strip() for name in ("TOMBA_API_KEY", "TOMBA_API_SECRET"))
    if not all(credentials):
        raise HTTPException(503, "Set TOMBA_API_KEY and TOMBA_API_SECRET before requesting a lookup.")
    return credentials


async def lookup_company_contacts(company_id, actor, credentials):
    reservation = await supabase(
        "rest/v1/rpc/reserve_tomba_lookup", "SUPABASE_SECRET_KEY", method="POST",
        json={"p_company": company_id, "p_actor": actor},
    )
    if not reservation.is_success:
        return "reservation_error"
    lookup = reservation.json()
    if lookup["status"] != "reserved":
        return lookup["status"]

    contacts = []
    try:
        payload = await request_tomba(lookup["domain"], *credentials)
        contacts = parse_tomba_contacts(payload, lookup["domain"])
        outcome = "completed" if contacts else "no_results"
    except httpx.HTTPStatusError as error:
        outcome = "failed" if error.response.status_code < 500 else "uncertain"
    except (httpx.RequestError, ValueError, KeyError, TypeError, AttributeError):
        outcome = "uncertain"

    finished = await supabase(
        "rest/v1/rpc/finish_tomba_lookup", "SUPABASE_SECRET_KEY", method="POST",
        json={"p_lookup": lookup["id"], "p_status": outcome, "p_contacts": contacts, "p_preferred": None},
    )
    return outcome if finished.is_success else "finish_error"


@app.post("/drafts/companies/lookup")
async def lookup_selected_contacts(request: Request, company_ids: list[int] = Form()):
    check_form_origin(request)
    account = await require_account(request)
    if not company_ids or any(not 0 < company_id < 2**63 for company_id in company_ids):
        raise HTTPException(422, "Select at least one valid company.")
    credentials = tomba_credentials()
    outcomes = {}
    for company_id in dict.fromkeys(company_ids):
        outcome = await lookup_company_contacts(company_id, account["user_id"], credentials)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if outcome in {"forbidden", "reservation_error", "finish_error"}:
            break
    return await email_drafts_page(request, account, lookup_results=outcomes)


@app.post("/drafts/companies/{company_id}/lookup")
async def lookup_draft_contacts(request: Request, company_id: int):
    check_form_origin(request)
    account = await require_account(request)
    outcome = await lookup_company_contacts(company_id, account["user_id"], tomba_credentials())
    errors = {
        "limit": (409, "The 23-lookup limit has been reached."),
        "not_found": (404, "Company not found."),
        "fit_required": (403, "A current Confirmed fit or Demo fit decision is required."),
        "forbidden": (403, "Only active employees can look up contacts."),
        "reservation_error": (503, "The lookup could not be reserved."),
        "finish_error": (503, "The lookup was reserved, but its outcome could not be saved. Do not retry."),
    }
    if outcome in errors:
        raise HTTPException(*errors[outcome])
    return RedirectResponse("/dashboard/drafts", status_code=303)


@app.post("/drafts/companies/{company_id}/contacts")
async def save_draft_contacts(request: Request, background: BackgroundTasks, company_id: int, primary_id: str = Form(""), backup_id: str = Form("")):
    check_form_origin(request)
    account = await require_account(request)

    def contact_id(value):
        if not value:
            return None
        if not re.fullmatch(r"[0-9]{1,19}", value) or not 0 < int(value) < 2**63:
            raise ValueError
        return int(value)

    try:
        primary, backup = contact_id(primary_id), contact_id(backup_id)
    except ValueError:
        return await email_drafts_page(request, account, contact_error="Choose valid saved contacts.")
    if backup is not None and (primary is None or primary == backup):
        return await email_drafts_page(request, account, contact_error="Choose a primary and a different backup.")
    status = await start_initial_outreach(background, company_id, account["user_id"], primary, backup)
    if status in {"saved", "generate"}:
        return RedirectResponse("/dashboard/drafts", status_code=303)
    errors = {"invalid_contact": "Choose distinct deliverable candidates saved for this company.",
              "fit_required": "The company needs a current Confirmed fit or Demo fit decision.",
              "forbidden": "Only active employees can validate contacts.",
              "not_found": "Company not found."}
    return await email_drafts_page(request, account, contact_error=errors.get(status, "Contact assignments could not be saved."))


async def start_initial_outreach(background, company_id, actor, primary=None, backup=None, *, retry=False):
    result = await supabase(
        "rest/v1/rpc/start_initial_outreach", "SUPABASE_SECRET_KEY", method="POST",
        json={"p_company": company_id, "p_actor": actor, "p_primary": primary,
              "p_backup": backup, "p_retry": retry},
    )
    if not result.is_success:
        return "save_error"
    job = result.json()
    if job["status"] == "generate":
        background.add_task(finish_initial_outreach, company_id, actor, job)
    return job["status"]


@app.post("/drafts/companies/{company_id}/refresh-personalization")
async def refresh_initial_personalization(request: Request, background: BackgroundTasks,
                                          company_id: int, message_id: int = Form()):
    check_form_origin(request)
    account = await require_account(request)
    result = await supabase(
        "rest/v1/rpc/refresh_initial_personalization", "SUPABASE_SECRET_KEY", method="POST",
        json={"p_company": company_id, "p_actor": account["user_id"], "p_message": message_id},
    )
    job = result.json() if result.is_success else {}
    if job.get("status") == "generate":
        background.add_task(finish_initial_outreach, company_id, account["user_id"], job)
        if request.headers.get("accept") == "application/json":
            return JSONResponse({"status": "generating"}, status_code=202)
        return RedirectResponse("/dashboard/drafts", status_code=303)
    if request.headers.get("accept") == "application/json":
        return JSONResponse({"detail": "Regeneration could not start. Check that the draft is still pending and no other attempt is running."}, status_code=409)
    return await email_drafts_page(request, account, contact_error="The draft could not be refreshed. Check whether it is still pending or generation is already running.")


async def finish_initial_outreach(company_id, actor, job):
    draft = {"error": "The primary contact or company eligibility changed during drafting."}
    recipient = None
    try:
        recipient = configured_test_email()
        companies = await read_companies(company_id)
        if companies:
            company = companies[0]
            contact = next((c for c in company["company_contacts"] if c["id"] == job["contact_id"]), None)
            if (contact and company["primary_contact_id"] == contact["id"]
                    and company["human_status"] in DRAFT_ELIGIBLE_DECISIONS):
                draft = await asyncio.to_thread(generate_initial_draft, company, contact, actor)
    except (ValueError, HTTPException, httpx.HTTPError) as error:
        if isinstance(error, httpx.HTTPStatusError):
            message = f"Gemini returned HTTP {error.response.status_code}. Check your Gemini project or retry later."
        elif isinstance(error, httpx.TimeoutException):
            message = "Gemini timed out. Retry when ready."
        elif isinstance(error, httpx.HTTPError):
            message = "The connection to Gemini failed. Retry when ready."
        elif isinstance(error, HTTPException):
            message = "Saved company evidence could not be loaded. Check Supabase and retry."
        else:
            message = str(error)
        draft = {"error": message}
        logging.getLogger(__name__).warning("Initial draft failed for company %s category=%s",
                                            company_id, type(error).__name__)
    try:
        result = await supabase(
            "rest/v1/rpc/finish_initial_outreach", "SUPABASE_SECRET_KEY", method="POST",
            json={"p_company": company_id, "p_token": job["token"], "p_draft": draft, "p_test_recipient": recipient},
        )
        if not result.is_success:
            logging.getLogger(__name__).warning("Initial draft could not be stored for company %s", company_id)
    except HTTPException:
        logging.getLogger(__name__).warning("Initial draft storage interrupted for company %s", company_id)


@app.post("/drafts/companies/{company_id}/retry")
async def retry_initial_outreach(request: Request, background: BackgroundTasks, company_id: int):
    check_form_origin(request)
    account = await require_account(request)
    status = await start_initial_outreach(background, company_id, account["user_id"], retry=True)
    if status in {"saved", "generate"}:
        return RedirectResponse("/dashboard/drafts", status_code=303)
    return await email_drafts_page(request, account, contact_error="Could not start drafting. Check company eligibility and the saved primary contact.")


def test_recipient_for_display():
    try:
        return configured_test_email()
    except ValueError:
        return None


@app.post("/conversations/replies/{message_id}/{action}")
@app.post("/drafts/messages/{message_id}/{action}")
async def review_initial_email(request: Request, message_id: int, action: Literal["edit", "reject", "approve"],
                               version: int = Form(gt=0), subject: str = Form("", max_length=120),
                               body: str = Form("", max_length=1600)):
    check_form_origin(request)
    account = await require_account(request)
    payload = {"p_message": message_id, "p_version": version, "p_actor": account["user_id"], "p_action": action}
    is_reply = request.url.path.startswith("/conversations/")
    if action == "edit":
        result = await supabase("rest/v1/messages", "SUPABASE_SECRET_KEY", params={
            "id": f"eq.{message_id}", "version": f"eq.{version}", "status": "eq.draft",
            "select": ("facts_used,initial_outreach,threads!inner(companies!inner(name))"
                       + (",reply_to_id,offered_slots,review_events" if is_reply else "")),
        })
        if not result.is_success:
            raise HTTPException(503, "Saved draft evidence could not be loaded.")
        rows = result.json()
        if not rows or not rows[0].get("reply_to_id" if is_reply else "initial_outreach"):
            raise HTTPException(409, "This draft changed. Reload and review the latest version.")
        try:
            if is_reply:
                if rows[0].get("offered_slots") or any(
                    (event.get("decision") or {}).get("intent") == "meeting_scheduling"
                    for event in rows[0].get("review_events", [])
                ):
                    raise ValueError("Use the Cal.com scheduling draft as generated to preserve the verified times.")
                if not body.strip():
                    raise ValueError("Enter a reply before saving.")
                subject = (await load_reply_parent(rows[0]["reply_to_id"]))["subject"]
            else:
                subject, body = await asyncio.to_thread(verify_edited_copy, subject, body,
                    rows[0]["threads"]["companies"]["name"], rows[0]["facts_used"])
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except httpx.HTTPError:
            raise HTTPException(503, "Gemini is unavailable to verify edits. Your saved draft is unchanged.") from None
        payload.update(p_subject=subject, p_body=body)
    elif action == "approve":
        try:
            recipient = configured_test_email()
            settings = gmail_settings()
        except ValueError as error:
            raise HTTPException(503, str(error)) from None
        payload["p_recipient"] = recipient

    rpc = "review_reply_message" if is_reply else "review_initial_message"
    result = await supabase(f"rest/v1/rpc/{rpc}", "SUPABASE_SECRET_KEY", method="POST", json=payload)
    if not result.is_success:
        raise HTTPException(503, "The review action could not be confirmed. Refresh the draft before retrying.")
    review = result.json()
    status = review["status"]
    if status in {"edited", "rejected"}:
        return {"status": status}
    if status != "claimed" or action != "approve":
        errors = {
            "forbidden": (403, "Only active employees can review email."),
            "stale": (409, "This draft changed or sending already started. Reload the latest version."),
            "ineligible": (409, "Company eligibility or the primary contact changed."),
            "suppressed": (409, "The test inbox or intended contact is suppressed."),
            "subject_in_use": (409, "Use a distinct subject for this initial email to keep Gmail threads separate."),
        }
        raise HTTPException(*errors.get(status, (422, "This review action is unavailable.")))

    try:
        locked_recipient = approved_test_recipient(review["thread"], review["message"])
        if locked_recipient != recipient or review["message"]["actual_recipient"] != recipient:
            raise ValueError("The approved test recipient changed. Reload before retrying.")
        if is_reply:
            if review["thread"].get("status") == "ai_handles":
                # A failed retry can leave a draft in history. Check after claiming its exact version.
                try:
                    decision = await supabase("rest/v1/reply_decisions", "SUPABASE_SECRET_KEY", params={
                        "incoming_id": f"eq.{review['incoming']['id']}", "select": "state",
                    })
                except HTTPException:
                    raise ValueError("Could not verify the latest AI reply state. Try again.") from None
                if not decision.is_success:
                    raise ValueError("Could not verify the latest AI reply state. Try again.")
                if any(job["state"] == "failed" for job in decision.json()):
                    raise ValueError("AI regeneration failed. Retry AI reply generation before approving.")
            headers = review["incoming"]["inbound_email"]
            outcome, error, ids = await deliver_test_email(review["message"], recipient, settings, reply={
                "gmail_thread_id": review["thread"]["gmail_thread_id"], "message_id": headers["message_id"],
                "references": headers.get("references") or headers.get("in_reply_to"),
            })
        else:
            outcome, error, ids = await deliver_test_email(review["message"], recipient, settings)
    except ValueError as failure:
        outcome, error, ids = "failed", str(failure), {}
    try:
        result = await supabase("rest/v1/rpc/record_initial_send", "SUPABASE_SECRET_KEY", method="POST", json={
            "p_message": message_id, "p_version": version, "p_outcome": outcome, "p_error": error,
            "p_gmail_id": ids.get("id"), "p_gmail_thread": ids.get("threadId"),
        })
        if not result.is_success or result.json() is not True:
            raise HTTPException(503)
    except HTTPException:
        raise HTTPException(503, "The send outcome could not be recorded. Check Gmail Sent and do not resend.") from None
    if outcome != "sent":
        return JSONResponse({"status": outcome, "detail": error}, status_code=502)
    return {"status": "sent"}


@app.post("/conversations/threads/{thread_id}/{action}")
async def change_conversation_owner(request: Request, thread_id: int, action: Literal["takeover", "return_to_ai"]):
    check_form_origin(request)
    account = await require_account(request)
    result = await supabase("rest/v1/rpc/change_conversation_owner", "SUPABASE_SECRET_KEY", method="POST",
        json={"p_thread": thread_id, "p_actor": account["user_id"], "p_action": action})
    if not result.is_success or result.json() is not True:
        raise HTTPException(409, "Cannot change ownership. Check the opt-out, handoff and latest reply status.")
    return {"status": action}


@app.post("/conversations/incoming/{message_id}/compose")
async def compose_manual_reply(request: Request, message_id: int, body: str = Form(min_length=1, max_length=1600)):
    check_form_origin(request)
    account = await require_account(request)
    if not body.strip():
        raise HTTPException(422, "Enter a reply before saving.")
    result = await supabase("rest/v1/rpc/compose_manual_reply", "SUPABASE_SECRET_KEY", method="POST",
        json={"p_incoming": message_id, "p_actor": account["user_id"], "p_body": body, "p_facts": []})
    if not result.is_success or result.json() is not True:
        raise HTTPException(409, "This conversation changed or a reply already exists. Select the company again.")
    return {"status": "composed"}


async def load_reply_parent(incoming_id):
    result = await supabase("rest/v1/messages", "SUPABASE_SECRET_KEY", params={
        "id": f"eq.{incoming_id}", "direction": "eq.in", "select": "id,thread_id,subject,body,received_at,created_at",
    })
    if not result.is_success or not result.json():
        raise ValueError("The original incoming email is no longer available.")
    return result.json()[0]


def confirmed_scheduling_slot(incoming, rows):
    arrived = incoming.get("received_at") or incoming.get("created_at")
    if not arrived:
        return None, None, None
    offers = [row for row in rows if row.get("sent_at") and row.get("offered_slots")
              and instant(row["sent_at"]) < instant(arrived)]
    offer = max(offers, key=lambda row: instant(row["sent_at"]), default=None)
    slot, phone = acceptance(incoming["body"], offer["offered_slots"]) if offer else (None, None)
    return offer, slot, phone


async def scheduling_draft(incoming, rows, company, job, mode, *, actor=None):
    if not os.getenv("CALCOM_API_KEY"):
        return None
    offer, slot, phone = confirmed_scheduling_slot(incoming, rows)
    if slot is None:
        if mode == "process_booking":
            raise ValueError("The latest reply no longer confirms a specific sent slot.")
        proposal = scheduling_request(incoming["body"], offer is not None)
        if proposal == "clarify":
            return scheduling_reply(
                ("Could you confirm the option number from our offer, or share your preferred date, time, and time zone?"
                 if offer else "Could you share your preferred date, time, and time zone?"),
                "The prospect's requested time or confirmation is unclear.")
        if proposal is not None:
            async with httpx.AsyncClient(timeout=15) as client:
                calendar = Calcom(client)
                if proposal == "next_week":
                    return await calendar.offer(next_week=True)
                return await calendar.propose(proposal)
        return None
    recorded = [event for row in rows for event in row.get("review_events", [])
                if event.get("action") == "booking_confirmed"]
    if recorded:
        return booking_handoff("This conversation already has a confirmed booking.")
    if os.getenv("CALCOM_BOOKING_ENABLED", "").lower() != "true":
        raise ValueError("Booking disabled. The prospect confirmed " + slot_label(slot) + ".")
    if mode == "regenerate":
        raise ValueError("Use Process confirmed booking to book the confirmed slot.")
    name = (company.get("contact_name") or "").strip()
    if not name:
        return booking_handoff("A named contact is required before booking.")
    email = configured_test_email()
    async with httpx.AsyncClient(timeout=15) as client:
        calcom = Calcom(client)
        start = instant(slot["start"])
        if start <= datetime.now(timezone.utc) or not any(
            item["start"] == slot["start"] for item in await calcom.slots(start, start + timedelta(days=1))
        ):
            return await calcom.offer()
        request = await supabase("rest/v1/rpc/reserve_calcom_booking", "SUPABASE_SECRET_KEY", method="POST",
            json={"p_incoming": incoming["id"], "p_token": job.get("token"), "p_offer": offer["id"], "p_actor": actor,
                  "p_slot": slot, "p_phone": phone, "p_email": email, "p_name": name})
        if not request.is_success or request.json() is not True:
            return booking_handoff("Booking could not be reserved. Check for an existing attempt before retrying.")
        try:
            booking = await calcom.book(slot, email, "Ledgerwise test attendee", incoming["id"])
            saved = await supabase("rest/v1/rpc/record_calcom_booking", "SUPABASE_SECRET_KEY", method="POST",
                json={"p_incoming": incoming["id"], "p_uid": booking["uid"],
                      "p_link": public_link(booking.get("meetingUrl")), "p_end": booking.get("end")})
            if not saved.is_success or saved.json() is not True:
                raise ValueError("Booking could not be recorded.")
        except (httpx.HTTPError, HTTPException, ValueError, KeyError, TypeError):
            return booking_handoff("Booking outcome needs verification in Cal.com. Do not retry until it is reconciled.")
    return scheduling_reply(f"Your call is confirmed for {slot_label(slot)}.",
                            "Cal.com confirmed the booking. This email still requires approval.")


async def reviewer_scheduling_context(request, thread_id):
    account = await require_account(request)
    result = await supabase("rest/v1/threads", "SUPABASE_SECRET_KEY", params={
        "id": f"eq.{thread_id}", "owner": f"eq.{account['user_id']}", "status": "eq.human_took_over",
        "gmail_thread_id": "not.is.null", "select": "companies(name,status,contact_name,company_contacts!company_contacts_company_id_fkey(id,name),primary_contact_id)",
    })
    if not result.is_success:
        raise HTTPException(503, "Could not check conversation ownership.")
    items = result.json()
    if not items or items[0]["companies"]["status"] == "opted_out":
        raise HTTPException(409, "Take over an eligible conversation before scheduling.")
    company = items[0]["companies"]
    contact = next((row for row in company["company_contacts"] if row["id"] == company["primary_contact_id"]), None)
    if contact:
        company = {**company, "contact_name": contact["name"]}
    result = await supabase("rest/v1/messages", "SUPABASE_SECRET_KEY", params={
        "thread_id": f"eq.{thread_id}", "status": "in.(received,sent)",
        "select": "id,direction,body,created_at,received_at,sent_at,offered_slots,review_events",
        "order": "id.desc", "limit": 50,
    })
    if not result.is_success:
        raise HTTPException(503, "Could not load the prospect's reply.")
    rows = result.json()
    incoming = max((row for row in rows if row["direction"] == "in"),
                   key=lambda row: (conversation_timestamp(row), row["id"]), default=None)
    if incoming is None:
        raise HTTPException(409, "Wait for a prospect reply before scheduling.")
    return account, company, incoming, rows


@app.get("/conversations/threads/{thread_id}/schedule")
async def reviewer_scheduling_options(request: Request, thread_id: int, month: str | None = None):
    _, company, incoming, rows = await reviewer_scheduling_context(request, thread_id)
    offer, agreed, _ = confirmed_scheduling_slot(incoming, rows)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            cal = Calcom(client)
            now = datetime.now(timezone.utc)
            zone = ZoneInfo(cal.zone)
            month = month or now.astimezone(zone).strftime("%Y-%m")
            if not re.fullmatch(r"20[0-9]{2}-(?:0[1-9]|1[0-2])", month):
                raise HTTPException(422, "Select a valid month.")
            start = datetime.fromisoformat(month + "-01").replace(tzinfo=zone)
            end = (start + timedelta(days=32)).replace(day=1)
            slots = await cal.slots(max(now, start), end) if end > now else []
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(503, "Cal.com availability could not be loaded.") from None
    return {"company": company["name"], "contact": company.get("contact_name"), "incoming_id": incoming["id"],
            "month": month, "timezone": cal.zone,
            "slots": [{"start": slot["start"], "label": slot_label(slot),
                       "day": instant(slot["start"]).astimezone(zone).date().isoformat()}
                      for slot in slots if instant(slot["start"]) < end],
            "confirmation": {"start": agreed["start"], "label": slot_label(agreed), "offer_id": offer["id"],
                             "message": incoming["body"]} if agreed else None,
            "booking_enabled": os.getenv("CALCOM_BOOKING_ENABLED", "").lower() == "true"}


@app.post("/conversations/threads/{thread_id}/schedule/{action}")
async def reviewer_schedule(request: Request, thread_id: int, action: Literal["offer", "book"],
                            incoming_id: int = Form(), starts: list[str] = Form(default=[]),
                            offer_id: int | None = Form(None), confirmed_start: str = Form(""), reviewed: bool = Form(False)):
    check_form_origin(request)
    account, company, incoming, rows = await reviewer_scheduling_context(request, thread_id)
    if incoming["id"] != incoming_id:
        raise HTTPException(409, "A newer reply arrived. Reopen Schedule meeting to review it.")
    try:
        if action == "offer":
            if not 1 <= len(set(starts)) == len(starts) <= 3:
                raise HTTPException(422, "Select one to three different available slots.")
            now = datetime.now(timezone.utc)
            requested = {instant(start) for start in starts}
            async with httpx.AsyncClient(timeout=15) as client:
                cal = Calcom(client)
                selected = []
                for start in sorted(requested):
                    if start <= now:
                        continue
                    available = await cal.slots(start, start + timedelta(days=1))
                    selected.extend(slot for slot in available if instant(slot["start"]) == start)
            if len(selected) != len(starts):
                raise HTTPException(409, "One or more slots are no longer available. Reopen Schedule meeting.")
            draft = offer_reply(selected)
        else:
            if os.getenv("CALCOM_BOOKING_ENABLED", "").lower() != "true":
                raise HTTPException(409, "Cal.com booking is disabled.")
            offer, slot, _ = confirmed_scheduling_slot(incoming, rows)
            if not reviewed or not slot or offer["id"] != offer_id or slot["start"] != confirmed_start:
                raise HTTPException(409, "Review the prospect's explicit confirmation of the sent offer.")
            draft = await scheduling_draft(incoming, rows, company, {}, "reviewer", actor=account["user_id"])
            if not draft or draft["needs_human"]:
                raise HTTPException(409, (draft or {}).get("reason", "Booking could not be started."))
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(503, "Cal.com or the scheduling configuration is unavailable.") from None
    saved = await supabase("rest/v1/rpc/compose_reviewer_scheduling", "SUPABASE_SECRET_KEY", method="POST",
        json={"p_incoming": incoming_id, "p_actor": account["user_id"], "p_result": draft})
    if not saved.is_success or saved.json() is not True:
        raise HTTPException(409, "The draft could not be saved. Check conversation ownership and any existing draft or booking before retrying.")
    return {"status": "draft"}


async def run_ai_reply(incoming_id, mode="auto", version_id=None):
    claim_mode = ("regenerate" if version_id else "retry") if mode == "process_booking" else mode
    claim = await supabase("rest/v1/rpc/claim_ai_reply", "SUPABASE_SECRET_KEY", method="POST", json={
        "p_incoming": incoming_id, "p_mode": claim_mode, "p_version_id": version_id,
    })
    if not claim.is_success:
        return "failed"
    job = claim.json()
    if job["status"] != "generate":
        return job["status"]
    draft = error = None
    try:
        incoming = await load_reply_parent(incoming_id)
        companies = await read_companies(job["company_id"])
        if not companies:
            raise ValueError("The company profile is no longer available.")
        company = companies[0]
        primary = next((entry for entry in company["company_contacts"] if entry["id"] == company.get("primary_contact_id")), None)
        if primary:
            company = {**company, "contact_name": primary["name"], "contact_role": primary["role"]}
        result = await supabase("rest/v1/messages", "SUPABASE_SECRET_KEY", params={
            "thread_id": f"eq.{incoming['thread_id']}", "status": "in.(sent,received)",
            "select": "id,direction,subject,body,review_events,offered_slots,sent_at", "order": "id.desc", "limit": 20,
        })
        if not result.is_success:
            raise ValueError("The conversation history could not be loaded.")
        rows = result.json()
        decisions = [event["decision"] for row in rows for event in row.get("review_events", [])
                     if event.get("decision")]
        history = [{**row, "body": row["body"][:4000]} for row in reversed(rows)]
        draft = await scheduling_draft(incoming, rows, company, job, mode)
        if draft is None:
            try:
                draft = await asyncio.to_thread(generate_reply, company, incoming, history, {"status": "ai_handles"},
                                                decisions=decisions)
            except ValueError as failure:
                # The generator already ran its allowed revisions. Preserve the reason without saving unsafe copy.
                draft = {"intent": "reply_validation_failed", "action": "human_assistance",
                         "reason": str(failure), "needs_human": True, "body": "", "facts": []}
            if (os.getenv("CALCOM_API_KEY") and draft["action"] in {"suggest_call", "scheduling_interest"}
                    and not draft["needs_human"]):
                async with httpx.AsyncClient(timeout=15) as client:
                    draft = await Calcom(client).offer()
    except (ValueError, httpx.HTTPError, HTTPException, KeyError, TypeError) as failure:
        error = str(failure) if isinstance(failure, ValueError) else "Gemini or conversation data was unavailable. Retry when ready."
    payload = {"p_incoming": incoming_id, "p_token": job["token"], "p_result": draft}
    rpc = "finish_scheduling_reply" if draft and "offered_slots" in draft else "finish_ai_reply"
    if rpc == "finish_ai_reply":
        payload["p_error"] = error
    result = await supabase(f"rest/v1/rpc/{rpc}", "SUPABASE_SECRET_KEY", method="POST", json=payload)
    return "ready" if result.is_success and result.json() is True else "failed"


async def generate_synced_replies(gmail_ids):
    for gmail_id in gmail_ids:
        try:
            result = await supabase("rest/v1/messages", "SUPABASE_SECRET_KEY", params={
                "gmail_message_id": f"eq.{gmail_id}", "select": "id", "direction": "eq.in",
            })
            if result.is_success and result.json():
                await run_ai_reply(result.json()[0]["id"])
        except HTTPException:
            logging.getLogger(__name__).warning("Synced reply could not be queued. It remains available in Conversations.")


@app.post("/conversations/incoming/{message_id}/generate")
async def request_ai_reply(request: Request, message_id: int, version_id: int | None = Form(None),
                           process_booking: bool = Form(False)):
    check_form_origin(request)
    await require_account(request)
    if process_booking:
        if os.getenv("CALCOM_BOOKING_ENABLED", "").lower() != "true":
            raise HTTPException(409, "Booking disabled.")
        if not os.getenv("CALCOM_API_KEY"):
            raise HTTPException(503, "Cal.com is not configured.")
    mode = "process_booking" if process_booking else "regenerate" if version_id else "retry"
    return await reply_generation_response(message_id, mode, version_id)


@app.post("/conversations/incoming/{message_id}/reopen")
async def reopen_ai_handoff(request: Request, message_id: int):
    check_form_origin(request)
    account = await require_account(request)
    try:
        incoming = await load_reply_parent(message_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from None
    result = await supabase("rest/v1/reply_decisions", "SUPABASE_SECRET_KEY", params={
        "incoming_id": f"eq.{message_id}", "state": "eq.ready", "select": "decision",
    })
    decisions = result.json() if result.is_success else []
    decision = (decisions[0].get("decision") or {}) if decisions else {}
    if not recoverable_handoff(incoming["body"], decision):
        raise HTTPException(409, "This handoff cannot be reopened for AI analysis.")
    result = await supabase("rest/v1/rpc/reopen_mistaken_ai_handoff", "SUPABASE_SECRET_KEY", method="POST", json={
        "p_incoming": message_id, "p_actor": account["user_id"], "p_expected": decision,
    })
    if not result.is_success:
        raise HTTPException(503, "Could not reopen the handoff. Check that the reviewer retry migration is applied.")
    if result.json() is not True:
        raise HTTPException(409, "The conversation changed or is no longer eligible for AI analysis.")
    return await reply_generation_response(message_id, "retry")


async def reply_generation_response(message_id, mode, version_id=None):
    status = await run_ai_reply(message_id, mode, version_id)
    if status == "failed":
        detail = "Reply drafting failed. Retry when ready."
        result = await supabase("rest/v1/reply_decisions", "SUPABASE_SECRET_KEY", params={
            "incoming_id": f"eq.{message_id}", "select": "error",
        })
        if result.is_success and (rows := result.json()):
            detail = rows[0].get("error") or detail
        raise HTTPException(502, detail)
    if status != "ready":
        raise HTTPException(409, "This reply is no longer eligible or an attempt is already running.")
    return {"status": "ready"}


SYNC_COUNT_KEYS = ("imported", "suppressed", "duplicate", "skipped", "failed")
SYNC_ERRORS = {
    "scope": "Gmail read access was denied. Reauthorize the dedicated account with gmail.readonly.",
    "auth": "Gmail authentication or sync failed. Check the refresh token and try again.",
    "config": "Gmail sync could not complete. Check the server Gmail configuration and retry.",
}


@app.post("/conversations/sync")
async def sync_conversation_inbox(request: Request, background: BackgroundTasks, thread_id: int | None = Form(None)):
    check_form_origin(request)
    await require_account(request)
    result = await supabase("rest/v1/threads", "SUPABASE_SECRET_KEY", params={
        "select": "id,gmail_thread_id,messages(actual_recipient,gmail_message_id)",
        "gmail_thread_id": "not.is.null", "messages.status": "eq.sent",
    })
    if not result.is_success:
        raise HTTPException(503, "Saved Gmail threads could not be loaded.")

    imported = []

    async def save_reply(thread, gmail_thread, reply):
        result = await supabase("rest/v1/rpc/import_gmail_reply", "SUPABASE_SECRET_KEY", method="POST",
                               json={"p_thread": thread, "p_gmail_thread": gmail_thread, "p_reply": reply})
        if result.is_success:
            outcome = result.json()
            if outcome == "imported":
                imported.append(reply["gmail_message_id"])
            return "skipped" if outcome == "unmatched" else outcome
        try:
            code = result.json().get("code", "")
        except (AttributeError, ValueError):
            code = ""
        logging.getLogger(__name__).warning("Gmail reply import failed for thread %s (HTTP %s, SQLSTATE %s)",
            thread, result.status_code, code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9]{5}", code) else "unknown")
        return "failed"

    stats, error = None, None
    try:
        stats = await sync_known_threads(result.json(), save_reply)
    except httpx.HTTPStatusError as failure:
        if failure.response.status_code in {401, 403}:
            error = "scope"
        else:
            error = "auth"
    except (httpx.RequestError, ValueError, KeyError, TypeError):
        error = "config"
    if imported:
        background.add_task(generate_synced_replies, imported)
    params = {"thread_id": thread_id} if thread_id is not None else {}
    if stats is not None:
        params["sync_counts"] = ",".join(str(stats.get(key, 0)) for key in SYNC_COUNT_KEYS)
    if error:
        params["sync_error"] = error
    return RedirectResponse("/dashboard/conversations" + ("?" + urlencode(params) if params else ""), status_code=303)


def conversation_timestamp(message):
    value = message.get("received_at") or message.get("sent_at") or message.get("created_at")
    return datetime.fromisoformat(value) if value else datetime.min.replace(tzinfo=timezone.utc)


async def conversations_page(request, account, thread_id, *, sync_stats=None, sync_error=None):
    result = await supabase("rest/v1/threads", "SUPABASE_SECRET_KEY", params={
        "select": ("id,gmail_thread_id,status,handoff_reason,owner,created_at,companies(name,contact_name,status),"
                   "messages(id,status,direction,initial_outreach,created_at,sent_at,received_at)"),
        "gmail_thread_id": "not.is.null",
    })
    if not result.is_success:
        raise HTTPException(503, "Conversations could not be loaded.")
    threads = result.json()
    for thread in threads:
        summaries = thread.get("messages", [])
        thread["activity"] = max((conversation_timestamp(message) for message in summaries),
                                 default=conversation_timestamp(thread))
        thread["pending"] = sum(message["status"] == "draft" and message["direction"] == "out"
                                for message in summaries)
        sent = next((message.get("initial_outreach") for message in summaries
                     if message["status"] == "sent" and message.get("initial_outreach")), None)
        thread["primary_contact"] = (sent or {}).get("name") or thread["companies"].get("contact_name")
    threads.sort(key=lambda thread: (thread["activity"], thread["id"]), reverse=True)
    selected = next((thread for thread in threads if thread["id"] == thread_id), None)
    if thread_id is not None and selected is None:
        raise HTTPException(404, "Conversation not found.")
    messages, history = [], []
    if selected:
        result = await supabase("rest/v1/messages", "SUPABASE_SECRET_KEY", params={
            "thread_id": f"eq.{selected['id']}", "order": "version.asc,id.asc",
            "select": ("id,direction,subject,body,version,status,created_at,sent_at,received_at,"
                       "actual_recipient,initial_outreach,review_events,gmail_message_id,inbound_email,reply_to_id,facts_used,send_state,send_error,offered_slots"),
        })
        if not result.is_success:
            raise HTTPException(503, "Conversation messages could not be loaded.")
        rows = result.json()
        history = [message for message in rows if not message.get("reply_to_id")
                   and (message.get("initial_outreach") or message.get("direction", "out") == "out")]
        reply_groups = {}
        for message in rows:
            parent_id = message.get("reply_to_id")
            if parent_id:
                reply_groups.setdefault(parent_id, []).append(message)
                for fact in message.get("facts_used", []):
                    fact["safe_url"] = public_link(fact.get("source_url") or fact.get("url"))
        latest_replies = {versions[-1]["id"] for versions in reply_groups.values()}
        for versions in reply_groups.values():
            versions[-1]["reply_versions"] = versions.copy()
        incoming_ids = [str(message["id"]) for message in rows if message["status"] == "received"]
        if incoming_ids:
            decisions = await supabase("rest/v1/reply_decisions", "SUPABASE_SECRET_KEY", params={
                "select": "incoming_id,state,decision,error,started_at", "incoming_id": f"in.({','.join(incoming_ids)})",
            })
            if not decisions.is_success:
                raise HTTPException(503, "AI reply decisions could not be loaded. Check the reply migration.")
            by_parent = {job["incoming_id"]: job for job in decisions.json()}
            for message in rows:
                if job := by_parent.get(message.get("reply_to_id") or message["id"]):
                    message["reply_job"] = job
                    if job["state"] == "generating" and datetime.fromisoformat(job["started_at"]) <= (
                            datetime.now(timezone.utc) - timedelta(minutes=5)):
                        job.update(state="failed", error="Generation was interrupted. Retry when ready.")
        latest_incoming = max((m for m in rows if m["status"] == "received"),
                              key=lambda m: (conversation_timestamp(m), m["id"]), default=None)
        if (latest_incoming and selected.get("status") == "ai_handles"
                and selected["companies"].get("status") != "opted_out"
                and not any(event["action"] in {"booking_requested", "booking_confirmed"}
                            for row in rows for event in row.get("review_events", []) if "action" in event)):
            _, agreed, _ = confirmed_scheduling_slot(latest_incoming, rows)
            versions = reply_groups.get(latest_incoming["id"], [])
            if (agreed and not any(version["status"] in {"approved", "sent"} for version in versions)
                    and not (versions and versions[-1]["status"] == "draft" and versions[-1].get("offered_slots"))):
                latest_incoming["pending_booking"] = slot_label(agreed)
                latest_incoming["booking_enabled"] = os.getenv("CALCOM_BOOKING_ENABLED", "").lower() == "true"
                if versions:
                    latest_incoming["booking_retry_version"] = versions[-1]["id"]
                    versions[-1]["pending_booking"] = True
        for message in rows:
            job = message.get("reply_job") or {}
            decision = job.get("decision") or {}
            message["can_reopen_handoff"] = (
                message is latest_incoming and selected.get("status") == "needs_human"
                and not selected.get("owner") and job.get("state") == "ready"
                and selected.get("handoff_reason") == decision.get("reason")
                and recoverable_handoff(message["body"], decision)
            )
            message["can_review_reply"] = (
                latest_incoming is not None and selected.get("status") == "human_took_over"
                and selected.get("owner") == account.get("user_id")
                and selected["companies"].get("status") != "opted_out"
                and (message.get("reply_to_id") or message["id"]) == latest_incoming["id"]
            )
            message["can_compose_reply"] = (message is latest_incoming and message["can_review_reply"]
                and not any(m["status"] in {"draft", "approved", "sent"} for m in reply_groups.get(message["id"], [])))
            message["can_draft_reply"] = (latest_incoming is not None and selected.get("status") == "ai_handles"
                and selected["companies"].get("status") != "opted_out"
                and (message.get("reply_to_id") or message["id"]) == latest_incoming["id"])
        messages = sorted(
            (message for message in rows if message["id"] in latest_replies or
             (not message.get("reply_to_id") and message["status"] in {"sent", "received", "draft"})),
            key=lambda message: (conversation_timestamp(message), message["id"]), reverse=True,
        )
        for message in messages:
            message["timeline_at"] = conversation_timestamp(message).isoformat()
    return render(request, "dashboard.html", sections=SECTIONS, section="conversations", account=account,
                  conversations=threads, selected_conversation=selected, conversation_messages=messages,
                  conversation_history=history, sync_stats=sync_stats, sync_error=sync_error)


async def read_companies(company_id=None):
    params = {
        "select": (
            "id,name,website,score,status,created_at,qualification_status,missing_information,"
            "research_errors,research_summary,contact_name,contact_role,contact_email,"
            "primary_contact_id,backup_contact_id,"
            "reasons(id,criterion,finding,points,source_url,quote,evidence_status),"
            "threads(id,status,handoff_reason,gmail_thread_id),"
            "company_reviews(id,decision,reviewer_name,reviewed_at,notes,criteria),"
            "company_contacts!company_contacts_company_id_fkey(id,email,name,role,contact_type,sources,publication_status,delivery_status,provider_status,provider_confidence)"
        ),
        "order": "id.asc", "reasons.order": "id.asc", "company_reviews.order": "id.desc",
        "company_contacts.order": "id.asc",
    }
    if company_id is None:
        params["company_reviews.limit"] = 1
    else:
        params["id"] = f"eq.{company_id}"
    result = await supabase("rest/v1/companies", "SUPABASE_SECRET_KEY", params=params)
    if not result.is_success:
        raise HTTPException(503, "Company data is unavailable. Check the schema and company review migration.")
    companies = result.json()
    for company in companies:
        company["review"] = next(iter(company["company_reviews"]), None)
        company["human_status"] = company["review"]["decision"] if company["review"] else "pending"
        qualification = company["qualification_status"]
        company["ai_status"] = (
            "potential_fit" if qualification in {"qualified_for_review", "fits_profile_no_recent_signal_found"}
            else qualification or "unavailable"
        )
        for reason in company["reasons"]:
            reason["safe_url"] = public_link(reason["source_url"])
        company["email_evidence"] = next((
            reason for reason in company["reasons"]
            if company["contact_email"] and reason["criterion"] == "contact"
            and reason["finding"] in CONTACT_EMAIL_LABELS.values()
            and reason["safe_url"] and company["contact_email"] in (reason["quote"] or "")
        ), None)
        for contact in company["company_contacts"]:
            for source in contact["sources"]:
                source["safe_url"] = public_link(source["url"])
        company["contact_readiness"] = contact_readiness(company)
        warnings = [MISSING_LABELS.get(item, item) for item in company["missing_information"]]
        company["warnings"] = [f"Check {label.lower()}" for label in warnings]
        if any(reason["evidence_status"] == "estimate" for reason in company["reasons"]):
            company["warnings"].append("Check sourced estimates")
        if company["research_errors"]:
            company["warnings"].append("Review research issues")
        if company["ai_status"] == "poor_fit":
            company["warnings"].append("Verify the reported mismatch")
    return companies


def public_link(value):
    try:
        url = urlsplit(value or "")
        if url.scheme in {"http", "https"} and url.hostname and not url.username:
            return value
    except ValueError:
        pass
    return None


SCORE_EVIDENCE_LABELS = {
    "verified": "The saved evidence supports the criterion.",
    "estimate": "A sourced estimate falls within the target range.",
    "missing": "No usable evidence was captured.",
    "unclear": "The available evidence was inconclusive.",
    "conflicting": "The sources disagree.",
    "range_crosses_target": "The range crosses the target limits.",
    "mismatch": "The evidence indicates a profile mismatch.",
    "estimate_outside_target": "A sourced estimate falls outside the target range.",
}


def company_summary(company):
    stored = company.get("research_summary") or {}
    groups = {}
    for reason in company["reasons"]:
        groups.setdefault(reason["criterion"], []).append(reason)
    breakdown = stored.get("score_breakdown", {})
    timing = [reason for category in ("hiring", "funding", "new_channel")
              for reason in groups.get(category, [])]
    score_rows = []
    for key, label, maximum in (
        ("location", "US location", 25), ("ecommerce", "Online product sales", 25),
        ("revenue", "Annual revenue", 15), ("headcount", "Employees", 15),
        ("timing", "Recent business signal", 20),
    ):
        evidence_key = "size" if key in {"revenue", "headcount"} else key
        reasons = timing if key == "timing" else groups.get(evidence_key, [])
        details = breakdown.get(key)
        if key == "timing":
            points = sum(reason["points"] for reason in reasons)
            explanation = "A recent signal earned points." if points else "No recent signal earned points."
        elif details:
            points = details["points"]
            explanation = SCORE_EVIDENCE_LABELS.get(details["evidence"], "See the saved evidence.")
        elif key in {"revenue", "headcount"}:
            points = None
            explanation = "Individual points were not captured. See the size evidence."
        else:
            points = sum(reason["points"] for reason in reasons)
            explanation = "See the saved evidence." if reasons else "No evidence was captured."
        score_rows.append({"label": label, "points": points, "maximum": maximum,
                           "explanation": explanation, "reasons": reasons})

    overview = stored.get("overview", {})
    for item in overview.values():
        item["safe_url"] = public_link(item["source_url"])
    sizes = stored.get("sizes", {})
    for entries in sizes.values():
        for item in entries:
            item["safe_url"] = public_link(item["source_url"])
    contact_source = next((reason for reason in groups.get("contact", [])
                           if reason["finding"] not in CONTACT_EMAIL_LABELS.values()), None)
    return {"overview": overview, "sizes": sizes, "locations": groups.get("location", []),
            "legacy_size": groups.get("size", []) if not stored else [],
            "timing": timing, "score_rows": score_rows, "contact_source": contact_source}


def render_company_view(request, account, companies, *, selected=None, filters=None, counts=None,
                        review_error=None, review_input=None, status=200):
    return render(
        request, "dashboard.html", status=status, sections=SECTIONS,
        section="companies" if filters is not None or selected else "overview",
        companies=companies, selected=selected, account=account, review_error=review_error,
        summary=company_summary(selected) if selected else None,
        review_input=review_input, core_criteria=CORE_REVIEW_CRITERIA, review_labels=REVIEW_LABELS,
        criterion_review_labels=CRITERION_REVIEW_LABELS, sales_statuses=SALES_STATUSES,
        ai_labels=AI_FILTERS, missing_labels=MISSING_LABELS, filters=filters, counts=counts,
        research=research_state(), company_limit=COMPANY_LIMIT, default_company_count=DEFAULT_COMPANY_COUNT,
        contact_type_labels=CONTACT_TYPE_LABELS,
    )


@app.post("/companies/{company_id}/review")
async def record_review(request: Request, company_id: int):
    check_form_origin(request)
    account = await require_account(request)
    companies = await read_companies(company_id)
    if not companies:
        raise HTTPException(404, "Company not found.")

    form = await request.form()
    decision = str(form.get("decision", ""))
    notes = str(form.get("notes", "")).strip()
    error = None
    if decision not in REVIEW_LABELS or decision == "pending":
        error = "Choose a human review decision."
    elif len(notes) > 500:
        error = "Keep the optional note to 500 characters or fewer."
    if error:
        return render_company_view(
            request, account, companies, selected=companies[0], review_error=error, status=422,
            review_input={"decision": decision, "notes": notes},
        )

    result = await supabase(
        "rest/v1/rpc/record_company_review", "SUPABASE_SECRET_KEY", method="POST",
        json={"p_company": company_id, "p_reviewer": account["user_id"], "p_decision": decision,
              "p_notes": notes, "p_criteria": None},
    )
    if not result.is_success:
        return render_company_view(
            request, account, companies, selected=companies[0], status=503,
            review_error="The review could not be saved. No company evidence was changed.",
            review_input={"decision": decision, "notes": notes},
        )
    if result.json() is None:
        raise HTTPException(403, "Only active employees can record a review.")
    return RedirectResponse(f"/dashboard/companies?company_id={company_id}#human-review", status_code=303)


async def meetings_page(request, account):
    bookings, offers = await asyncio.gather(
        supabase("rest/v1/bookings", "SUPABASE_SECRET_KEY", params={
            "select": ("id,thread_id,start_time,status,threads(companies(name,contact_name)),"
                       "acceptance:messages!bookings_acceptance_message_id_fkey(review_events)"),
            "order": "start_time.asc",
        }),
        supabase("rest/v1/messages", "SUPABASE_SECRET_KEY", params={
            "select": "id,thread_id,actual_recipient,offered_slots,sent_at,threads(companies(name,contact_name,status))",
            "status": "eq.sent", "offered_slots": "neq.[]", "order": "sent_at.desc,id.desc",
        }),
    )
    if not bookings.is_success or not offers.is_success:
        raise HTTPException(503, "Meetings could not be loaded.")
    items = []
    now = datetime.now(timezone.utc)
    booked_threads = set()
    for booking in bookings.json():
        company = booking["threads"]["companies"]
        events = (booking.get("acceptance") or {}).get("review_events", [])
        attempt = next((event for event in events if event["action"] == "booking_requested"), {})
        confirmed = next((event for event in events if event["action"] == "booking_confirmed"), {})
        start = instant(booking["start_time"])
        zone = (attempt.get("slot") or {}).get("zone", "UTC")
        local = start.astimezone(ZoneInfo(zone))
        ends = instant(confirmed.get("end") or booking["start_time"])
        status = "Cancelled" if booking["status"] == "cancelled" else "Upcoming" if ends >= now else "Completed"
        items.append({"key": f"booking-{booking['id']}", "company": company["name"],
                      "contact": attempt.get("contact_name") or company.get("contact_name"),
                      "email": attempt.get("email"), "phone": attempt.get("phone"),
                      "zone": zone, "status": status, "thread": booking["thread_id"],
                      "link": public_link(confirmed.get("link")), "time": slot_label({"start": booking["start_time"], "zone": zone}),
                      "day": local.date().isoformat(), "clock": local.strftime("%I:%M %p %Z")})
        booked_threads.add(booking["thread_id"])
    for offer in offers.json():
        thread = offer["thread_id"]
        company = offer["threads"]["companies"]
        if thread in booked_threads or company.get("status") == "opted_out":
            continue
        booked_threads.add(thread)
        slots = [slot for slot in offer["offered_slots"] if instant(slot["start"]) > now]
        if not slots:
            continue
        items.append({"key": f"offer-{offer['id']}", "company": company["name"], "contact": company.get("contact_name"),
                      "email": offer.get("actual_recipient"), "zone": slots[0]["zone"], "status": "Pending",
                      "thread": thread, "time": ", ".join(slot_label(slot) for slot in slots)})
    counts = {status: sum(item["status"] == status for item in items)
              for status in ("Upcoming", "Pending", "Completed")}
    return render(request, "dashboard.html", sections=SECTIONS, section="meetings", account=account,
                  meetings=items, meeting_counts=counts)


@app.get("/dashboard/{section}")
async def dashboard(
    request: Request, section: str, company_id: int | None = None,
    thread_id: int | None = None, edit: UUID | None = None, notice: str | None = None, q: str = "",
    ai: str = "", human: str = "", sales: str = "", sort: str = "",
):
    account = await signed_in_account(request)
    if account is None:
        return sign_in_redirect()
    if section == "settings":
        if edit is not None and account["role"] != "admin":
            raise HTTPException(403, "Only admins can manage employees.")
        return await settings_page(request, account, edit=edit, notice=notice)
    if section not in SECTIONS:
        raise HTTPException(404)
    if section == "drafts":
        return await email_drafts_page(request, account)
    if section == "conversations":
        counts = request.query_params.get("sync_counts", "")
        sync_stats = None
        if re.fullmatch(r"[0-9]{1,7}(?:,[0-9]{1,7}){4}", counts):
            sync_stats = dict(zip(SYNC_COUNT_KEYS, map(int, counts.split(","))))
        return await conversations_page(request, account, thread_id, sync_stats=sync_stats,
                                        sync_error=SYNC_ERRORS.get(request.query_params.get("sync_error")))
    if section == "meetings":
        return await meetings_page(request, account)
    if section not in {"companies", "overview"}:
        return render(request, "dashboard.html", sections=SECTIONS, section=section, account=account)

    companies = await read_companies(company_id if section == "companies" else None)
    if section == "overview":
        counts = {status: sum(company["human_status"] == status for company in companies)
                  for status in REVIEW_LABELS}
        return render_company_view(request, account, companies, counts=counts)
    if company_id is not None:
        if not companies:
            raise HTTPException(404, "Company not found.")
        return render_company_view(request, account, companies, selected=companies[0])

    filters = {"q": q.strip()[:120], "ai": ai if ai in AI_FILTERS else "",
               "human": human if human in REVIEW_LABELS else "",
               "sales": sales if sales in SALES_STATUSES else "",
               "sort": sort if sort in {"latest", "score"} else "pending"}
    query = filters["q"].casefold()
    companies = [company for company in companies if (
        (not query or query in f"{company['name']} {company['website']}".casefold())
        and (not filters["ai"] or company["ai_status"] == filters["ai"])
        and (not filters["human"] or company["human_status"] == filters["human"])
        and (not filters["sales"] or company["status"] == filters["sales"])
    )]
    if filters["sort"] == "score":
        companies.sort(key=lambda company: (company["score"], company["id"]), reverse=True)
    elif filters["sort"] == "latest":
        companies.sort(key=lambda company: (company["created_at"], company["id"]), reverse=True)
    else:
        companies.sort(key=lambda company: (company["human_status"] != "pending", -company["id"]))
    return render_company_view(request, account, companies, filters=filters)
