import { createHash } from "node:crypto";
import { setTimeout as sleep } from "node:timers/promises";

export interface Config {
  projectID: string; projectSecret: string; line: string;
  caregiver: string; backendURL: string; bridgeToken: string;
  /** The one address texts for the senior may be sent to. Only set when the server forwards by iMessage. */
  senior?: string;
}
export interface Incoming {
  platform: string; direction: string; id: string; spaceID: string;
  spaceType: string; line: string; sender?: string;
  content: { type: string; text?: string };
}
/** The two kinds of job this service may take: a warning for the caregiver, or an approved text for the senior. */
export type JobKind = "caregiver_alert" | "senior_imessage";
export interface Job {
  job_id: string; attempt_id: string; message_id: string; kind: JobKind;
  recipient: string; line: string; text: string;
}
interface Status {
  held_count: number; why: string; forwarding_enabled: boolean; sms_ready: boolean; shield_number: string;
  forward_via?: string; forwarding_ready?: boolean;
}
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
    // Texts for the senior are carried only when the server says so, here as well as there: two locks.
    senior: (env.TEXT_FORWARD_VIA || "").trim().toLowerCase() === "imessage"
      ? required("SENIOR_IMESSAGE_ADDRESS", env.SENIOR_PHONE) : undefined,
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
  if (result.senior !== undefined) {
    const senior = normalizeAddress(result.senior);
    const others = [result.caregiver, result.line, env.TWILIO_PROXY_NUMBER || ""].map(normalizeAddress).filter(Boolean);
    if (!senior || others.includes(senior)) {
      throw new Error("The senior's address must be a personal address, distinct from the caregiver and the service numbers");
    }
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
        const how = status.forward_via === "imessage"
          ? "forwards approved texts to the protected person by iMessage with a screening note"
          : "forwards approved texts from that number with a screening footer";
        response = `Palisade screens SMS sent to ${status.shield_number}, then ${how}. ` +
          "Suspicious texts are held in the Palisade app for caregiver review for 24 hours. " +
          "I send review alerts here. Send STATUS or WHY. Replies to forwarded texts are not relayed.";
      } else if (command === "STATUS" || command === "WHY") {
        const status = await this.backend.request<Status>("/integrations/photon/status");
        response = command === "WHY" ? status.why :
          `Palisade has ${status.held_count} message(s) awaiting review. ${status.forward_via === "imessage" ? "iMessage" : "SMS"} forwarding is ${status.forwarding_enabled && (status.forwarding_ready ?? status.sms_ready) ? "enabled" : "paused or unavailable"}. Open the Palisade app's Texts tab to review.`;
      } else {
        response = "Send HELP, STATUS or WHY. Review, release and discard happen in the authenticated Palisade app.";
      }
      this.seenCommands.add(id);
      if (this.seenCommands.size > 1000) this.seenCommands.delete(this.seenCommands.values().next().value!);
      try {
        await this.send(this.config.caregiver, response);
      } catch {
        // An uncertain command response must not be retried with the receive operation.
        console.warn("Palisade command response was not confirmed; automatic resend suppressed.");
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

  /**
   * Takes one job of `kind` from the server and sends it. A caregiver alert goes to the caregiver, and a
   * senior text to the senior, and to nobody else, whatever the job says: the destination is this
   * service's own configuration, and a job that names another is not sent.
   */
  async drain(report: (path: string, body: unknown) => Promise<unknown> =
      (path, body) => this.backend.request(path, body), kind: JobKind = "caregiver_alert"): Promise<boolean> {
    const recipient = kind === "senior_imessage" ? this.config.senior : this.config.caregiver;
    if (!recipient) return false; // not set up to carry texts for the senior: do not even ask for them
    const { job } = await this.backend.request<{ job: Job | null }>("/integrations/photon/outbox/claim", { kind });
    if (!job) return false;
    let outcome: "sent" | "delivery_unknown" = "delivery_unknown";
    try {
      if (job.kind !== kind ||
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
      console.warn("Palisade bridge unavailable; retaining the operation for retry.");
      await sleep(delay, undefined, { signal });
      delay = Math.min(delay * 2, 8000);
    }
  }
  throw new Error("Service stopped");
}
