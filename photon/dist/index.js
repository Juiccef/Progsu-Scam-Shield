import { Spectrum } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";
import { setTimeout as sleep } from "node:timers/promises";
import { Bridge, readConfig, retry, Worker } from "./worker.js";
async function main() {
    const config = readConfig(process.env);
    const stop = new AbortController();
    const app = await Spectrum({
        projectId: config.projectID, projectSecret: config.projectSecret,
        providers: [imessage.config()], telemetry: false, options: { logLevel: "silent" },
    });
    const bridge = new Bridge(config, stop.signal);
    const im = imessage(app);
    const transport = {
        async send(recipient, line, text) {
            const user = await im.user(recipient);
            // Shared plans route each caregiver automatically; phone pins only dedicated lines.
            const space = await im.space.create(user, line ? { phone: line } : undefined);
            const sent = await space.send(text);
            if (!sent)
                throw new Error("No send acknowledgement");
        },
    };
    const worker = new Worker(config, bridge, transport);
    let stopped = false;
    async function shutdown() {
        if (stopped)
            return;
        stopped = true;
        stop.abort();
        // Separate signal so the disconnected heartbeat can complete during shutdown.
        try {
            await new Bridge(config, AbortSignal.timeout(3000)).request("/integrations/photon/heartbeat", { connected: false });
        }
        catch { /* stale heartbeat expires on the backend */ }
        await app.stop();
    }
    process.once("SIGTERM", () => { void shutdown(); });
    process.once("SIGINT", () => { void shutdown(); });
    console.info("Shield Spectrum caregiver agent initialized.");
    const receive = async () => {
        for await (const [space, message] of app.messages) {
            if (stop.signal.aborted)
                break;
            if (message.platform !== "imessage")
                continue;
            const imSpace = imessage(space);
            const imMessage = imessage(message);
            await retry(() => worker.receive({
                platform: message.platform, direction: message.direction, id: message.id,
                spaceID: space.id, spaceType: imSpace.type, line: imSpace.phone,
                sender: imMessage.sender?.address ?? message.sender?.id,
                content: message.content.type === "text" ? { type: "text", text: message.content.text } :
                    { type: message.content.type },
            }), stop.signal);
        }
        if (!stop.signal.aborted)
            throw new Error("Spectrum receive stream ended");
    };
    const outbox = async () => {
        while (!stop.signal.aborted) {
            try {
                await worker.drain((path, body) => retry(() => bridge.request(path, body), stop.signal));
            }
            catch {
                if (!stop.signal.aborted)
                    console.warn("Outbox operation unavailable; claimed sends will not be repeated.");
            }
            await sleep(1000, undefined, { signal: stop.signal });
        }
    };
    const heartbeat = async () => {
        while (!stop.signal.aborted) {
            try {
                await bridge.request("/integrations/photon/heartbeat", { connected: true });
            }
            catch {
                if (!stop.signal.aborted)
                    console.warn("Gateway heartbeat unavailable.");
            }
            await sleep(15_000, undefined, { signal: stop.signal });
        }
    };
    // The heartbeat indicates an initialized, running SDK process; it is not a delivery receipt.
    try {
        await Promise.all([receive(), outbox(), heartbeat()]);
    }
    finally {
        await shutdown();
    }
}
main().catch(() => {
    // SDK errors can contain credentials or content, so emit a fixed diagnostic.
    console.error("Shield Spectrum caregiver agent stopped. Check required configuration and Photon connectivity.");
    process.exitCode = 1;
});
