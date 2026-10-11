# Palisade's iMessage agent

A small Node service that tells the caregiver, by iMessage, when Palisade holds a text, and
that can also carry an approved text to the protected person. It uses Photon's Spectrum SDK
(`spectrum-ts` 12.10.1) and its hosted iMessage provider.

What it does, and what it does not:

- It asks the Palisade server for alerts waiting to go out and sends each to the
  caregiver. An alert is a fixed sentence that names the kind of warning. It never holds
  a text's words, and the agent is never given them.
- **Only when the server is set to forward by iMessage** (`TEXT_FORWARD_VIA=imessage`), it
  also takes an approved text for the senior and sends it to the senior's address. That is
  the one time it is given a text's words, and the words and the sender's number then pass
  through Photon. It is off by default, and off in the agent unless its own settings say so
  too: the server and the agent each have to agree.
- It answers `HELP`, `STATUS` and `WHY` from the caregiver, and nobody else. Groups,
  strangers, its own echoes, attachments and reactions are ignored.
- It writes to two addresses and no others: the caregiver, and, in iMessage mode, the
  senior (`SENIOR_IMESSAGE_ADDRESS`, or `SENIOR_PHONE`). The destination is this service's own
  setting. A job that names another address or line is not sent, nor one of a kind that was
  not asked for, and the server hands the agent a job for the senior only while forwarding is
  on and the server is not in practice mode. In SMS mode (the default) the agent never takes
  such a job, and forwarding is the server's, by SMS (`gateway_sms.py`).
- The senior is not a commander: what the senior replies in the conversation is ignored.
- It cannot release or discard a text. That happens in the app, signed in or with the
  access token.
- A send that timed out is not sent again: it may have arrived.

```sh
npm ci --ignore-scripts
npm test
npm run build
node --env-file=../.env dist/index.js
```

Node 22 or later. The tests put stand-ins in place of Photon and the server and need no
account and no phone: 13 of them, run by `npm test` after it builds the TypeScript.

Settings, from the environment: `SPECTRUM_PROJECT_ID`, `SPECTRUM_PROJECT_SECRET`,
`PHOTON_BRIDGE_TOKEN`, and `PHOTON_CAREGIVER_ADDRESS` (or `CAREGIVER_PHONE`). To carry texts
for the senior as well: `TEXT_FORWARD_VIA=imessage` and `SENIOR_IMESSAGE_ADDRESS` (or
`SENIOR_PHONE`), a different address from the caregiver's and the Twilio number.
`SHIELD_BACKEND_URL` is `http://127.0.0.1:8000` unless set, and has to be this machine:
the agent refuses any other address. The server needs the same bridge token and
caregiver address, and `TEXT_GATEWAY_ENABLED=true`.

In Photon, add the caregiver's own iMessage phone number or email under Users (and, to carry
texts for the senior, the senior's too, who then starts a conversation with their own "Texts on"
number once), and start
the conversation from the "Texts on" number Photon assigns. A shared Photon line is
enough; leave `PHOTON_LINE` empty unless the project has a dedicated one, and then set
it for both the agent and the server. The Twilio number and the protected person's
number are never Photon users.

The lockfile pins `@opentelemetry/core` to 2.12.0, because the copies `spectrum-ts`
would otherwise bring in are older than the fix for GHSA-8988-4f7v-96qf. The SDK's
telemetry and logging are off, and the agent prints fixed lines only, so that nothing a
caregiver wrote ends up in a log.

**Not run against Photon yet.** The agent has only met its stand-ins. Running it on the
Vultr server, and what to check when it first runs for real, is in
[deploy/README.md](../deploy/README.md).
