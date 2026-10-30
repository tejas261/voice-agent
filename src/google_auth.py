"""Google's server-side OpenID Connect flow; no Google tokens are persisted."""

import base64
import hashlib
import hmac
from urllib.parse import urlencode, urlsplit

import httpx
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from src.accounts import Credentials
from src.config import Settings

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def configured(settings: Settings) -> bool:
    if not (settings.google_client_id and settings.google_client_secret.get_secret_value() and settings.google_redirect_uri):
        return False
    redirect = urlsplit(settings.google_redirect_uri)
    origin = urlsplit(settings.public_origin) if settings.public_origin else None
    if origin:
        return (redirect.scheme, redirect.netloc) == (origin.scheme, origin.netloc)
    return redirect.scheme == "http" and redirect.hostname in {"localhost", "127.0.0.1", "::1"}


def authorize_url(settings: Settings, state: str, nonce: str, verifier: str) -> str:
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": settings.google_client_id, "redirect_uri": settings.google_redirect_uri,
        "response_type": "code", "scope": "openid email", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })


def verify_identity(token: str, settings: Settings, nonce: str) -> dict:
    google_request = GoogleRequest()

    def request(url, method="GET", body=None, headers=None, **kwargs):
        return google_request(url, method=method, body=body, headers=headers, timeout=10)

    claims = id_token.verify_oauth2_token(token, request, settings.google_client_id)
    if (claims.get("aud") != settings.google_client_id
            or claims.get("azp", settings.google_client_id) != settings.google_client_id
            or not isinstance(claims.get("nonce"), str)
            or not hmac.compare_digest(claims["nonce"], nonce)
            or claims.get("email_verified") is not True):
        raise ValueError("Invalid Google identity")
    subject = claims.get("sub")
    email = claims.get("email")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 255 or not isinstance(email, str) or len(email) > 254:
        raise ValueError("Invalid Google identity")
    email = Credentials.valid_email(email)
    hosted_domain = claims.get("hd")
    authoritative = email.endswith("@gmail.com") or bool(isinstance(hosted_domain, str) and hosted_domain)
    return {"subject": subject, "email": email, "authoritative": authoritative}


async def exchange_code(settings: Settings, code: str, verifier: str, nonce: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(TOKEN_URL, data={
            "code": code, "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret.get_secret_value(),
            "redirect_uri": settings.google_redirect_uri, "grant_type": "authorization_code",
            "code_verifier": verifier,
        })
        response.raise_for_status()
        token = response.json().get("id_token")
    if not isinstance(token, str) or len(token) > 16384:
        raise ValueError("Missing Google identity token")
    import asyncio
    return await asyncio.to_thread(verify_identity, token, settings, nonce)
