# Text protection on the Vultr server

Text protection ships switched off. This is how to switch it on on the server that
`tools/vultr_deploy.py` set up (see "Run it on Vultr" in the main README), and how to check
it before any text is forwarded to the protected person.

**Nothing on this page has been run on the real server yet.** The server's text code was
tested on this Mac with stand-ins for Twilio and Photon (`tests/test_gateway.py`). Steps 6
and 7 need phones and a Photon account, and are the first time those parts run for real.

## What runs where

- **The Palisade server** (the `shield` service) receives texts sent to the Twilio number,
  scores them, keeps them encrypted, and queues the approved ones for the senior. No second
  backend is needed.
- **The iMessage agent** (`photon/`, the `shield-spectrum` service) sends the caregiver an
  iMessage when a text is held and answers `HELP`, `STATUS` and `WHY`. Without it, text
  protection still works; the app is then the only place a held text shows up. In iMessage
  mode (section 7) it also carries the approved texts to the senior.
- **How an approved text reaches the senior** is one of two ways, chosen by
  `TEXT_FORWARD_VIA`: `sms` (the default), by Twilio from the proxy number, which US carriers
  refuse until the number is registered with them; or `imessage`, through the agent, which
  needs no carrier registration.
- **Caddy** passes everything through to the server except `/integrations/`, which the
  agent asks on `127.0.0.1` and which answers 404 from outside.

## 1. Settings

Add these to `.env.vultr` on this computer (it is git-ignored). `push` sends them.

```dotenv
TEXT_GATEWAY_ENABLED=true
# Leave this off until step 6 has passed. Texts are checked and kept, and none is sent on.
TEXT_FORWARDING_ENABLED=false
# python3 -c "import secrets; print(secrets.token_urlsafe(24))"   (not the same as EVENTS_TOKEN)
PHOTON_BRIDGE_TOKEN=
# .venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
GATEWAY_ENCRYPTION_KEY=
# Only if the caregiver's iMessage address is not CAREGIVER_PHONE. An email address works.
PHOTON_CAREGIVER_ADDRESS=
# From the Photon project. Only the iMessage agent uses these two.
SPECTRUM_PROJECT_ID=
SPECTRUM_PROJECT_SECRET=
```

Keep `GATEWAY_ENCRYPTION_KEY` the same from then on: with another key the texts already
waiting cannot be read.

The server also needs what it already has: `TWILIO_PROXY_NUMBER` and `SENIOR_PHONE` written
as `+1…`, three different numbers for the Twilio number, the protected person and the
caregiver, `TWILIO_VALIDATE_SIGNATURE` left on, and either `EVENTS_TOKEN` or sign-in, so
that somebody can review what is held.

## 2. Push, and read what it says

```bash
.venv/bin/python tools/vultr_deploy.py push      # restarts the server: not during a call
.venv/bin/python tools/vultr_deploy.py status
```

`status` should say `texts: on, forwarding paused`. If a setting is wrong the server
still starts, so that calls stay protected, and says which one:
`texts: OFF, a setting is wrong (GATEWAY_ENCRYPTION_KEY must be a Fernet key)`.

Then check that the agent's pages are closed from outside. This should print `404`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<server address>/integrations/photon/status
```

## 3. Send the number's texts to the server

```bash
.venv/bin/python tools/set_webhook.py --sms-only <server address>
```

This changes only the number's incoming-text webhook. Calls keep going where they go.

## 4. Look in the app

Texts should show "<name>'s Texts", "Forwarding Paused" and the number.

## 5. Text the number from a third phone

- An ordinary text ("Your appointment is tomorrow at 2pm") shows up under Recent as
  "Waiting to Be Forwarded". Nothing reaches the protected person: forwarding is off.
- A scam ("USPS: pay the redelivery fee at https://…") shows up under Needs Review, with a
  count on the Texts tab and a row on Summary. Open it, and discard it.
- A picture is held as "Has an Attachment" and can only be discarded.

## 6. The iMessage agent (optional)

On the server, as root. `ssh root@<server ip>` gets you there.

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs
cd /opt/shield/photon && sudo -u shield npm ci --ignore-scripts && sudo -u shield npm run build
install -d -m 700 /etc/shield
grep -E '^(SPECTRUM_PROJECT_ID|SPECTRUM_PROJECT_SECRET|PHOTON_LINE|PHOTON_CAREGIVER_ADDRESS|CAREGIVER_PHONE|PHOTON_BRIDGE_TOKEN|TWILIO_PROXY_NUMBER|TEXT_FORWARD_VIA|SENIOR_IMESSAGE_ADDRESS|SENIOR_PHONE)=' \
  /opt/shield/.env > /etc/shield/photon.env && chmod 600 /etc/shield/photon.env
cp /opt/shield/deploy/shield-spectrum.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now shield-spectrum
journalctl -u shield-spectrum -n 20 --no-pager
```

The agent gets a settings file of its own, with only what it needs, so the Twilio and
model keys in `/opt/shield/.env` are not handed to it.

In Photon, add the caregiver's own iMessage phone number or email under Users (not the
Twilio number), and start the conversation from the "Texts on" number Photon assigns.
`PHOTON_LINE` stays empty unless the project has a dedicated line.

**A `push` deletes the built agent.** It replaces everything in `/opt/shield` except
`.venv`, `logs` and `.env`. After every push, run the second line above again, the `grep`
line if a setting changed, and `systemctl restart shield-spectrum`.

Then send the scam text again: the caregiver should get an iMessage that names the kind
of warning and none of the text's words, and `WHY` sent back to it should be answered.

## 7. Switch forwarding on

Only with people who have agreed to get the texts, and with texts you wrote yourself.

### By iMessage (no carrier registration)

The senior must have an iPhone or Mac signed in to iMessage, and must have agreed that their
texts pass through Photon: the text and the sender's number go through Photon's service.

1. In Photon, add the senior's iMessage phone or email under Users, and have the senior text
   their own "Texts on" number once. The conversation has to exist before the agent can write.
2. In `.env.vultr`:

   ```dotenv
   TEXT_FORWARD_VIA=imessage
   SENIOR_IMESSAGE_ADDRESS=      # only if the senior's iMessage is an email or another number than SENIOR_PHONE
   TEXT_FORWARDING_ENABLED=true
   DRY_RUN_ACTIONS=false
   ```

3. Push, then rebuild the agent (section 6, all of it: a push deletes it, and its settings file
   must be written again so that it knows `TEXT_FORWARD_VIA`).
4. `tools/vultr_deploy.py status` says `texts: on`, and the app's Texts page says Protected.
   It says Can't Forward when the agent is not checking in.
5. Text the Twilio number an ordinary text. It should arrive in the senior's Messages
   from the Photon number, with "— Palisade screening —" under it. In the app it goes from
   "Waiting to Be Forwarded" to "Forwarded". It never becomes "Delivered": iMessage does not
   report that to Palisade.
6. A scam must stay held until released in the app, and then say "Released after caregiver
   review."

**Every text already waiting is sent the moment forwarding comes on.** Switching from SMS
to iMessage moves the ones that are waiting onto iMessage.

### By SMS (needs the number registered)

```dotenv
TEXT_FORWARDING_ENABLED=true
DRY_RUN_ACTIONS=false
```

Push again. **Every text already waiting is sent the moment forwarding comes on.**

- The ordinary text should reach the protected person from the Twilio number, with
  "— Palisade screening —", the sender's number and "Passed automated screening" under it.
- A scam must not arrive until it is released in the app. Released, it says "Released
  after caregiver review."
- In the app the text goes from "Waiting to Be Forwarded" to "Forwarded", then
  "Delivered" if the phone company reports it.

**Expect "Not Delivered" at first.** The caregiver texts Palisade sends from this number
are refused by US carriers because the number is not registered (error 30034, see
HANDOFF.md), and a forwarded text leaves from the same number. Registering it with the
carriers is done in Twilio's console and takes days.

## What is kept, and for how long

`/opt/shield/logs/gateway/queue.sqlite3` holds each text and its sender's number,
encrypted with `GATEWAY_ENCRYPTION_KEY`, until it is forwarded, discarded, or 24 hours old,
whichever comes first. Afterwards only what Palisade made of it stays: the risk, the
warning signs, and the sender's last four digits. `logs` survives a push. Twilio, Photon
and the model's provider keep what they keep; that is theirs to answer for.

## Switching it off

- Stop forwarding: `TEXT_FORWARDING_ENABLED=false`, push. A text already on its way may
  still go out; the rest wait.
- Stop the iMessages: `systemctl stop shield-spectrum` on the server.
- Switch text protection off altogether: review or discard what is held first, then
  `TEXT_GATEWAY_ENABLED=false`, push. Texts that are still stored are only deleted on time
  while text protection is on.
