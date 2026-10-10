# Vultr deployment

The backend and Spectrum service run on the same Linux VM. Spectrum initiates
its cloud connection; it needs no public incoming port. The backend listens on
loopback, with the existing HTTPS reverse proxy serving the caregiver app and
Twilio. **Twilio receives and forwards the senior's SMS. Photon powers the
caregiver's iMessage agent.** A shared Photon line is sufficient; no dedicated
line is required for this architecture.

## Provision and stage

1. Keep the existing SMS-capable Twilio number as `TWILIO_PROXY_NUMBER`. Set
   `SENIOR_PHONE` to the senior's actual phone and `CAREGIVER_PHONE` to the
   caregiver's actual phone. In Photon **Users**, add the caregiver's iMessage
   phone/email, not the Twilio number. Obtain Spectrum project credentials and
   establish the caregiver conversation using their assigned **Texts on** number.
   Leave `PHOTON_LINE` empty for the shared pool. Configure the Twilio number's
   incoming Messaging webhook to `https://PUBLIC_HOST/sms/incoming`, **POST**;
   `tools/set_webhook.py --sms-only` preserves the existing voice webhook.
2. Prepare `/opt/shield` as a checkout of this branch, a `shield` service account,
   Python 3.11+ and Node 22+. If you already have a backend service, adapt these
   templates rather than starting a second backend on the same port.
3. Install Python requirements into `/opt/shield/.venv`. In `/opt/shield/photon`,
   run `npm ci --ignore-scripts` and `npm run build`. The lockfile pins Spectrum.
4. Create `/etc/shield/backend.env` and `/etc/shield/photon.env`, readable only by
   root (systemd loads them). Use ordinary `KEY=value` lines, without `export`.
   Keep credentials out of command arguments, Git and screenshots.

Backend environment: existing Twilio/STT/scorer settings, `PUBLIC_HOST`, and:

```dotenv
TEXT_GATEWAY_ENABLED=true
TEXT_FORWARDING_ENABLED=false
TWILIO_PROXY_NUMBER=+15555550100
SENIOR_PHONE=+15555550101
CAREGIVER_PHONE=+15555550102
PHOTON_CAREGIVER_ADDRESS=+15555550102
PHOTON_LINE=
TWILIO_VALIDATE_SIGNATURE=true
EVENTS_TOKEN=replace-with-caregiver-token
PHOTON_BRIDGE_TOKEN=replace-with-different-bridge-token
GATEWAY_ENCRYPTION_KEY=replace-with-generated-fernet-key
```

Photon environment:

```dotenv
SPECTRUM_PROJECT_ID=replace-with-project-id
SPECTRUM_PROJECT_SECRET=replace-with-project-secret
PHOTON_LINE=
PHOTON_CAREGIVER_ADDRESS=+15555550102
PHOTON_BRIDGE_TOKEN=match-backend-bridge-token
SHIELD_BACKEND_URL=http://127.0.0.1:8000
```

All numbers above are examples. The backend also needs its existing
`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `PUBLIC_HOST`. Live senior SMS
requires `DRY_RUN_ACTIONS=false`; keep the forwarding switch false during staging.
The forwarding sender is always `TWILIO_PROXY_NUMBER`, even when `TWILIO_SMS_FROM`
differs for call alerts. The caregiver address must match in both environments.
The earlier `PHOTON_GATEWAY_ENABLED`, `PHOTON_FORWARDING_ENABLED` and
`PHOTON_SENIOR_ADDRESS` variables no longer enable or configure the gateway.
Generate the encryption key once with
`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`.
Keep it stable across restarts; changing it prevents decryption of queued texts.
Do not back up the temporary queue database if you need the 24-hour body retention
limit to extend to backups. Content is encrypted in SQLite and purged on send,
discard or expiry; summaries remain.

## Services and HTTPS

Copy the supplied units into `/etc/systemd/system/`, adjust paths and the Node
binary if needed, then reload systemd and start both services. The backend unit
creates private `/var/lib/shield` storage. Keep **one backend worker and one
Spectrum service**. The signed Twilio webhook commits a pending record before
returning empty TwiML, and scoring resumes after restart. Only the Python worker
claims senior SMS jobs; Spectrum can claim only caregiver warnings. Provider
sends cannot guarantee exactly-once delivery across ambiguous network failures.
Unknown attempts are visible in the app and never automatically repeated.

Merge `nginx-shield.locations.conf` into the existing HTTPS server. Preserve any
existing specialized WebSocket locations. The internal Photon paths must stay
unavailable publicly. Keep `/sms/incoming` and `/gateway/sms/status/` publicly
reachable through HTTPS for Twilio's signed webhooks. Do not expose port 8000 externally. The app's server URL
must use HTTPS; its access token is `EVENTS_TOKEN`, not the bridge token.

## Acceptance checks before enabling forwarding

- `/health` reports gateway enabled, forwarding paused, `ingress: twilio`, and connected after the
  Spectrum process checks in. `connected` indicates a running SDK process with a
  recent heartbeat, not independently verified Photon transport health.
- Text the **Twilio number** from a third phone. An ordinary reminder queues
  without reaching the senior while forwarding is paused.
- Send a package-fee scam. It appears in **Texts → Needs review**, and the
  caregiver receives an iMessage warning. No original text/link goes in the warning.
- Review the held text; exercise discard, unsupported MMS (including captions),
  and caregiver `HELP`, `STATUS` and `WHY` **in the Photon iMessage conversation**.
- Restart both services and confirm that held/pending records survive.
- Confirm outbound SMS is enabled for your Twilio account and destination before
  judging (including trial recipient verification or applicable US registration).
- Set `TEXT_FORWARDING_ENABLED=true` with `DRY_RUN_ACTIONS=false` and restart the backend. **Previously
  queued staging messages will now be sent**, so use only consenting demo
  participants and clearly labeled examples during staging.
- Verify reminder delivery **from the Twilio number**, with the original sender
  and **Passed automated screening: low scam risk detected.** in the footer.
  A scam must not reach the senior before review. Release it deliberately using
  a consenting demo participant and verify **Released after caregiver review.**
  replaces the low-risk sentence. Observe queued → sending → sent, then delivered
  if a signed carrier callback arrives. “Sent” means Twilio accepted the request;
  “Delivered” does not mean read. Carrier failures appear as delivery failed.
- Repeat an ordinary voice call to verify the existing Twilio path.

Text content support is plain SMS, up to the provider's 1,600-character body
limit **including the footer**. Oversized texts and MMS are held, cannot be
released, and are never silently truncated. Incoming participant replies and
sender-number echoes are ignored: no two-way relay. Different original senders
share one Shield thread in Apple Messages, identified by their footer. Texts
addressed to the senior's personal number still bypass Shield. Do not claim native
iMessage/RCS screening on the Twilio number. Photon is only the caregiver agent.
Previous Photon-gateway records are preserved for review/discard but cannot be
released through this SMS transport. Shield's retention policy does not change
Twilio/Photon/model-provider retention.

## Rollback and monitoring

To pause senior forwarding, set `TEXT_FORWARDING_ENABLED=false` and restart the
backend. Already claimed jobs may finish; queued jobs remain paused. To stop all
caregiver iMessage sends, stop the Spectrum service; that does not stop Twilio
forwarding. Keep the existing call backend running. Disable `TEXT_GATEWAY_ENABLED`
only after reviewing the pending queue: expiration
cleanup runs only while the gateway is enabled.

Monitor `journalctl -u shield-backend -u shield-spectrum`, gateway queue counts,
the last successful send time, and held/delivery-unconfirmed states. The service
prints fixed diagnostics and disables SDK telemetry/logging to avoid message
content in logs. Check Photon-side retention separately from Shield's storage.
