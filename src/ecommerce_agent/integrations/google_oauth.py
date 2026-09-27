"""Minimal "Sign in with Google" (OAuth 2.0 authorization code flow + OpenID Connect).

Only the three calls that talk to (or on behalf of) Google live here, so tests can replace them:
building the consent-screen URL, exchanging the authorization code for tokens, and verifying
the returned ID token. The ID token's signature, audience, issuer and expiry are checked with
`google-auth` against Google's published keys - the email we trust comes only from that token.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPES = "openid email"


class GoogleOAuthError(Exception):
    """Anything that should abort a Google sign-in (network, rejected code, bad token)."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str | None
    email_verified: bool
    nonce: str | None


def generate_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorization_url(
    *, client_id: str, redirect_uri: str, state: str, nonce: str, code_challenge: str
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


def exchange_code(
    *, code: str, client_id: str, client_secret: str, redirect_uri: str, code_verifier: str
) -> str:
    """Server-to-server code exchange. Returns the raw (not yet verified) ID token."""
    try:
        response = httpx.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise GoogleOAuthError(f"Token request failed: {exc}") from exc
    if response.status_code != 200:
        raise GoogleOAuthError(f"Token endpoint returned HTTP {response.status_code}")
    token = response.json().get("id_token")
    if not token:
        raise GoogleOAuthError("Token response did not include an id_token")
    return token


def verify_id_token(token: str, *, client_id: str) -> GoogleIdentity:
    """Verifies signature (Google's JWKS), `aud`, `iss` and `exp`. Raises GoogleOAuthError."""
    try:
        claims = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=client_id, clock_skew_in_seconds=10
        )
    except (ValueError, google_auth_exceptions.GoogleAuthError) as exc:
        raise GoogleOAuthError(f"Invalid ID token: {exc}") from exc
    sub = claims.get("sub")
    if not sub:
        raise GoogleOAuthError("ID token has no subject")
    return GoogleIdentity(
        sub=str(sub),
        email=claims.get("email"),
        email_verified=claims.get("email_verified") is True,
        nonce=claims.get("nonce"),
    )
