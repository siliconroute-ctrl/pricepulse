"""Who is calling: checks the sign-in token sent by the app.

PricePulse uses the same Firebase accounts as TrolleyUp (Google, or email and
password). The app sends the Firebase ID token; we check Google's signature on it.
Nothing here reads or writes TrolleyUp's data.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from cryptography import x509

log = logging.getLogger("pricepulse.auth")
CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"


class AuthError(Exception):
    """The token is missing, expired or not ours. Message is safe to show."""


@dataclass(frozen=True)
class Identity:
    uid: str
    email: str = ""
    provider: str = ""


def _fetch_certs() -> tuple[dict[str, str], float]:
    with urllib.request.urlopen(CERTS_URL, timeout=10) as resp:  # noqa: S310 - fixed https URL
        max_age = 3600.0
        for part in resp.headers.get("Cache-Control", "").split(","):
            if part.strip().startswith("max-age="):
                max_age = float(part.strip().split("=", 1)[1])
        return json.loads(resp.read()), max_age


class FirebaseVerifier:
    """Verifies Firebase ID tokens for one project."""

    def __init__(self, project_id: str, fetch: Callable[[], tuple[dict[str, str], float]] = _fetch_certs):
        self.project_id = project_id
        self._fetch = fetch
        self._certs: dict[str, str] = {}
        self._expires = 0.0

    def _key(self, kid: str):  # noqa: ANN202
        if kid not in self._certs or time.time() > self._expires:
            try:
                self._certs, max_age = self._fetch()
                self._expires = time.time() + max_age
            except Exception as exc:  # noqa: BLE001
                log.error("Could not fetch Google's sign-in keys: %s", exc)
                if kid not in self._certs:
                    raise AuthError("Sign-in could not be checked right now. Please try again.") from exc
        pem = self._certs.get(kid)
        if not pem:
            raise AuthError("Please sign in again.")
        return x509.load_pem_x509_certificate(pem.encode()).public_key()

    def verify(self, token: str) -> Identity:
        try:
            kid = jwt.get_unverified_header(token).get("kid", "")
            claims = jwt.decode(
                token,
                self._key(kid),
                algorithms=["RS256"],
                audience=self.project_id,
                issuer=f"https://securetoken.google.com/{self.project_id}",
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
                leeway=30,
            )
        except AuthError:
            raise
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Your sign-in has expired. Please sign in again.") from exc
        except Exception as exc:  # noqa: BLE001 - any bad token is the same to the caller
            raise AuthError("Please sign in again.") from exc
        provider = (claims.get("firebase") or {}).get("sign_in_provider", "")
        if not claims.get("sub") or provider in ("", "anonymous"):
            raise AuthError("Please sign in with Google or with your email and password.")
        return Identity(uid=claims["sub"], email=claims.get("email", ""), provider=provider)


class DevVerifier:
    """FOR LOCAL TESTING ONLY (PP_AUTH_DEV=1): accepts tokens like "dev:alice"."""

    def verify(self, token: str) -> Identity:
        if not token.startswith("dev:") or len(token) < 5 or len(token) > 80:
            raise AuthError("Please sign in again.")
        uid = token[4:]
        return Identity(uid="dev-" + uid, email=f"{uid}@example.test", provider="dev")


def make_verifier(settings):  # noqa: ANN001, ANN201
    if settings.auth_dev:
        log.warning("PP_AUTH_DEV is ON: anyone can sign in as anyone. Never use this on a public server.")
        return DevVerifier()
    return FirebaseVerifier(settings.firebase_project)
