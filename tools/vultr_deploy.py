#!/usr/bin/env python3
"""Run Shield on a Vultr server instead of this computer behind a tunnel.

    python tools/vultr_deploy.py up        # create the server if there is none, then send Shield to it
    python tools/vultr_deploy.py push      # send the code and settings again and restart Shield
    python tools/vultr_deploy.py status    # where it is and what its /health says
    python tools/vultr_deploy.py logs      # follow Shield's log on the server (Ctrl+C to stop)
    python tools/vultr_deploy.py down      # delete the server

Needs VULTR_API_KEY (in .env or the environment) and an SSH key (~/.ssh/id_ed25519.pub
unless --ssh-key says otherwise). The server costs money for as long as it exists: about
$5 a month on the default plan, billed by the hour.

What is sent: the files git tracks, without ios/ (so `git add` a new file first), and a
.env made of this computer's .env with the lines of .env.vultr on top. .env.vultr is
created the first time, holds what differs on the server (demo calls on, the access
token, ...) and is git-ignored. VULTR_API_KEY itself is never sent.

The server gets a public https address and a certificate by itself: its IP address as a
name under sslip.io, or your own domain with --host once its A record points at the server.
A push restarts Shield, which would drop a call in progress, so it refuses while one is active.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from dotenv import dotenv_values, set_key  # noqa: E402

from config import _host, default_scorer_model  # noqa: E402
from scorer.providers import key_vars, resolve  # noqa: E402

API = "https://api.vultr.com/v2"
LABEL = "shield"  # the server is found again by this label
OS_NAME = "Ubuntu 24.04 LTS x64"
APP_DIR = "/opt/shield"
LOCAL_ENV = ROOT / ".env"
SERVER_ENV = ROOT / ".env.vultr"
SERVER_ENV_TEMPLATE = """\
# What differs on the Vultr server. tools/vultr_deploy.py sends .env with these lines on top.
# Git-ignored, like .env. After editing: python tools/vultr_deploy.py push
DEMO_MODE=true
LOG_TRANSCRIPTS=false
"""
NEVER_SENT = ("VULTR_API_KEY",)  # the account key creates and deletes servers; the server has no use for it
# Everything but the virtualenv, the call history and the settings is replaced by what is sent.
UNPACK = (
    f"set -e; mkdir -p {APP_DIR}; cd {APP_DIR}; "
    "find . -mindepth 1 -maxdepth 1 ! -name .venv ! -name logs ! -name .env -exec rm -rf {} +; "
    "tar -xzf - --no-same-owner"
)
_HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")
_PLAIN = re.compile(r"[A-Za-z0-9_./:@%+,=-]*")


class Failure(Exception):
    """Something the user has to see and act on. The message is the whole report."""


class Vultr:
    """The few calls this tool makes to the Vultr API (https://www.vultr.com/api/)."""

    def __init__(self, api_key: str, *, transport: Optional[httpx.BaseTransport] = None) -> None:
        self._client = httpx.Client(
            base_url=API, headers={"Authorization": f"Bearer {api_key}"}, timeout=30, transport=transport
        )

    def call(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise Failure(f"Could not reach the Vultr API: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("error") or response.text
            except (ValueError, AttributeError):
                message = response.text
            hint = ""
            if response.status_code in (401, 403):
                hint = (
                    "\nCheck VULTR_API_KEY, and that this computer's IP address is allowed for it"
                    " in the Vultr console (Account, API, Access Control)."
                )
            raise Failure(f"Vultr API answered HTTP {response.status_code}: {str(message).strip()[:300]}{hint}")
        return response.json() if response.content else {}


# --- the server at Vultr -------------------------------------------------------


def find_server(api: Vultr) -> Optional[dict]:
    servers = [s for s in api.call("GET", "/instances", params={"per_page": 500})["instances"] if s["label"] == LABEL]
    if len(servers) > 1:
        raise Failure(f"{len(servers)} servers are labeled {LABEL!r}. Delete the extra ones in the Vultr console.")
    return servers[0] if servers else None


def ssh_key_id(api: Vultr, public_key: str) -> str:
    """The ID of this public key in the Vultr account. It is added if it is not there yet."""
    material = public_key.split()[1]
    for key in api.call("GET", "/ssh-keys", params={"per_page": 500})["ssh_keys"]:
        if material in key["ssh_key"].split():
            return key["id"]
    return api.call("POST", "/ssh-keys", json={"name": LABEL, "ssh_key": public_key.strip()})["ssh_key"]["id"]


def create_server(api: Vultr, region: str, plan: str, key_id: str) -> dict:
    images = api.call("GET", "/os", params={"per_page": 500})["os"]
    os_id = next((image["id"] for image in images if image["name"] == OS_NAME), None)
    if os_id is None:
        raise Failure(f"Vultr does not list {OS_NAME!r} any more. Update OS_NAME in {Path(__file__).name}.")
    body = {
        "region": region,
        "plan": plan,
        "os_id": os_id,
        "label": LABEL,
        "hostname": LABEL,
        "sshkey_id": [key_id],
        "backups": "disabled",
        "activation_email": False,
    }
    return api.call("POST", "/instances", json=body)["instance"]


def wait_until_active(api: Vultr, server: dict, *, timeout_s: float = 600, poll_s: float = 5) -> dict:
    """A new server has no address for the first minute or so."""
    deadline = time.monotonic() + timeout_s
    while server["status"] != "active" or server["main_ip"] in ("", "0.0.0.0"):
        if time.monotonic() > deadline:
            raise Failure("The server did not come up in 10 minutes. Look at it in the Vultr console.")
        time.sleep(poll_s)
        server = api.call("GET", f"/instances/{server['id']}")["instance"]
    return server


# --- what is sent ----------------------------------------------------------------


def sslip_host(ip: str) -> str:
    """A public name for a bare IP address: sslip.io answers 203-0-113-7.sslip.io with 203.0.113.7."""
    return ip.replace(".", "-") + ".sslip.io"


def server_env(
    local: Mapping[str, Optional[str]], overrides: Mapping[str, Optional[str]], shell: Mapping[str, str], host: str
) -> dict[str, str]:
    """The server's .env: this computer's .env, the lines of .env.vultr on top, and the scorer's API key.

    That key often lives in the shell profile instead of .env, so it is taken from the
    environment when neither file has it. No other key is taken from there.
    """
    env = {key: value for key, value in {**local, **overrides}.items() if value is not None and key not in NEVER_SENT}
    env["PUBLIC_HOST"] = host
    filled = {key: value for key, value in env.items() if value}
    model = filled.get("SCORER_MODEL") or default_scorer_model({**shell, **filled})
    if model.lower() not in ("rules", "none"):
        try:
            names = key_vars(resolve(model)[0])
        except ValueError:  # a spec no provider serves: the server says so when it starts
            names = ()
        for name in names:
            if not env.get(name) and shell.get(name):
                env[name] = shell[name]
    return env


def render_env(env: Mapping[str, str]) -> str:
    return "".join(f"{key}={_quoted(value)}\n" for key, value in env.items())


def _quoted(value: str) -> str:
    if _PLAIN.fullmatch(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def tracked_files() -> list[str]:
    """What git tracks, minus the iPhone app. Untracked files, .env among them, are never sent."""
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], check=True, capture_output=True).stdout
    names = [name for name in listed.decode().split("\0") if name and not name.startswith("ios/")]
    return [name for name in names if (ROOT / name).is_file()]


def archive(files: list[str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in files:
            tar.add(ROOT / name, arcname=name)
    return buffer.getvalue()


# --- the server itself, over SSH -----------------------------------------------------


def default_ssh_key() -> Path:
    candidates = [Path.home() / ".ssh" / name for name in ("id_ed25519.pub", "id_rsa.pub")]
    found = next((path for path in candidates if path.exists()), None)
    if found is None:
        raise Failure("No SSH key in ~/.ssh. Make one with: ssh-keygen -t ed25519   (or pass --ssh-key)")
    return found


def public_key_text(path: Path) -> str:
    """The key that is registered with Vultr. Anything that is not a public key stays on this computer."""
    text = path.read_text(encoding="utf-8").strip()
    fields = text.split()
    if "PRIVATE KEY" in text or len(fields) < 2 or not fields[0].startswith(("ssh-", "ecdsa-", "sk-")):
        raise Failure(f"{path} is not an SSH public key. Pass the file that ends in .pub.")
    return text


def ssh_argv(ip: str, public_key: Path, *options: str) -> list[str]:
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
    identity = public_key.with_suffix("")
    if public_key.suffix == ".pub" and identity.exists():
        argv += ["-i", str(identity)]
    return [*argv, *options, f"root@{ip}"]


def remote(
    ip: str, public_key: Path, command: str, *, input: Optional[bytes] = None, quiet: bool = False
) -> subprocess.CompletedProcess:
    """Run a command on the server as root. `quiet` keeps its output off the terminal."""
    return subprocess.run([*ssh_argv(ip, public_key), command], input=input, capture_output=quiet)


def must(result: subprocess.CompletedProcess, what: str) -> None:
    if result.returncode != 0:
        raise Failure(f"{what} failed on the server (exit {result.returncode}). The output above says why.")


def wait_for_ssh(ip: str, public_key: Path, *, timeout_s: float = 420) -> None:
    """A new server shows up as active a minute or two before it accepts logins."""
    deadline = time.monotonic() + timeout_s
    while True:
        result = remote(ip, public_key, "true", quiet=True)
        if result.returncode == 0:
            return
        if time.monotonic() > deadline:
            raise Failure(
                f"Could not log in to root@{ip}: {result.stderr.decode(errors='replace').strip()[-300:]}\n"
                "If your SSH key has a passphrase, run `ssh-add` first."
            )
        time.sleep(5)


def active_calls(ip: str, public_key: Path) -> int:
    """Calls in progress on the server. Asked on the server itself, so it works before HTTPS does."""
    result = remote(ip, public_key, "curl -fsS -m 3 http://127.0.0.1:8000/health", quiet=True)
    try:
        return int(json.loads(result.stdout).get("active_calls", 0))
    except (ValueError, AttributeError):
        return 0  # nothing is running there yet


def wait_for_health(host: str, *, timeout_s: float = 180) -> dict:
    """The first answer takes as long as Caddy needs to get the certificate, usually well under a minute."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            response = httpx.get(f"https://{host}/health", timeout=5)
            if response.status_code == 200:
                return response.json()
            problem = f"HTTP {response.status_code}"
        except (httpx.HTTPError, ValueError) as exc:
            problem = type(exc).__name__
        if time.monotonic() > deadline:
            raise Failure(
                f"Shield is running on the server, but https://{host}/health does not answer yet ({problem}).\n"
                "The certificate may still be on its way: try `status` in a minute. If it stays this way,\n"
                "`journalctl -u caddy -n 50` on the server says why. Your own domain (--host) avoids\n"
                "the limits shared by everyone who uses sslip.io."
            )
        time.sleep(3)


# --- commands --------------------------------------------------------------------


def public_host(args: argparse.Namespace, overrides: Mapping[str, Optional[str]], ip: str) -> str:
    host = _host(args.host or overrides.get("PUBLIC_HOST") or sslip_host(ip))
    if not _HOSTNAME.fullmatch(host):
        raise Failure(f"{host!r} is not a host name. Give it bare, for example shield.example.com.")
    try:
        resolved = socket.gethostbyname(host)
    except OSError:
        resolved = "nothing"
    if resolved != ip:
        fix = (
            "This network may be blocking sslip.io. Try another network, or your own domain with --host."
            if host == sslip_host(ip)
            else "Add an A record for it with that address and try again in a few minutes."
        )
        raise Failure(f"{host} points at {resolved}, not at the server ({ip}), so it cannot get a certificate.\n{fix}")
    return host


def push(args: argparse.Namespace, server: dict) -> int:
    ip, key = server["main_ip"], args.ssh_key
    if not SERVER_ENV.exists():
        SERVER_ENV.write_text(SERVER_ENV_TEMPLATE, encoding="utf-8")
        print(f"Created {SERVER_ENV.name} with the settings that differ on the server.")
    overrides = dotenv_values(SERVER_ENV, interpolate=False)
    host = public_host(args, overrides, ip)
    if args.host:
        set_key(str(SERVER_ENV), "PUBLIC_HOST", host, quote_mode="never")
    local = dotenv_values(LOCAL_ENV, interpolate=False)
    env = server_env(local, overrides, os.environ, host)
    if not env.get("EVENTS_TOKEN"):
        # /events carries live transcripts, so a server on the public internet always gets a token.
        env["EVENTS_TOKEN"] = secrets.token_hex(8)
        set_key(str(SERVER_ENV), "EVENTS_TOKEN", env["EVENTS_TOKEN"], quote_mode="never")
    from_shell = sorted(
        name
        for name, value in env.items()
        if value and name not in ("PUBLIC_HOST", "EVENTS_TOKEN") and not (local.get(name) or overrides.get(name))
    )

    if not args.force and active_calls(ip, key):
        raise Failure("A call is in progress on the server and a restart would drop it. Wait, or pass --force.")
    files = tracked_files()
    print(f"Sending {len(files)} files and {len(env)} settings to {ip} ...")
    if from_shell:
        print(f"  from your shell, because no .env file has it: {', '.join(from_shell)}")
    must(remote(ip, key, UNPACK, input=archive(files)), "Unpacking the code")
    must(remote(ip, key, f"umask 077 && cat > {APP_DIR}/.env", input=render_env(env).encode()), "Writing .env")
    must(remote(ip, key, f"bash {APP_DIR}/deploy/vultr-setup.sh {shlex.quote(host)}"), "The setup script")
    print(f"Waiting for https://{host} ...")
    health = wait_for_health(host)

    token = env["EVENTS_TOKEN"]
    print(f"\nShield is running at https://{host}")
    print(f"  {describe(health)}")
    if health.get("actions") == "dry_run":
        print("  Actions are a dry run, so a real call is not ended. For real calls put")
        print(f"  DRY_RUN_ACTIONS=false in {SERVER_ENV.name} and push again.")
    print("\nNext:")
    print(f"  python tools/set_webhook.py {host}     # send your Twilio number's calls to this server")
    print(f"  iPhone app, Settings:  Address {host}   Access token {token}")
    print(f"  python tools/watch.py https://{host} --token {token}")
    return 0


def describe(health: dict) -> str:
    return (
        f"scorer: {health.get('scorer_model')} | speech to text: {health.get('stt') or 'none'} | "
        f"actions: {health.get('actions')} | demo calls: {'on' if health.get('demo_mode') else 'off'} | "
        f"calls in progress: {health.get('active_calls')}"
    )


def up(args: argparse.Namespace, api: Vultr) -> int:
    server = find_server(api)
    created = server is None
    if created:
        server = create_server(api, args.region, args.plan, ssh_key_id(api, public_key_text(args.ssh_key)))
        print(f"Created a {args.plan} server in {args.region}. It is billed by the hour until `down`.")
    else:
        print(f"Using the server that already exists ({server['main_ip']}).")
    print("Waiting for the server to come up ...")
    server = wait_until_active(api, server)
    if created:
        # A new server can be handed an address this computer has seen before, with another host key.
        subprocess.run(["ssh-keygen", "-R", server["main_ip"]], capture_output=True)
    wait_for_ssh(server["main_ip"], args.ssh_key)
    return push(args, server)


def status(api: Vultr, server: dict) -> int:
    ip = server["main_ip"]
    overrides = dotenv_values(SERVER_ENV, interpolate=False) if SERVER_ENV.exists() else {}
    host = _host(overrides.get("PUBLIC_HOST") or sslip_host(ip))
    print(f"Server: {server['plan']} in {server['region']}, {ip}, {server['status']}")
    print(f"Address: https://{host}")
    try:
        print(f"Shield: {describe(httpx.get(f'https://{host}/health', timeout=8).json())}")
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Shield: no answer from https://{host}/health ({type(exc).__name__})")
    account = api.call("GET", "/account")["account"]
    print(f"Vultr charges so far this month: ${account.get('pending_charges', 0):.2f}")
    return 0


def down(args: argparse.Namespace, api: Vultr, server: dict) -> int:
    ip = server["main_ip"]
    if not args.yes:
        try:
            answer = input(f"Delete the server at {ip}, and the call history on it? Type 'delete' to go ahead: ")
        except EOFError:
            answer = ""
        if answer.strip() != "delete":
            print("Nothing was deleted.")
            return 1
    api.call("DELETE", f"/instances/{server['id']}")
    subprocess.run(["ssh-keygen", "-R", ip], capture_output=True)
    print("Deleted. If your Twilio number still sends calls there, point it elsewhere with tools/set_webhook.py.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("up", "push", "status", "logs", "down"))
    parser.add_argument("--region", default="atl", help="where a new server is created (default: atl, Atlanta)")
    parser.add_argument("--plan", default="vc2-1c-1gb", help="size of a new server (default: vc2-1c-1gb, 1 vCPU, 1 GB)")
    parser.add_argument("--host", help="your own domain for the server; remembered in .env.vultr")
    parser.add_argument("--ssh-key", type=Path, help="public key to log in with (default: ~/.ssh/id_ed25519.pub)")
    parser.add_argument("--force", action="store_true", help="push even while a call is in progress")
    parser.add_argument("--yes", action="store_true", help="down: do not ask first")
    args = parser.parse_args()

    try:
        api_key = os.environ.get("VULTR_API_KEY", "").strip()
        if not api_key:
            raise Failure(
                "VULTR_API_KEY is not set. Make a key in the Vultr console (Account, API), allow this\n"
                "computer's IP address for it there, and put VULTR_API_KEY=... in .env."
            )
        api = Vultr(api_key)
        if args.command in ("up", "push", "logs"):
            args.ssh_key = args.ssh_key or default_ssh_key()
        if args.command == "up":
            return up(args, api)
        server = find_server(api)
        if server is None:
            raise Failure("There is no server yet. Create it with: python tools/vultr_deploy.py up")
        if args.command == "push":
            return push(args, server)
        if args.command == "status":
            return status(api, server)
        if args.command == "down":
            return down(args, api, server)
        os.execvp("ssh", ssh_argv(server["main_ip"], args.ssh_key, "-t") + ["journalctl -u shield -f -n 50"])
    except Failure as failure:
        print(failure, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
