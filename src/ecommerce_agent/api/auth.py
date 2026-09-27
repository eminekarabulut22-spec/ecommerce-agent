"""Email/password registration and login. Sessions are an opaque token in an httponly cookie,
checked against the `sessions` table on every request - no JWTs, no refresh flow, no password
reset yet (see README for scope notes).

"Sign in with Google" (OAuth code flow + OIDC, with state + nonce + PKCE) ends in the same
`_log_in()` as password login, so Google users get exactly the same session cookie.
"""

import base64
import binascii
import json
import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ecommerce_agent.api.dependencies import get_current_user, get_db_session
from ecommerce_agent.api.schemas import LoginRequest, RegisterRequest, UserResponse
from ecommerce_agent.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL,
    generate_session_token,
    hash_password,
    new_session_expiry,
    verify_password,
)
from ecommerce_agent.config import get_settings
from ecommerce_agent.db import repository
from ecommerce_agent.integrations import google_oauth
from ecommerce_agent.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_FLOW_COOKIE_NAME = "google_oauth"
GOOGLE_FLOW_COOKIE_PATH = "/auth/google"
GOOGLE_FLOW_TTL_SECONDS = 600
# Only these post-login destinations are allowed; anything else falls back to "/".
NEXT_PAGES = {"cart": "/cart.html", "orders": "/orders.html"}


def _secure_cookies() -> bool:
    """Mark cookies Secure (HTTPS-only) when the app is served over HTTPS, e.g. on Render.
    Plain-http localhost keeps working because the flag is then off."""
    return get_settings().frontend_base_url.startswith("https://")


def _log_in(response: Response, session: Session, user: User) -> None:
    token = generate_session_token()
    repository.create_session(session, token=token, user_id=user.id, expires_at=new_session_expiry())
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=_secure_cookies(),
    )


@router.post("/register", response_model=UserResponse, status_code=201)
def register(
    body: RegisterRequest,
    response: Response,
    session: Annotated[Session, Depends(get_db_session)],
) -> UserResponse:
    if repository.find_user_by_email(session, body.email) is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = User(email=body.email, password_hash=hash_password(body.password))
    try:
        user = repository.create_user(session, user)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail="An account with this email already exists."
        ) from exc
    _log_in(response, session, user)
    return UserResponse.from_user(user)


@router.post("/login", response_model=UserResponse)
def login(
    body: LoginRequest,
    response: Response,
    session: Annotated[Session, Depends(get_db_session)],
) -> UserResponse:
    user = repository.find_user_by_email(session, body.email)
    # Google-only accounts have no password_hash - they can't log in with a password.
    if (
        user is None
        or user.password_hash is None
        or not verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    _log_in(response, session, user)
    return UserResponse.from_user(user)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db_session)],
) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        repository.delete_session(session, token)
    response.delete_cookie(SESSION_COOKIE_NAME, secure=_secure_cookies())


@router.get("/me", response_model=UserResponse)
def me(user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    return UserResponse.from_user(user)


# --- Sign in with Google ---------------------------------------------------------------------


@router.get("/providers")
def providers() -> dict[str, bool]:
    """Which optional sign-in methods are configured, so the frontend can hide the rest."""
    return {"google": _google_config() is not None}


def _google_config() -> tuple[str, str, str] | None:
    """(client_id, client_secret, redirect_uri), or None if Google sign-in isn't configured."""
    settings = get_settings()
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        return None
    redirect_uri = f"{settings.frontend_base_url.rstrip('/')}/auth/google/callback"
    return settings.google_oauth_client_id, settings.google_oauth_client_secret, redirect_uri


def _safe_next(next_key: str | None) -> str | None:
    return next_key if next_key in NEXT_PAGES else None


def _encode_flow(flow: dict[str, str | None]) -> str:
    return base64.urlsafe_b64encode(json.dumps(flow).encode("utf-8")).decode("ascii")


def _decode_flow(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        flow = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
    except (ValueError, binascii.Error, UnicodeError):
        return None
    if not isinstance(flow, dict) or not all(
        isinstance(flow.get(k), str) and flow[k] for k in ("state", "nonce", "verifier")
    ):
        return None
    return flow


def _google_failure(next_key: str | None, reason: str) -> RedirectResponse:
    logger.warning("Google sign-in failed: %s", reason)
    url = "/account.html?error=google"
    if next_key:
        url += f"&next={next_key}"
    response = RedirectResponse(url, status_code=303)
    response.delete_cookie(
        GOOGLE_FLOW_COOKIE_NAME, path=GOOGLE_FLOW_COOKIE_PATH, secure=_secure_cookies()
    )
    return response


@router.get("/google/login")
def google_login(next: str | None = None) -> RedirectResponse:
    config = _google_config()
    if config is None:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured.")
    client_id, _, redirect_uri = config

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = google_oauth.generate_pkce_pair()
    url = google_oauth.build_authorization_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
        code_challenge=challenge,
    )
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        GOOGLE_FLOW_COOKIE_NAME,
        _encode_flow(
            {"state": state, "nonce": nonce, "verifier": verifier, "next": _safe_next(next)}
        ),
        max_age=GOOGLE_FLOW_TTL_SECONDS,
        path=GOOGLE_FLOW_COOKIE_PATH,
        httponly=True,
        # lax (not strict) so the cookie is sent on Google's top-level redirect back to us.
        samesite="lax",
        secure=_secure_cookies(),
    )
    return response


@router.get("/google/callback")
def google_callback(
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    flow = _decode_flow(request.cookies.get(GOOGLE_FLOW_COOKIE_NAME))
    next_key = _safe_next(flow.get("next")) if flow else None

    if flow is None:
        return _google_failure(None, "missing or malformed flow cookie")
    if not state or not secrets.compare_digest(state, flow["state"]):
        return _google_failure(next_key, "state mismatch")
    if error or not code:
        return _google_failure(next_key, f"authorization error: {error or 'no code'}")
    config = _google_config()
    if config is None:
        return _google_failure(next_key, "not configured")
    client_id, client_secret, redirect_uri = config

    try:
        raw_token = google_oauth.exchange_code(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            code_verifier=flow["verifier"],
        )
        identity = google_oauth.verify_id_token(raw_token, client_id=client_id)
    except google_oauth.GoogleOAuthError as exc:
        return _google_failure(next_key, str(exc))

    if not identity.nonce or not secrets.compare_digest(identity.nonce, flow["nonce"]):
        return _google_failure(next_key, "nonce mismatch")
    if not identity.email or not identity.email_verified:
        return _google_failure(next_key, "email missing or not verified")

    user = _find_or_create_google_user(session, identity.sub, identity.email.strip().lower())
    if user is None:
        return _google_failure(next_key, "account conflict")

    response = RedirectResponse(NEXT_PAGES.get(next_key or "", "/"), status_code=303)
    response.delete_cookie(
        GOOGLE_FLOW_COOKIE_NAME, path=GOOGLE_FLOW_COOKIE_PATH, secure=_secure_cookies()
    )
    _log_in(response, session, user)
    return response


def _find_or_create_google_user(session: Session, google_sub: str, email: str) -> User | None:
    """Match by Google account ID first, then link by verified email, else create a
    password-less user. None if the email already belongs to a *different* Google account."""
    user = repository.find_user_by_google_sub(session, google_sub)
    if user is not None:
        return user
    user = repository.find_user_by_email(session, email)
    if user is not None:
        if user.google_sub is not None:
            return None
        return repository.link_google_account(session, user.id, google_sub)
    try:
        return repository.create_user(
            session, User(email=email, password_hash=None, google_sub=google_sub)
        )
    except IntegrityError:
        session.rollback()
        return None
