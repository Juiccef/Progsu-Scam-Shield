"""Sign-in with Auth0: which tokens the server accepts, and from whom."""

import asyncio
import base64
import hashlib
import hmac
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from auth import EMAIL_CLAIM, EMAIL_VERIFIED_CLAIM, Auth0, SignInError
from main import create_app
from tests.test_server import make_settings

DOMAIN = "palisade-test.us.auth0.com"
AUDIENCE = "https://palisade-api"
CAREGIVER = "daughter@example.com"

AUTH0_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
SOMEONE_ELSES_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWKS = {"keys": [{**jwt.algorithms.RSAAlgorithm.to_jwk(AUTH0_KEY.public_key(), as_dict=True), "kid": "k1", "use": "sig"}]}


def token(key=AUTH0_KEY, *, kid="k1", algorithm="RS256", **claims) -> str:
    """An access token as Auth0 would issue it for a signed-in caregiver, with whatever is changed for a test."""
    payload = {
        "iss": f"https://{DOMAIN}/",
        "aud": [AUDIENCE, f"https://{DOMAIN}/userinfo"],
        "sub": "google-oauth2|1234567890",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        EMAIL_CLAIM: CAREGIVER,
        EMAIL_VERIFIED_CLAIM: True,
        **claims,
    }
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": kid})


def auth0(reads=None) -> Auth0:
    def handler(request):
        if reads is not None:
            reads.append(str(request.url))
        return httpx.Response(200, json=JWKS)

    return Auth0(DOMAIN, AUDIENCE, [CAREGIVER, " Son@Example.com "], transport=httpx.MockTransport(handler))


def check(access_token: str, verifier=None) -> str:
    return asyncio.run((verifier or auth0()).caregiver(access_token))


def refused(access_token: str) -> int:
    with pytest.raises(SignInError) as error:
        check(access_token)
    return error.value.status


# --- the token check ---------------------------------------------------------


def test_a_signed_in_caregiver_is_recognized_by_email():
    reads = []
    assert check(token(), auth0(reads)) == CAREGIVER
    assert reads == [f"https://{DOMAIN}/.well-known/jwks.json"]
    assert check(token(**{EMAIL_CLAIM: "SON@example.com"})) == "son@example.com"  # the list is not case-sensitive


def test_tokens_that_auth0_did_not_issue_for_this_api_are_refused():
    assert refused("") == 401
    assert refused("not.a.token") == 401
    assert refused(token(SOMEONE_ELSES_KEY)) == 401  # right key ID, wrong signature
    assert refused(token(kid="k9")) == 401  # a key Auth0 does not have
    assert refused(token(aud="https://some-other-api")) == 401
    assert refused(token(iss="https://evil.example.com/")) == 401
    assert refused(token(exp=int(time.time()) - 3600)) == 401


def test_the_token_does_not_get_to_choose_its_own_algorithm():
    public_pem = AUTH0_KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    claims = jwt.decode(token(), options={"verify_signature": False})

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    # Signed with Auth0's *public* key as an HMAC secret: the classic way around a careless check.
    signed = part({"alg": "HS256", "typ": "JWT", "kid": "k1"}) + "." + part(claims)
    signature = base64.urlsafe_b64encode(hmac.new(public_pem, signed.encode(), hashlib.sha256).digest()).rstrip(b"=")
    assert refused(signed + "." + signature.decode()) == 401
    assert refused(part({"alg": "none", "typ": "JWT", "kid": "k1"}) + "." + part(claims) + ".") == 401


def test_signing_in_is_not_enough_the_email_must_be_verified_and_on_the_list():
    assert refused(token(**{EMAIL_CLAIM: "stranger@example.com"})) == 403
    assert refused(token(**{EMAIL_VERIFIED_CLAIM: False})) == 403  # anyone can sign up with someone else's address
    assert refused(token(**{EMAIL_VERIFIED_CLAIM: "true"})) == 403  # the claim must be the boolean
    assert refused(token(**{EMAIL_CLAIM: ""})) == 403


def test_made_up_key_ids_do_not_make_the_server_ask_auth0_each_time():
    reads = []
    verifier = auth0(reads)

    async def flood():
        for number in range(25):
            with pytest.raises(SignInError):
                await verifier.caregiver(token(kid=f"bogus-{number}"))
        return await verifier.caregiver(token())

    assert asyncio.run(flood()) == CAREGIVER  # the real key was read once and still works
    assert len(reads) == 1


def test_a_failed_key_read_is_tried_again_soon_and_does_not_lock_everyone_out(monkeypatch):
    answers = [httpx.Response(503), httpx.Response(200, json=JWKS)]
    verifier = Auth0(DOMAIN, AUDIENCE, [CAREGIVER], transport=httpx.MockTransport(lambda request: answers.pop(0)))

    async def scenario():
        with pytest.raises(SignInError):
            await verifier.caregiver(token())
        verifier._next_read = 0.0  # as if the retry delay had passed
        return await verifier.caregiver(token())

    assert asyncio.run(scenario()) == CAREGIVER


# --- the server --------------------------------------------------------------


def app_with_sign_in(tmp_path, **settings):
    env = {
        "AUTH0_DOMAIN": DOMAIN,
        "AUTH0_AUDIENCE": AUDIENCE,
        "AUTH0_WEB_CLIENT_ID": "web-client",
        "AUTH0_IOS_CLIENT_ID": "ios-client",
        "CAREGIVER_EMAILS": CAREGIVER,
        **settings,
    }
    return create_app(make_settings(tmp_path, **env), stt_factory=None, sign_in=auth0())


def bearer(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def test_calls_and_demo_calls_need_a_signed_in_caregiver(tmp_path):
    with TestClient(app_with_sign_in(tmp_path, DEMO_MODE="true")) as client:
        assert client.get("/calls").status_code == 401
        assert client.get("/calls", headers=bearer(token())).json() == {"calls": []}
        assert client.get("/demo/scripts", headers=bearer(token())).json()["enabled"] is True
        stranger = client.get("/calls", headers=bearer(token(**{EMAIL_CLAIM: "stranger@example.com"})))
        assert stranger.status_code == 403 and stranger.json()["detail"] == "This account is not on the caregiver list"
        assert client.post("/demo/call?script=scam_01", headers=bearer(token(SOMEONE_ELSES_KEY))).status_code == 401
        assert client.get("/calls?token=" + token()).status_code == 401  # never accepted from the URL
        assert client.get("/health").status_code == 200  # still open: it holds no call data


def test_the_shared_token_still_works_only_while_it_is_set(tmp_path):
    with TestClient(app_with_sign_in(tmp_path, EVENTS_TOKEN="s3cret")) as client:
        assert client.get("/calls?token=s3cret").status_code == 200
        assert client.get("/calls", headers=bearer(token())).status_code == 200
        assert client.get("/calls?token=wrong").status_code == 401
    with TestClient(app_with_sign_in(tmp_path)) as client:
        assert client.get("/calls?token=s3cret").status_code == 401


def test_clients_learn_from_the_server_where_to_sign_in(tmp_path):
    with TestClient(app_with_sign_in(tmp_path)) as client:
        assert client.get("/auth/config").json() == {
            "enabled": True,
            "domain": DOMAIN,
            "audience": AUDIENCE,
            "web_client_id": "web-client",
            "ios_client_id": "ios-client",
        }
    with TestClient(create_app(make_settings(tmp_path), stt_factory=None)) as client:
        assert client.get("/auth/config").json() == {"enabled": False}
        assert client.get("/calls").status_code == 200  # no sign-in and no token configured: open, as before
