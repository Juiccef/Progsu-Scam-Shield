"""Sign-in with Auth0: checks the access token a caregiver's browser or app sends.

Auth0 proves who someone is. Whether that person may watch calls is decided here, by
CAREGIVER_EMAILS. Tokens are checked on this server against Auth0's public keys, so
nothing on a call waits for Auth0, and a caregiver stays signed in if Auth0 is down.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Iterable, Optional

import httpx
import jwt

log = logging.getLogger("shield.auth")

# Put on every access token by the post-login Action in Auth0 (docs/auth0-sign-in.md, step 5).
EMAIL_CLAIM = "https://palisade/email"
EMAIL_VERIFIED_CLAIM = "https://palisade/email_verified"
KEYS_EVERY_S = 600  # an unknown key ID makes us re-read Auth0's keys at most this often
KEYS_RETRY_S = 15  # or this soon after a read that failed


class SignInError(Exception):
    """Why a token was refused. `status` is the HTTP status to answer with."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status


class Auth0:
    def __init__(
        self,
        domain: str,
        audience: str,
        caregiver_emails: Iterable[str],
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.issuer = f"https://{domain}/"
        self.audience = audience
        self.caregivers = frozenset(email.strip().lower() for email in caregiver_emails if email.strip())
        self._client = httpx.AsyncClient(timeout=5, transport=transport)
        self._keys: dict[str, object] = {}
        self._next_read = 0.0
        self._reading = asyncio.Lock()

    async def caregiver(self, token: str) -> str:
        """The email of the caregiver this access token belongs to. Raises SignInError otherwise."""
        try:
            key = await self._key(jwt.get_unverified_header(token).get("kid") or "")
            if key is None:
                raise SignInError(401, "Sign in again")
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],  # never the token's own choice of algorithm
                audience=self.audience,
                issuer=self.issuer,
                leeway=30,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise SignInError(401, "Sign in again") from exc
        email = str(claims.get(EMAIL_CLAIM) or "").lower()
        if not email or claims.get(EMAIL_VERIFIED_CLAIM) is not True:
            raise SignInError(403, "This account has no verified email address")
        if email not in self.caregivers:
            raise SignInError(403, "This account is not on the caregiver list")
        return email

    async def _key(self, kid: str) -> Optional[object]:
        """Auth0's public key with this ID. A flood of made-up IDs cannot make us ask Auth0 each time."""
        if kid not in self._keys and time.monotonic() >= self._next_read:
            async with self._reading:
                if kid not in self._keys and time.monotonic() >= self._next_read:
                    await self._read_keys()
        return self._keys.get(kid)

    async def _read_keys(self) -> None:
        try:
            response = await self._client.get(self.issuer + ".well-known/jwks.json")
            response.raise_for_status()
            self._keys = {
                key["kid"]: jwt.PyJWK(key).key for key in response.json()["keys"] if key.get("use", "sig") == "sig"
            }
            self._next_read = time.monotonic() + KEYS_EVERY_S
        except (httpx.HTTPError, jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            log.warning("Could not read Auth0's signing keys: %s", type(exc).__name__)
            self._next_read = time.monotonic() + KEYS_RETRY_S

    async def aclose(self) -> None:
        await self._client.aclose()
