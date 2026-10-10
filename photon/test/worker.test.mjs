import test from "node:test";
import assert from "node:assert/strict";
import { Bridge, BridgeError, messageID, readConfig, Worker } from "../dist/worker.js";

const config = {
  projectID: "test-project", projectSecret: "private", line: "", caregiver: "+15555550102",
  backendURL: "http://127.0.0.1:8000", bridgeToken: "private-bridge",
};
const input = {
  platform: "imessage", direction: "inbound", id: "original-1", spaceID: "caregiver-space",
  spaceType: "dm", line: "+15555550900", sender: config.caregiver, content: { type: "text", text: "WHY" },
};
const alert = { job_id: "j", attempt_id: "a", message_id: "m", kind: "caregiver_alert",
  recipient: config.caregiver, line: "", text: "Shield held a suspicious text. Open the app." };

function setup({ job = null, sendFails = false, workerConfig = config } = {}) {
  const requests = [], sends = [];
  const backend = { async request(path, body) {
    requests.push({ path, body });
    if (path.endsWith("/claim")) return { job };
    if (path.endsWith("/status")) return { held_count: 1, why: "Unexpected fee request.",
      forwarding_enabled: true, sms_ready: true, shield_number: "+15555550100" };
    return {};
  } };
  const transport = { async send(recipient, line, text) {
    sends.push({ recipient, line, text });
    if (sendFails) throw new Error("potentially sensitive SDK failure");
  } };
  return { worker: new Worker(workerConfig, backend, transport), requests, sends };
}

test("Photon handles caregiver WHY on a shared line without submitting texts for relay", async () => {
  const { worker, requests, sends } = setup();
  await worker.receive(input);
  assert.equal(requests[0].path, "/integrations/photon/status");
  assert.deepEqual(sends, [{ recipient: config.caregiver, line: "", text: "Unexpected fee request." }]);
  assert.equal(requests.filter(r => r.path.endsWith("/messages")).length, 0);
});

test("HELP explains the Twilio number and STATUS reports SMS forwarding", async () => {
  const { worker, sends } = setup();
  await worker.receive({ ...input, id: "help", content: { type: "text", text: "HELP" } });
  await worker.receive({ ...input, id: "status", content: { type: "text", text: "STATUS" } });
  assert.match(sends[0].text, /\+15555550100/);
  assert.match(sends[0].text, /from that number with a screening footer/);
  assert.match(sends[1].text, /1 message\(s\) awaiting review/);
  assert.match(sends[1].text, /SMS forwarding is enabled/);
});

test("outbound echoes, strangers, seniors, groups and attachments are ignored", async () => {
  const { worker, requests, sends } = setup();
  for (const change of [
    { direction: "outbound" }, { platform: "terminal" }, { spaceType: "group" },
    { sender: "+15555550999" }, { sender: "+15555550101" }, { sender: undefined },
    { content: { type: "reaction" } }, { content: { type: "read" } }, { content: { type: "attachment" } },
  ]) await worker.receive({ ...input, ...change });
  assert.equal(requests.length, 0);
  assert.equal(sends.length, 0);
});

test("dedicated caregiver line is optional but restricts commands when configured", async () => {
  const { worker, sends } = setup({ workerConfig: { ...config, line: input.line } });
  await worker.receive({ ...input, line: "+15555550888" });
  assert.equal(sends.length, 0);
  await worker.receive(input);
  assert.equal(sends[0].line, input.line);
});

test("replayed and uncertain command responses are not automatically repeated", async () => {
  const { worker, sends } = setup({ sendFails: true });
  await worker.receive({ ...input, sender: "+1 (555) 555-0102" });
  await worker.receive(input);
  assert.equal(sends.length, 1);
  assert.equal(sends[0].recipient, config.caregiver);
});

test("review commands direct the caregiver to the authenticated app", async () => {
  const { worker, sends } = setup();
  await worker.receive({ ...input, content: { type: "text", text: "release latest" } });
  assert.match(sends[0].text, /authenticated Shield app/);
});

test("only caregiver alerts are sent by Spectrum", async () => {
  const { worker, sends, requests } = setup({ job: alert });
  await worker.drain();
  assert.deepEqual(sends, [{ recipient: config.caregiver, line: "", text: alert.text }]);
  assert.equal(requests.at(-1).body.outcome, "sent");
});

test("Spectrum rejects senior relay jobs and arbitrary destinations", async () => {
  for (const job of [{ ...alert, kind: "relay", recipient: "+15555550101" },
    { ...alert, recipient: "+15555550777" }, { ...alert, line: "+15555550777" }]) {
    const { worker, sends, requests } = setup({ job });
    await worker.drain();
    assert.equal(sends.length, 0);
    assert.equal(requests.at(-1).body.outcome, "delivery_unknown");
  }
});

test("ambiguous alert send is recorded without resending", async () => {
  const { worker, sends, requests } = setup({ job: alert, sendFails: true });
  await worker.drain();
  assert.equal(sends.length, 1);
  assert.equal(requests.at(-1).body.outcome, "delivery_unknown");
});

test("failed result acknowledgement does not retry the send operation", async () => {
  const { worker, sends } = setup({ job: alert });
  await assert.rejects(worker.drain(async () => { throw new Error("backend unavailable"); }));
  assert.equal(sends.length, 1);
});

test("command identity includes source conversation and project", () => {
  assert.equal(messageID(config, input), messageID(config, { ...input }));
  assert.notEqual(messageID(config, input), messageID(config, { ...input, spaceID: "another" }));
  assert.notEqual(messageID(config, input), messageID({ ...config, projectID: "other" }, input));
});

test("bridge authenticates without exposing private error bodies", async () => {
  const originalFetch = globalThis.fetch;
  let captured;
  globalThis.fetch = async (url, options) => {
    captured = { url, options };
    return new Response(JSON.stringify({ held_count: 1 }), { status: 200 });
  };
  try {
    const bridge = new Bridge(config, new AbortController().signal);
    assert.deepEqual(await bridge.request("/integrations/photon/status"), { held_count: 1 });
    assert.equal(captured.options.headers.Authorization, "Bearer private-bridge");
    globalThis.fetch = async () => new Response("private response", { status: 401 });
    await assert.rejects(bridge.request("/test"), e => e instanceof BridgeError && !e.message.includes("private response"));
  } finally { globalThis.fetch = originalFetch; }
});

test("configuration supports shared lines and requires a personal caregiver and loopback bridge", () => {
  const env = { SPECTRUM_PROJECT_ID: "project", SPECTRUM_PROJECT_SECRET: "secret", CAREGIVER_PHONE: config.caregiver,
    PHOTON_BRIDGE_TOKEN: "bridge", TWILIO_PROXY_NUMBER: "+15555550100" };
  assert.equal(readConfig(env).line, "");
  assert.equal(readConfig(env).caregiver, config.caregiver);
  assert.throws(() => readConfig({ ...env, SPECTRUM_PROJECT_SECRET: "" }));
  assert.throws(() => readConfig({ ...env, SHIELD_BACKEND_URL: "https://public.example" }));
  assert.throws(() => readConfig({ ...env, CAREGIVER_PHONE: env.TWILIO_PROXY_NUMBER }));
  assert.throws(() => readConfig({ ...env, PHOTON_LINE: config.caregiver }));
});
