# Sign-in with Auth0: step by step

This adds a real sign-in to Palisade. It is written for the setup as it is today: the
server runs on Vultr, and nothing here is run on a laptop except the tests.

**Today** one shared token is the only lock on live call transcripts and the call history.
It sits in the dashboard link, never expires and is the same for everyone.

**After this guide** each person signs in through Auth0 (Google, or an email and password),
and the server only accepts someone whose verified email is on your list. You can remove
one person without touching anyone else.

**What does not change:** the call itself. Twilio's webhooks, the audio, the scoring and
the hang-up never talk to Auth0, so a call is protected even if Auth0 is down.

| Part | What | Who | Rough time |
|---|---|---|---|
| A | Set up Auth0 in its console | You | 15 min |
| B | Server and dashboard code | Done on 2026-10-10 | - |
| C | Send it to the Vultr server and test | You | 10 min |
| D | Retire the old shared token | You | 5 min |
| E | Sign-in inside the iPhone app | Whoever works on the app | 1 to 2 hours |

Parts A to D give you sign-in on the web dashboard, which is what friends open on their
phones. The iPhone app keeps working during A to D with a token, and gets its own sign-in
in part E.

**How far this was tested (2026-10-10).** The server code and its tests ran in a copy of
this repo: 9 new tests, the whole suite passing with them, and each of six deliberate
weakenings of the token check made a test fail. The dashboard page was opened in real Chrome against that copy: it
showed a live demo call, and with sign-in switched on it showed the Sign In button. The
iPhone file in part E compiles against Auth0's library, version 3.1.0. **Not tested,
because it needs your Auth0 account:** the trip to Auth0's sign-in page and back, and a
real Auth0 token being accepted by the server. Step 15 is that test.

## Values you will collect

Keep this list open and fill it in as you go. Nothing in it is a secret.

| Name | Value | Where it comes from |
|---|---|---|
| Server address | `64-177-53-243.sslip.io` | `.venv/bin/python tools/vultr_deploy.py status` |
| `AUTH0_DOMAIN` | looks like `dev-abc123.us.auth0.com` | Step 3, the field "Domain" |
| `AUTH0_AUDIENCE` | `https://palisade-api` | You type it in step 2 |
| `AUTH0_WEB_CLIENT_ID` | a long string of letters and digits | Step 3, the field "Client ID" |
| `AUTH0_IOS_CLIENT_ID` | a long string of letters and digits | Step 4, the field "Client ID" |

---

## Part A: set up Auth0 (in the browser)

### Step 1. Create the account

1. Open `mlh.link/auth0-signup` and sign up. No card is needed.
2. If it asks about a tenant, choose the **US** region and a **Development** environment.

A new account starts with a 22-day trial of Auth0's paid features. Nothing in this guide
depends on them, so it keeps working on the free plan afterwards.

### Step 2. Create the API

This tells Auth0 that your server exists, so it can issue tokens meant for it.

1. Go to **Applications**, then **APIs**, then **+ Create API**.
2. Fill in:
   - **Name:** `Palisade API`
   - **Identifier:** `https://palisade-api`
   - **JSON Web Token (JWT) Profile:** `Auth0`
   - **JSON Web Token (JWT) Signing Algorithm:** `RS256`
3. If the form asks about an access policy for applications "within user flow", choose
   the option that allows your applications (it is called "Allow" or "All apps allowed").
4. Click **Create**.
5. Open the API's **Settings** tab, switch on **Allow Offline Access**, and save. This is
   what lets people stay signed in.

The Identifier is only a name. It is not a web address, nothing has to exist there, and it
cannot be changed later. Type it exactly: it has to match `AUTH0_AUDIENCE` in step 12.

### Step 3. Create the dashboard's application

1. Go to **Applications**, then **Applications**, then **Create Application**.
2. Name it `Palisade Dashboard`, choose **Single-Page Web Applications**, click **Create**.
3. Open its **Settings** tab. Copy **Domain** and **Client ID** into your list above.
4. Under **Application URIs**, fill in these three fields exactly:
   - **Allowed Callback URLs:** `https://64-177-53-243.sslip.io/dashboard`
   - **Allowed Logout URLs:** `https://64-177-53-243.sslip.io/dashboard`
   - **Allowed Web Origins:** `https://64-177-53-243.sslip.io`
5. Under **Refresh Token Rotation**, switch on **Allow Refresh Token Rotation**.
6. Click **Save Changes**.

If the server is ever deleted and created again, its address changes, and these three
fields have to be updated.

### Step 4. Create the iPhone app's application

This is only used in part E, but it takes a minute now.

1. **Create Application** again. Name it `Palisade iPhone`, choose **Native**, click **Create**.
2. On its **Settings** tab, copy **Client ID** into your list.
3. Put this one line in both **Allowed Callback URLs** and **Allowed Logout URLs**, with
   your own Domain from step 3 in place of `YOUR-DOMAIN`:

   ```text
   com.progsu.shield://YOUR-DOMAIN/ios/com.progsu.shield/callback
   ```

4. Switch on **Allow Refresh Token Rotation** and click **Save Changes**.

### Step 5. Add the Action that puts the email on the token

Auth0's tokens do not carry the person's email unless you ask for it, and the server
decides by email. Without this step the server refuses everyone with "This account has no
verified email address".

1. Go to **Actions**, then **Library**, then **Create Action**, then **Build from scratch**.
2. Name it `Add email to access token`, choose the trigger **Login / Post Login**, click **Create**.
3. Replace all the code in the editor with this:

   ```js
   exports.onExecutePostLogin = async (event, api) => {
     const namespace = "https://palisade";
     api.accessToken.setCustomClaim(`${namespace}/email`, event.user.email);
     api.accessToken.setCustomClaim(`${namespace}/email_verified`, event.user.email_verified === true);
   };
   ```

4. Click **Deploy**.
5. Go to **Actions**, then **Triggers** (some versions of the console call it **Flows**),
   and open **post-login** (or **Login**).
6. Drag `Add email to access token` into the flow, between Start and Complete, and click **Apply**.

### Step 6. Decide who can sign in

Nothing to change here to get going. Two things to know:

- **Google** sign-in is normally switched on already in a new account (look under
  **Authentication**, then **Social**), using Auth0's shared test keys. That is fine for a
  hackathon. Ask people to use "Continue with Google": Google has already verified their
  email.
- **Email and password** sign-up is also on, so a stranger can create an account. That
  account gets them nothing: the server refuses any email that is unverified or not on
  your list. To keep strangers out of your user list anyway, open **Authentication**, then
  **Database**, then `Username-Password-Authentication`, and switch on **Disable Sign Ups**.

---

## Part B: the server and the dashboard

**Done on 2026-10-10.** Steps 7 to 11, and step 13, are applied and committed in this
repo, and all 186 tests pass. They are kept here to show what changed. Do part A, then go
straight to step 12.

Four files change and two are new.

### Step 7. Add the new file `auth.py`

Create `auth.py` in the top folder of the repo, next to `main.py`:

```python
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
```

What it does, in order: it reads the token's signature against Auth0's public keys, checks
that Auth0 issued it for this API and that it has not expired, and then checks that the
email on it is verified and on your list. Only `RS256` is accepted, whatever the token
itself claims.

### Step 8. Edit `config.py`, `requirements.txt` and `main.py`

Lines that start with `+` are added and lines that start with `-` are removed. The rest is
there so you can find the place.

```diff
--- a/config.py
+++ b/config.py
@@ -73,6 +73,12 @@
     log_transcripts: bool
     log_dir: Path
     events_token: str
+    # Sign-in (Auth0). With no domain, sign-in is off and EVENTS_TOKEN alone guards the feed.
+    auth0_domain: str
+    auth0_audience: str  # the API's Identifier in Auth0
+    auth0_web_client_id: str  # the dashboard's application (Single Page)
+    auth0_ios_client_id: str  # the iPhone app's application (Native)
+    caregiver_emails: tuple[str, ...]  # who may watch calls once signed in
 
     @classmethod
     def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
@@ -118,6 +124,11 @@
             log_transcripts=_flag(env.get("LOG_TRANSCRIPTS"), False),
             log_dir=log_dir if log_dir.is_absolute() else ROOT / log_dir,
             events_token=get("EVENTS_TOKEN"),
+            auth0_domain=_host(get("AUTH0_DOMAIN")),
+            auth0_audience=get("AUTH0_AUDIENCE"),
+            auth0_web_client_id=get("AUTH0_WEB_CLIENT_ID"),
+            auth0_ios_client_id=get("AUTH0_IOS_CLIENT_ID"),
+            caregiver_emails=tuple(e.strip().lower() for e in get("CAREGIVER_EMAILS").split(",") if e.strip()),
         )
 
 
```

```diff
--- a/requirements.txt
+++ b/requirements.txt
@@ -3,6 +3,7 @@
 twilio>=9.0
 websockets>=14
 httpx>=0.27
+PyJWT[crypto]>=2.8
 anthropic>=1.0
 python-dotenv>=1.0
 pytest>=8.0
```

```diff
--- a/main.py
+++ b/main.py
@@ -25,6 +25,7 @@
 
 import demo
 from actions import Actions
+from auth import Auth0, SignInError
 from config import ROOT, Settings, get_settings
 from events import EventBus, mask_phone
 from history import CallHistory
@@ -60,7 +61,12 @@
 
 
 def create_app(
-    settings: Optional[Settings] = None, *, stt_factory=_DEFAULT, llm=_DEFAULT, twilio_client_factory=None
+    settings: Optional[Settings] = None,
+    *,
+    stt_factory=_DEFAULT,
+    llm=_DEFAULT,
+    twilio_client_factory=None,
+    sign_in=_DEFAULT,
 ) -> FastAPI:
     settings = settings or get_settings()
     stt: Optional[STTFactory] = make_stt_factory(settings) if stt_factory is _DEFAULT else stt_factory
@@ -94,6 +100,12 @@
         )
     )
     voice = {"voice": settings.say_voice} if settings.say_voice else {}
+    if sign_in is _DEFAULT:
+        sign_in = (
+            Auth0(settings.auth0_domain, settings.auth0_audience, settings.caregiver_emails)
+            if settings.auth0_domain
+            else None
+        )
 
     @asynccontextmanager
     async def lifespan(app: FastAPI):
@@ -113,6 +125,8 @@
         await registry.close_all()
         if scorer_llm is not None and llm is _DEFAULT:
             await scorer_llm.aclose()
+        if sign_in is not None:
+            await sign_in.aclose()
         bus.close()
 
     app = FastAPI(title="Palisade", lifespan=lifespan)
@@ -149,14 +163,21 @@
         state["public_host"] = public_host(request)  # where Twilio reaches us, for callback URLs
         return form
 
-    def authorize(request: Request) -> None:
-        """/events carries live transcripts, so EVENTS_TOKEN (when set) is required to read it."""
-        if not settings.events_token:
-            return
+    async def authorize(request: Request) -> None:
+        """/events carries live transcripts. A signed-in caregiver may read it, and so may EVENTS_TOKEN where set."""
         bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
         supplied = request.query_params.get("token") or bearer
-        if not hmac.compare_digest(supplied.encode(), settings.events_token.encode()):
-            raise HTTPException(401, "Missing or wrong token")
+        if settings.events_token and hmac.compare_digest(supplied.encode(), settings.events_token.encode()):
+            return
+        if sign_in is None:
+            if settings.events_token:
+                raise HTTPException(401, "Missing or wrong token")
+            return
+        try:
+            # Only from the Authorization header: a sign-in token never belongs in a URL, where it gets logged.
+            await sign_in.caregiver(bearer)
+        except SignInError as refused:
+            raise HTTPException(refused.status, str(refused)) from refused
 
     def twiml(response: VoiceResponse) -> Response:
         return Response(str(response), media_type="application/xml")
@@ -329,7 +350,7 @@
     @app.get("/events")
     async def events(request: Request, replay: bool = True) -> StreamingResponse:
         """Server-sent events: call_started, transcript, score, action, action_result, call_ended, ..."""
-        authorize(request)
+        await authorize(request)
         queue = bus.subscribe()
         backlog = bus.recent() if replay else []
 
@@ -357,7 +378,7 @@
     @app.get("/metrics/last")
     async def metrics_last(request: Request) -> dict:
         """Seconds from the triggering utterance to the most recent action, with a per-stage breakdown."""
-        authorize(request)
+        await authorize(request)
         last = state["last_action"]
         return {
             "seconds": last["seconds"] if last else None,
@@ -369,7 +390,7 @@
     @app.get("/calls")
     async def calls(request: Request) -> dict:
         """Recent calls, newest first: outcome, flags, actions and the risk timeline. Never transcript text."""
-        authorize(request)
+        await authorize(request)
         return {"calls": history.list()}
 
     # --- demo calls (DEMO_MODE=true) ---------------------------------------------
@@ -377,7 +398,7 @@
     @app.get("/demo/scripts")
     async def demo_scripts(request: Request) -> dict:
         """The scripts a demo call can play. Empty when demo mode is off, so a client can hide the feature."""
-        authorize(request)
+        await authorize(request)
         if not settings.demo_mode:
             return {"enabled": False, "scripts": []}
         return {"enabled": True, "scripts": [demo.describe(script) for script in demo.load_scripts(DEMO_SCRIPTS)]}
@@ -385,7 +406,7 @@
     @app.post("/demo/call", status_code=202)
     async def demo_call(request: Request, script: str, speed: float = 1.0) -> dict:
         """Start a scripted call with no phone involved. Same scoring and decisions, dry-run actions."""
-        authorize(request)
+        await authorize(request)
         if not settings.demo_mode:
             raise HTTPException(404, "Demo mode is off (set DEMO_MODE=true)")
         if registry.any_simulated():
@@ -408,6 +429,19 @@
         )
         return {"call_sid": call_sid, "script": matches[0]["id"]}
 
+    @app.get("/auth/config")
+    async def auth_config() -> dict:
+        """What the dashboard and the app need to send someone to sign in. None of it is secret."""
+        if sign_in is None:
+            return {"enabled": False}
+        return {
+            "enabled": True,
+            "domain": settings.auth0_domain,
+            "audience": settings.auth0_audience,
+            "web_client_id": settings.auth0_web_client_id,
+            "ios_client_id": settings.auth0_ios_client_id,
+        }
+
     @app.get("/health")
     async def health() -> dict:
         return {
```

In `main.py` the same one-word change, `authorize(request)` to `await authorize(request)`,
is made in five places. The new `/auth/config` address tells the dashboard and the app
which Auth0 account to use, so neither has Auth0 settings of its own.

### Step 9. Add the tests

Create `tests/test_auth.py`:

<details>
<summary>tests/test_auth.py (click to open)</summary>

```python
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
```

</details>

### Step 10. Edit `dashboard.html`

This also fixes a bug that has been there since the first version: the page connected but
never showed a call, because it listened for the wrong kind of event.

```diff
--- a/dashboard.html
+++ b/dashboard.html
@@ -20,6 +20,7 @@
   .pill.on { color: var(--ok); border-color: var(--ok); }
   .pill.off { color: var(--bad); border-color: var(--bad); }
   .pill.dry { color: var(--warn); border-color: var(--warn); }
+  button.pill { background: none; font: inherit; font-size: 12px; cursor: pointer; color: var(--accent); border-color: var(--accent); }
   main { display: grid; gap: 16px; padding: 16px 20px; grid-template-columns: minmax(280px, 1fr) minmax(320px, 2fr); max-width: 1200px; margin: 0 auto; }
   @media (max-width: 760px) { main { grid-template-columns: 1fr; } }
   .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
@@ -55,6 +56,9 @@
   <span class="pill" id="p-actions">actions: ?</span>
   <span class="pill" id="p-model">scorer: ?</span>
   <span class="pill" id="p-stt">stt: ?</span>
+  <span class="pill" id="p-user" hidden></span>
+  <button class="pill" id="sign-in" hidden>Sign In</button>
+  <button class="pill" id="sign-out" hidden>Sign Out</button>
 </header>
 
 <main>
@@ -193,13 +197,97 @@
 // Frame counts come from the throttled "media" events; show them without logging every one.
 function onMedia(ev) { const f = ev.frames || {}; $("c-frames").textContent = Object.entries(f).map(([k, v]) => k + " " + v).join(", "); }
 
-function connect() {
-  const es = new EventSource("/events?" + q);
-  es.onopen = () => { $("p-feed").textContent = "feed: live"; $("p-feed").className = "pill on"; };
-  es.onmessage = (m) => { const ev = JSON.parse(m.data); if (ev.type === "media") onMedia(ev); onEvent(ev); };
-  es.onerror = () => { $("p-feed").textContent = "feed: reconnecting"; $("p-feed").className = "pill off"; };
+// Sign-in. The server says whether it wants one and which Auth0 tenant to use (GET /auth/config).
+// Where it does, an old ?token= link is ignored and the page offers the sign-in instead.
+const AUTH0_SDK = "https://cdn.auth0.com/js/auth0-spa-js/2.28/auth0-spa-js.production.js";
+const here = location.origin + location.pathname;
+let signIn = null;        // the Auth0 client, when this server asks for a sign-in
+
+function feed(text, state) { $("p-feed").textContent = "feed: " + text; $("p-feed").className = "pill " + state; }
+
+function loadScript(src) {
+  return new Promise((resolve, reject) => {
+    const tag = document.createElement("script");
+    tag.src = src; tag.onload = resolve; tag.onerror = () => reject(new Error("could not load " + src));
+    document.head.append(tag);
+  });
 }
 
+async function setUpSignIn() {
+  const config = await (await fetch("/auth/config")).json();
+  if (!config.enabled) return;       // no sign-in on this server: a ?token= link works as before
+  await loadScript(AUTH0_SDK);
+  signIn = await auth0.createAuth0Client({
+    domain: config.domain,
+    clientId: config.web_client_id,
+    authorizationParams: { audience: config.audience, redirect_uri: here },
+    useRefreshTokens: true,          // Safari blocks the cookie that staying signed in would otherwise need
+    cacheLocation: "localstorage",
+  });
+  const back = new URLSearchParams(location.search);
+  if (back.has("state") && (back.has("code") || back.has("error"))) {     // returning from Auth0's page
+    try { await signIn.handleRedirectCallback(); }
+    catch (e) { feed(e.error_description || e.message || "sign-in failed", "off"); }
+    history.replaceState(null, "", location.pathname);
+  }
+  $("sign-in").onclick = () => signIn.loginWithRedirect();
+  $("sign-out").onclick = () => signIn.logout({ logoutParams: { returnTo: here } });
+}
+
+// The token for the feed, or null when someone has to sign in first.
+async function accessToken() {
+  try {
+    if (!(await signIn.isAuthenticated())) throw new Error("not signed in");
+    const accessToken = await signIn.getTokenSilently();
+    const user = await signIn.getUser();
+    $("p-user").textContent = (user && user.email) || "signed in"; $("p-user").hidden = false;
+    $("sign-in").hidden = true; $("sign-out").hidden = false;
+    return accessToken;
+  } catch (e) {
+    $("p-user").hidden = true; $("sign-out").hidden = true; $("sign-in").hidden = false;
+    return null;
+  }
+}
+
+// Reads the event feed with fetch, not EventSource: EventSource cannot send the Authorization
+// header a sign-in needs. Each event is one "data:" line of JSON.
+async function connect() {
+  const headers = {};
+  if (signIn) {
+    const accessTokenNow = await accessToken();
+    if (!accessTokenNow) { if ($("p-feed").className !== "pill off") feed("sign in to watch", ""); return; }
+    headers.Authorization = "Bearer " + accessTokenNow;
+  }
+  try {
+    const response = await fetch(signIn ? "/events" : "/events?" + q, { headers });
+    if (response.status === 401 || response.status === 403) {
+      // Not retried: signing in, or being added to the caregiver list, is what fixes it.
+      feed((await response.json()).detail, "off");
+      if (signIn && response.status === 401) { $("sign-in").hidden = false; }
+      return;
+    }
+    if (!response.ok) throw new Error("HTTP " + response.status);
+    feed("live", "on");
+    $("log").replaceChildren();        // the server replays recent events on every connection
+    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
+    let pending = "";
+    for (;;) {
+      const { value, done } = await reader.read();
+      if (done) break;
+      const lines = (pending + value).split("\n");
+      pending = lines.pop();
+      for (const line of lines) {
+        if (!line.startsWith("data:")) continue;
+        const ev = JSON.parse(line.slice(5));
+        if (ev.type === "media") onMedia(ev);
+        onEvent(ev);
+      }
+    }
+  } catch (e) { /* the connection dropped or never opened: try again below */ }
+  feed("reconnecting", "off");
+  setTimeout(connect, 2000);
+}
+
 async function health() {
   try {
     const h = await (await fetch("/health")).json();
@@ -209,7 +297,8 @@
   } catch (e) { $("p-actions").textContent = "server unreachable"; $("p-actions").className = "pill off"; }
 }
 
-connect(); health(); setInterval(health, 10000);
+setUpSignIn().catch((e) => feed(e.message, "off")).then(connect);
+health(); setInterval(health, 10000);
 </script>
 </body>
 </html>
```

### Step 11. Install the new package and run the tests

```bash
uv pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

Every test should pass, 9 more than before. Do not go on to part C if one fails: a server
that cannot start takes calls down with it.

---

## Part C: send it to the Vultr server and test

### Step 12. Add the settings

Open `.env.vultr` and add these five lines. Everything after each `=` is an example:
replace it with your own value from the list at the top, with no quotes and no spaces.

```text
AUTH0_DOMAIN=dev-abc123.us.auth0.com
AUTH0_AUDIENCE=https://palisade-api
AUTH0_WEB_CLIENT_ID=PasteTheDashboardClientIdHere
AUTH0_IOS_CLIENT_ID=PasteTheIphoneClientIdHere
CAREGIVER_EMAILS=you@gmail.com,friend@gmail.com
```

`CAREGIVER_EMAILS` is the list of people who may watch calls: the email each one signs in
with, separated by commas. Adding or removing someone later is an edit here and a push.

### Step 13. Tell git about the new files

The deploy tool only sends files that git tracks. Without this step the server would get a
`main.py` that needs `auth.py` and no `auth.py`, and it would not start.

```bash
git add auth.py tests/test_auth.py
git commit -m "Sign-in with Auth0 for the dashboard" -- auth.py tests/test_auth.py config.py main.py requirements.txt dashboard.html
```

The push also sends every other tracked file as it is on disk at that moment, committed
or not. Make sure nothing half-finished is sitting in the backend files first.

### Step 14. Push

```bash
.venv/bin/python tools/vultr_deploy.py push
```

It refuses while a call is in progress, because it restarts the server. It ends by
printing "Palisade is running at ...". If it prints "Palisade did not start" instead, the
log it shows says why: fix that and push again, because the server is down until you do.

### Step 15. Test it

1. **The server knows about Auth0.**

   ```bash
   curl -s https://64-177-53-243.sslip.io/auth/config
   ```

   Expect `"enabled":true` and your own domain and client IDs.

2. **The call history is closed to strangers.**

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://64-177-53-243.sslip.io/calls
   ```

   Expect `401`.

3. **Signing in works.** Open `https://64-177-53-243.sslip.io/dashboard` in a browser. It should say
   "feed: sign in to watch" and show a **Sign In** button. Click it, choose "Continue with
   Google", and use an email that is in `CAREGIVER_EMAILS`. You should land back on the
   dashboard with "feed: live", your email, and a **Sign Out** button.

4. **A call shows up.** Start a demo call from the iPhone app. The dashboard should show
   the transcript, the risk number rising, and "Palisade ended this call".

5. **Someone who is not on the list is refused.** Sign out, then sign in with a Google
   account that is not in `CAREGIVER_EMAILS`. Expect "feed: This account is not on the
   caregiver list".

6. **You stay signed in.** Sign in again with your own account and reload the page. It
   should go straight to "feed: live". Try the same on an iPhone in Safari.

---

## Part D: retire the old shared token

Until you do this, the old link with the old token in it still works, so nothing has
actually been locked. The server keeps one token for your own tools and, until part E, for
the iPhone app. It should be new, long, and never put in a link again.

### Step 16. Replace the token

1. Make a new one:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(24))"
   ```

2. In `.env.vultr`, replace the value after `EVENTS_TOKEN=` with it.
3. Push: `.venv/bin/python tools/vultr_deploy.py push`
4. Put the new token in the iPhone app's Settings, under Access Token.
5. Open the old dashboard link you shared earlier. It should now offer **Sign In** and
   show no calls.

From here on, give friends the plain address, `https://64-177-53-243.sslip.io/dashboard`, and add
their email to `CAREGIVER_EMAILS`.

---

## Part E: sign-in inside the iPhone app

This part changes files that are being worked on in the app, so do it together with
whoever is working on the app. Only step 18's file has been compiled. Steps 19 and 20
describe the change and have not been built.

### Step 17. Add Auth0's library in Xcode

1. In Xcode choose **File**, then **Add Package Dependencies...**
2. Paste `https://github.com/auth0/Auth0.swift` into the search field.
3. Set the rule to **Up to Next Major Version** starting at `3.1.0`, click **Add Package**,
   and add the product **Auth0** to the **Shield** target.

Nothing else is needed for the sign-in sheet to return to the app. Returning through a web
link instead needs a paid Apple developer account, which this project does not have.

### Step 18. Add the new file `ios/Shield/Services/SignIn.swift`

Add it to the Shield target, like the other files in `Services`:

```swift
import Auth0
import Foundation

/// What `GET /auth/config` on the server answers. The server says whether it wants a sign-in
/// and which Auth0 tenant to use, so the app keeps no Auth0 settings of its own.
struct SignInConfig: Decodable, Equatable, Sendable {
    let enabled: Bool
    var domain: String?
    var audience: String?
    var iosClientID: String?

    enum CodingKeys: String, CodingKey {
        case enabled, domain, audience
        case iosClientID = "ios_client_id"
    }
}

/// One caregiver's session with Auth0: signing in and out, and the token the server accepts.
/// Credentials are kept in the Keychain by Auth0's credentials manager, not in app settings.
final class SignIn: Sendable {
    let clientID: String
    let domain: String
    let audience: String
    private let manager: CredentialsManager

    init?(_ config: SignInConfig) {
        guard config.enabled, let domain = config.domain, let audience = config.audience,
              let clientID = config.iosClientID, !clientID.isEmpty
        else { return nil }
        self.clientID = clientID
        self.domain = domain
        self.audience = audience
        manager = CredentialsManager(authentication: Auth0.authentication(clientId: clientID, domain: domain))
    }

    /// Asks the server at `baseURL` whether it wants a sign-in. Nil when it does not, or cannot be reached.
    static func offered(by baseURL: URL) async -> SignIn? {
        guard let (data, _) = try? await URLSession.shared.data(from: baseURL.appending(path: "auth/config")),
              let config = try? JSONDecoder().decode(SignInConfig.self, from: data)
        else { return nil }
        return SignIn(config)
    }

    /// Opens Auth0's sign-in page in the system's sign-in sheet.
    func signIn() async throws {
        _ = try await Auth0
            .webAuth(clientId: clientID, domain: domain)
            .audience(audience)
            .scope("openid profile email offline_access")
            .useCredentialsManager(manager)
            .start()
    }

    func signOut() async throws {
        try await Auth0
            .webAuth(clientId: clientID, domain: domain)
            .useCredentialsManager(manager)
            .logout()
    }

    /// The signed-in caregiver's email, or nil when nobody is signed in.
    var email: String? {
        guard manager.canRenew() else { return nil }
        return (try? manager.userProfile())?.email
    }

    /// A token the server accepts, renewed when it has run out. Nil when nobody is signed in.
    func accessToken() async -> String? {
        guard manager.canRenew() else { return nil }
        return try? await manager.credentials().accessToken
    }
}
```

### Step 19. Use the sign-in's token for the connection (`ShieldStore.swift`, `ShieldAPI.swift`)

- When the app connects to a server, call `SignIn.offered(by:)` with the server's address
  and keep the result on the store, so Settings can show it.
- If the server offers a sign-in, each time the app is about to open the event feed it asks
  `signIn.accessToken()` and uses that as the bearer token, in place of the Access Token
  from Settings. `accessToken()` renews an expired token by itself.
- If `accessToken()` answers nil, nobody is signed in: show "Not Connected" with a way
  into Settings, as the app already does when it cannot connect.
- `ShieldAPI.token` is a constant today. It needs a way to make a copy of the same server
  with another token.

### Step 20. Offer Sign In and Sign Out (`SettingsView.swift`)

Where the Access Token field is now:

- if the server offers a sign-in and `signIn.email` is nil, show a **Sign In** button that
  calls `signIn.signIn()` and then reconnects;
- if `signIn.email` has a value, show it, with a **Sign Out** button that calls
  `signIn.signOut()` and then reconnects;
- if the server offers no sign-in, keep the Access Token field as it is.

The first time, iOS shows its own notice that the app wants to use `auth0.com` to sign in.
That is normal.

When this works on a phone, remove the token from the app's Settings: the app no longer
needs one.

---

## What this does and does not protect

**It protects** who can read live transcripts and the call history, and who can start demo
calls. Each person has their own sign-in, a token has to be renewed after a day unless you
set a shorter time on the API, one person can be removed, and Auth0 keeps a log of sign-ins.

**It does not change** the call path, which Twilio's signatures already guard; how well
scams are detected; or the server itself, where the Twilio, Deepgram and Anthropic keys
live. SSH on the server still accepts passwords.

**Limits of the free plan.** Sign-in with Google, email and passkeys is included, for up to
25,000 people a month. Multi-factor sign-in and Auth0's own roles are not, which is why the
list of allowed people lives in `CAREGIVER_EMAILS` on your server.

## If something goes wrong

| What you see | Likely cause | What to do |
|---|---|---|
| Auth0 shows "Callback URL mismatch" | The address in step 3 is not exactly the dashboard's | Check for `https`, the exact server address, `/dashboard`, and no slash at the end |
| Auth0 shows an error about "Service not found" | The API's Identifier and `AUTH0_AUDIENCE` differ | Make them identical, then push |
| Auth0 says the client is not authorized for the API | The API only allows applications it lists | On the API, open **Application Access** and authorize both applications |
| "feed: This account has no verified email address" | The Action in step 5 is not deployed, or not in the post-login flow | Redo step 5, sign out and sign in again |
| The same message, for a password account | The person has not clicked the link in Auth0's verification email | Click it, sign out and sign in again |
| "feed: This account is not on the caregiver list" | The email is not in `CAREGIVER_EMAILS` | Add it in `.env.vultr` and push |
| "feed: Sign in again" straight after signing in | `AUTH0_DOMAIN` or `AUTH0_AUDIENCE` on the server does not match Auth0 | Compare with the list at the top, fix `.env.vultr`, push |
| Asked to sign in again every day | Staying signed in is switched off | Step 2 item 5 and step 3 item 5 |
| The push prints "Palisade did not start" | Usually a new file that git does not track | Step 13, then push again |
