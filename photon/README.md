# Shield Spectrum caregiver agent

Node 22+ service using `spectrum-ts` 12.10.1 and its hosted iMessage provider.
The lockfile overrides OpenTelemetry Core to 2.12.0 to remove the vulnerable
pre-2.8.0 transitive copies (GHSA-8988-4f7v-96qf). SDK telemetry is disabled.
Twilio receives protected SMS and the Python backend sends approved relay jobs
from that Twilio number with a screening footer. Spectrum handles a different
role: body-free caregiver iMessage warnings and `HELP`, `STATUS`, `WHY` commands.
It never ingests or forwards the senior's original texts.

```sh
npm ci --ignore-scripts
npm test
npm run build
node --env-file=../.env dist/index.js
```

Required environment: `SPECTRUM_PROJECT_ID`, `SPECTRUM_PROJECT_SECRET`,
`PHOTON_BRIDGE_TOKEN`, and `PHOTON_CAREGIVER_ADDRESS` (or `CAREGIVER_PHONE`).
`SHIELD_BACKEND_URL` defaults to `http://127.0.0.1:8000` and must be loopback.
The Python backend must have matching caregiver/token, `TEXT_GATEWAY_ENABLED=true`,
and its own caregiver token and encryption key.

**Shared Photon lines work.** Add the caregiver's actual iMessage phone/email in
Photon Users, then establish a conversation with their assigned **Texts on**
number. Leave `PHOTON_LINE` empty. If you have a dedicated line, set it in both
services to pin caregiver traffic to that line. The protected Twilio number and
senior's number are not Photon user destinations for this version.

Only inbound direct-message text from the configured caregiver is handled.
Groups, strangers, outbound echoes, attachments and control events are ignored.
Only `caregiver_alert` outbox jobs can be claimed or sent; senior relay jobs and
arbitrary destinations are rejected. Commands never release/discard a message;
those actions remain in the authenticated app. Sending timeouts are uncertain
outcomes, not retryable sends. Command replay suppression is process-local.

Unit tests inject transport/backend adapters and need no credentials or phones.
The actual SDK adapter is compiled by TypeScript. Live acceptance still requires
real credentials and the three-phone exchange described in
[Vultr deployment](../deploy/README.md).

For an isolated app preview, run `.venv/bin/python tools/demo_gateway.py` from
the repository root. In the simulator use server `http://127.0.0.1:8877` and
access token `demo-caregiver`. This uses temporary storage and dummy phone
numbers, seeds a held scam and an approved appointment, and never sends texts.
