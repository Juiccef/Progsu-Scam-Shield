import { createHash } from "node:crypto";
import { setTimeout as sleep } from "node:timers/promises";

export interface Config {
  projectID: string; projectSecret: string; line: string;
  caregiver: string; backendURL: string; bridgeToken: string;
}
export interface Incoming {
  platform: string; direction: string; id: string; spaceID: string;
  spaceType: string; line: string; sender?: string;
  content: { type: string; text?: string };
}
export interface Job {
  job_id: string; attempt_id: string; message_id: string; kind: "caregiver_alert";
  recipient: string; line: string; text: string;
}
interface Status { held_count: number; why: string; forwarding_enabled: boolean; sms_ready: boolean; shield_number: string }
export interface Transport {
  send(recipient: string, line: string, text: string): Promise<void>;
}
export interface Backend {
  request<T>(path: string, body?: unknown): Promise<T>;
}

export function normalizeAddress(value: string): string {
  const trimmed = value.trim().toLowerCase();
  return trimmed.includes("@") ? trimmed : trimmed.replace(/\D/g, "");
}

export function readConfig(env: NodeJS.ProcessEnv): Config {
  const required = (name: string, fallback?: string) => {
    const value = (env[name] || fallback || "").trim();
    if (!value) throw new Error(`Missing configuration: ${name}`);
    return value;
  };
  const result = {
    projectID: required("SPECTRUM_PROJECT_ID"), projectSecret: required("SPECTRUM_PROJECT_SECRET"),
    line: (env.PHOTON_LINE || "").trim(),
    caregiver: required("PHOTON_CAREGIVER_ADDRESS", env.CAREGIVER_PHONE),
    backendURL: env.SHIELD_BACKEND_URL || "http://127.0.0.1:8000", bridgeToken: required("PHOTON_BRIDGE_TOKEN"),
  };
  const url = new URL(result.backendURL);
  if (!["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
      !["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error("SHIELD_BACKEND_URL must be a loopback HTTP(S) address");
  }
  if (!normalizeAddress(result.caregiver) ||
      (result.line && normalizeAddress(result.line) === normalizeAddress(result.caregiver)) ||
      (env.TWILIO_PROXY_NUMBER && normalizeAddress(env.TWILIO_PROXY_NUMBER) === normalizeAddress(result.caregiver))) {
    throw new Error("Caregiver must be a personal address, distinct from the service numbers");
  }
  return result;
}

export class BridgeError extends Error {
  constructor(readonly status: number) { super(`Bridge request failed (${status})`); }
}

export class Bridge implements Backend {
  constructor(private config: Config, private signal: AbortSignal) {}
  async request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(this.config.backendURL.replace(/\/$/, "") + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { Authorization: `Bearer ${this.config.bridgeToken}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.any([this.signal, AbortSignal.timeout(10_000)]),
    });
    if (!response.ok) throw new BridgeError(response.status);
    return await response.json() as T;
  }
}

export function messageID(config: Config, incoming: Incoming): string {
  return "photon_" + createHash("sha256").update(JSON.stringify([
    config.projectID, normalizeAddress(incoming.line), incoming.spaceID, incoming.id,
  ])).digest("hex");
}

export class Worker {
  private seenCommands = new Set<string>();
  constructor(private config: Config, private backend: Backend, private transport: Transport) {}

  async receive(incoming: Incoming): Promise<void> {
    if (incoming.platform !== "imessage" || incoming.direction !== "inbound" ||
        incoming.spaceType !== "dm" || !incoming.sender || !incoming.id ||
        (this.config.line && normalizeAddress(incoming.line) !== normalizeAddress(this.config.line))) return;
    const sender = normalizeAddress(incoming.sender);
    if (!sender || sender !== normalizeAddress(this.config.caregiver)) return;
    const type = incoming.content.type;
    // Control events carry no user content and must not create quarantine records.
    if (["reaction", "read", "typing", "rename", "avatar", "addMember", "removeMember", "leaveSpace", "edit", "unsend"].includes(type)) return;
    {
      if (type !== "text") return;
      const id = messageID(this.config, incoming);
      if (this.seenCommands.has(id)) return;
      const command = incoming.content.text?.trim().toUpperCase();
      let response: string;
      if (command === "HELP" || command === "START" || command === "HELLO") {
        const status = await this.backend.request<Status>("/integrations/photon/status");
        response = `Shield screens SMS sent to ${status.shield_number}, then forwards approved texts from that number with a screening footer. ` +
          "Suspicious texts are held in the Shield app for caregiver review for 24 hours. " +
          "I send review alerts here. Send STATUS or WHY. Replies to forwarded SMS are not relayed.";
      } else if (command === "STATUS" || command === "WHY") {
        const status = await this.backend.request<Status>("/integrations/photon/status");
        response = command === "WHY" ? status.why :
          `Shield has ${status.held_count} message(s) awaiting review. SMS forwarding is ${status.forwarding_enabled && status.sms_ready ? "enabled" : "paused or unavailable"}. Open the Shield app's Texts tab to review.`;
      } else {
        response = "Send HELP, STATUS or WHY. Review, release and discard happen in the authenticated Shield app.";
      }
      this.seenCommands.add(id);
      if (this.seenCommands.size > 1000) this.seenCommands.delete(this.seenCommands.values().next().value!);
      try {
        await this.send(this.config.caregiver, response);
      } catch {
        // An uncertain command response must not be retried with the receive operation.
        console.warn("Shield command response was not confirmed; automatic resend suppressed.");
      }
      return;
    }
  }

  private async send(recipient: string, text: string): Promise<void> {
    let timeout: NodeJS.Timeout | undefined;
    try {
      await Promise.race([
        this.transport.send(recipient, this.config.line, text),
        new Promise<never>((_, reject) => { timeout = setTimeout(() => reject(new Error("Send unconfirmed")), 15_000); }),
      ]);
    } finally { if (timeout) clearTimeout(timeout); }
  }

  async drain(report: (path: string, body: unknown) => Promise<unknown> =
      (path, body) => this.backend.request(path, body)): Promise<boolean> {
    const { job } = await this.backend.request<{ job: Job | null }>("/integrations/photon/outbox/claim", {});
    if (!job) return false;
    let outcome: "sent" | "delivery_unknown" = "delivery_unknown";
    const recipient = this.config.caregiver;
    try {
      if (job.kind !== "caregiver_alert" ||
          normalizeAddress(job.recipient) !== normalizeAddress(recipient) ||
          normalizeAddress(job.line) !== normalizeAddress(this.config.line)) {
        throw new Error("Unexpected outbox destination");
      }
      // A timed-out operation may still complete remotely. Never re-send this job.
      await this.send(recipient, job.text);
      outcome = "sent";
    } catch {
      // Do not print transport exceptions: they may include message content.
      console.warn("Photon send was not confirmed; automatic resend suppressed.");
    }
    await report(`/integrations/photon/outbox/${job.job_id}/result`, { attempt_id: job.attempt_id, outcome });
    return true;
  }
}

export async function retry<T>(fn: () => Promise<T>, signal: AbortSignal): Promise<T> {
  let delay = 1000;
  while (!signal.aborted) {
    try { return await fn(); }
    catch (error) {
      if (error instanceof BridgeError && [400, 401, 403, 404, 422].includes(error.status)) throw error;
      console.warn("Shield bridge unavailable; retaining the operation for retry.");
      await sleep(delay, undefined, { signal });
      delay = Math.min(delay * 2, 8000);
    }
  }
  throw new Error("Service stopped");
}
