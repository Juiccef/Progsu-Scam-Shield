import argparse
import io
import json
import subprocess
import tarfile

import httpx
import pytest
from dotenv import dotenv_values

from tools import vultr_deploy as deploy

SERVER = {"id": "i-1", "main_ip": "203.0.113.7", "status": "active"}


def vultr(handler):
    return deploy.Vultr("test-key", transport=httpx.MockTransport(handler))


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Stands in for the server and this computer's settings: records what `push` would run over SSH."""
    local = tmp_path / ".env"
    local.write_text("TWILIO_AUTH_TOKEN=tw\nSCORER_MODEL=\nANTHROPIC_API_KEY=\nVULTR_API_KEY=account-key\n")
    monkeypatch.setattr(deploy, "LOCAL_ENV", local)
    monkeypatch.setattr(deploy, "SERVER_ENV", tmp_path / ".env.vultr")
    monkeypatch.setattr(deploy, "tracked_files", lambda: ["main.py", "requirements.txt"])
    monkeypatch.setattr(deploy.socket, "gethostbyname", lambda host: "203.0.113.7")
    monkeypatch.setattr(deploy, "wait_for_health", lambda host: {"scorer_model": "claude-haiku-4-5", "actions": "live"})
    for name in ("OPENAI_API_KEY", "SCORER_MODEL", "EVENTS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    ran = []

    def remote(ip, public_key, command, *, input=None, quiet=False):
        ran.append((command, input))
        busy = command.startswith("curl") and getattr(remote, "calls_in_progress", 0)
        return subprocess.CompletedProcess([], 0, stdout=json.dumps({"active_calls": busy}).encode(), stderr=b"")

    monkeypatch.setattr(deploy, "remote", remote)
    remote.ran = ran
    return remote


def push(**options):
    args = argparse.Namespace(host=None, force=False, ssh_key=deploy.Path("unused.pub"))
    vars(args).update(options)
    return deploy.push(args, SERVER)


# --- the settings that go to the server -------------------------------------------


def test_server_env_puts_the_server_file_on_top_and_never_sends_the_account_key():
    local = {
        "TWILIO_AUTH_TOKEN": "tw",
        "DEMO_MODE": "false",
        "PUBLIC_HOST": "abc.ngrok-free.app",
        "VULTR_API_KEY": "account-key",
        "ANTHROPIC_API_KEY": "",
    }
    overrides = {"DEMO_MODE": "true", "VULTR_API_KEY": "account-key"}
    shell = {"ANTHROPIC_API_KEY": "sk-ant", "GROQ_API_KEY": "gsk", "HOME": "/Users/someone"}
    assert deploy.server_env(local, overrides, shell, "203-0-113-7.sslip.io") == {
        "TWILIO_AUTH_TOKEN": "tw",
        "DEMO_MODE": "true",
        "PUBLIC_HOST": "203-0-113-7.sslip.io",
        "ANTHROPIC_API_KEY": "sk-ant",  # the scorer's key, from the shell; nothing else from there
    }


def test_server_env_takes_the_key_of_the_model_the_server_will_run():
    shell = {"ANTHROPIC_API_KEY": "sk-ant", "VULTR_INFERENCE_API_KEY": "vk"}
    env = deploy.server_env({}, {"SCORER_MODEL": "vultr-decision:clef-flash"}, shell, "shield.example.com")
    assert env["VULTR_INFERENCE_API_KEY"] == "vk" and "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in deploy.server_env({}, {"SCORER_MODEL": "rules"}, shell, "shield.example.com")
    env = deploy.server_env({}, {"SCORER_MODEL": "jev-latest"}, {**shell, "TYPESAFE_API_KEY": "ts"}, "shield.example.com")
    assert env["TYPESAFE_API_KEY"] == "ts" and "VULTR_INFERENCE_API_KEY" not in env


def test_rendered_env_reads_back_the_same(tmp_path):
    env = {
        "SENIOR_NAME": "your family member",
        "SAY_VOICE": "Polly.Joanna-Neural",
        "TWILIO_PROXY_NUMBER": "+14045550100",
        "ODD": 'a "quoted" #value\\ with = signs',
        "EMPTY": "",
    }
    path = tmp_path / ".env"
    path.write_text(deploy.render_env(env), encoding="utf-8")
    assert dict(dotenv_values(path, interpolate=False)) == env


def test_a_bare_address_gets_a_name():
    assert deploy.sslip_host("203.0.113.7") == "203-0-113-7.sslip.io"


@pytest.mark.skipif(not (deploy.ROOT / ".git").exists(), reason="needs the git checkout")
def test_what_is_sent_is_the_tracked_code_without_secrets_or_the_app():
    files = deploy.tracked_files()
    assert {"main.py", "requirements.txt", "prompts/scam-scorer-prompt.md"} <= set(files)
    assert ".env" not in files and ".env.vultr" not in files
    assert not any(name.startswith(("ios/", ".venv/", "logs/")) for name in files)
    with tarfile.open(fileobj=io.BytesIO(deploy.archive(files[:3])), mode="r:gz") as tar:
        assert tar.getnames() == files[:3]


def test_only_a_public_key_is_registered(tmp_path):
    private = tmp_path / "id_ed25519"
    private.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n")
    with pytest.raises(deploy.Failure, match="not an SSH public key"):
        deploy.public_key_text(private)
    public = tmp_path / "id_ed25519.pub"
    public.write_text("ssh-ed25519 AAAAmine me@laptop\n")
    assert deploy.public_key_text(public) == "ssh-ed25519 AAAAmine me@laptop"


# --- push ------------------------------------------------------------------------


def sent_settings(data: bytes) -> dict:
    return dict(dotenv_values(stream=io.StringIO(data.decode())))


def test_push_unpacks_the_code_writes_the_settings_and_runs_the_setup(server, capsys):
    assert push() == 0
    commands = [command for command, _ in server.ran]
    assert commands[0].startswith("curl")  # asks first whether a call is in progress
    assert commands[1:] == [
        deploy.UNPACK,
        "umask 077 && cat > /opt/shield/.env",
        "bash /opt/shield/deploy/vultr-setup.sh 203-0-113-7.sslip.io",
    ]
    with tarfile.open(fileobj=io.BytesIO(server.ran[1][1]), mode="r:gz") as tar:
        assert tar.getnames() == ["main.py", "requirements.txt"]
    sent = sent_settings(server.ran[2][1])
    token = sent.pop("EVENTS_TOKEN")
    assert sent == {
        "TWILIO_AUTH_TOKEN": "tw",
        "SCORER_MODEL": "",
        "ANTHROPIC_API_KEY": "sk-ant",
        "DEMO_MODE": "true",
        "LOG_TRANSCRIPTS": "false",
        "PUBLIC_HOST": "203-0-113-7.sslip.io",
    }
    assert len(token) == 16 and token in capsys.readouterr().out  # made up for the server and shown
    assert dotenv_values(deploy.SERVER_ENV)["EVENTS_TOKEN"] == token  # and kept for the next push


def test_a_second_push_keeps_the_access_token(server):
    push()
    push()
    tokens = [sent_settings(data)["EVENTS_TOKEN"] for command, data in server.ran if command.startswith("umask")]
    assert len(tokens) == 2 and tokens[0] == tokens[1]


def test_push_refuses_while_a_call_is_in_progress(server):
    server.calls_in_progress = 1
    with pytest.raises(deploy.Failure, match="call is in progress"):
        push()
    assert all(command.startswith("curl") for command, _ in server.ran)  # nothing was sent
    assert push(force=True) == 0


def test_your_own_domain_is_remembered_and_must_point_at_the_server(server, monkeypatch):
    assert push(host="https://shield.example.com/") == 0
    assert server.ran[-1][0] == "bash /opt/shield/deploy/vultr-setup.sh shield.example.com"
    assert push() == 0 and server.ran[-1][0].endswith(" shield.example.com")
    with pytest.raises(deploy.Failure, match="not a host name"):
        push(host="shield.example.com; rm -rf /")
    monkeypatch.setattr(deploy.socket, "gethostbyname", lambda host: "198.51.100.9")
    with pytest.raises(deploy.Failure, match="points at 198.51.100.9, not at the server"):
        push()


# --- the Vultr API ---------------------------------------------------------------


def test_the_server_is_found_by_its_label():
    servers = [
        {"id": "a", "label": "blog", "main_ip": "203.0.113.1"},
        {"id": "b", "label": "shield", "main_ip": "203.0.113.7"},
    ]
    assert deploy.find_server(vultr(lambda request: httpx.Response(200, json={"instances": servers})))["id"] == "b"
    assert deploy.find_server(vultr(lambda request: httpx.Response(200, json={"instances": servers[:1]}))) is None
    twice = vultr(lambda request: httpx.Response(200, json={"instances": [servers[1], servers[1]]}))
    with pytest.raises(deploy.Failure, match="2 servers"):
        deploy.find_server(twice)


def test_a_new_server_is_ubuntu_with_this_computers_ssh_key():
    requests = []

    def handler(request):
        requests.append(request)
        path = request.url.path
        if path == "/v2/ssh-keys" and request.method == "GET":
            return httpx.Response(200, json={"ssh_keys": [{"id": "k-old", "ssh_key": "ssh-ed25519 AAAAother x@y"}]})
        if path == "/v2/ssh-keys":
            return httpx.Response(201, json={"ssh_key": {"id": "k-new"}})
        if path == "/v2/os":
            images = [{"id": 1743, "name": "Ubuntu 22.04 LTS x64"}, {"id": 2284, "name": "Ubuntu 24.04 LTS x64"}]
            return httpx.Response(200, json={"os": images})
        return httpx.Response(202, json={"instance": {"id": "i-1", "main_ip": "0.0.0.0", "status": "pending"}})

    api = vultr(handler)
    key_id = deploy.ssh_key_id(api, "ssh-ed25519 AAAAmine me@laptop\n")
    server = deploy.create_server(api, "atl", "vc2-1c-1gb", key_id)
    assert key_id == "k-new" and server["id"] == "i-1"
    assert requests[0].headers["authorization"] == "Bearer test-key"
    assert json.loads(requests[1].content) == {"name": "shield", "ssh_key": "ssh-ed25519 AAAAmine me@laptop"}
    assert (requests[-1].method, requests[-1].url.path) == ("POST", "/v2/instances")
    assert json.loads(requests[-1].content) == {
        "region": "atl",
        "plan": "vc2-1c-1gb",
        "os_id": 2284,
        "label": "shield",
        "hostname": "shield",
        "sshkey_id": ["k-new"],
        "backups": "disabled",
        "activation_email": False,
    }


def test_an_ssh_key_already_at_vultr_is_reused():
    def handler(request):
        assert request.method == "GET"  # nothing is added
        return httpx.Response(200, json={"ssh_keys": [{"id": "k-1", "ssh_key": "ssh-ed25519 AAAAmine old-name"}]})

    assert deploy.ssh_key_id(vultr(handler), "ssh-ed25519 AAAAmine me@laptop") == "k-1"


def test_waiting_ends_when_the_server_has_an_address():
    pending = {"id": "i-1", "status": "pending", "main_ip": "0.0.0.0"}
    states = iter([pending, {"id": "i-1", "status": "active", "main_ip": "203.0.113.7"}])
    api = vultr(lambda request: httpx.Response(200, json={"instance": next(states)}))
    assert deploy.wait_until_active(api, pending, poll_s=0)["main_ip"] == "203.0.113.7"


@pytest.mark.parametrize(
    "message, advice",
    [
        ("Invalid API token.", "Check VULTR_API_KEY: letters and digits only"),
        ("Unauthorized IP address: 131.96.221.135", "not on the key's allowlist"),
        ("API use is not enabled for this account.", "Switch API access on"),
    ],
)
def test_a_refused_key_says_which_step_is_missing(message, advice):
    api = vultr(lambda request: httpx.Response(401, json={"error": message, "status": 401}))
    with pytest.raises(deploy.Failure) as refused:
        deploy.find_server(api)
    text = str(refused.value)
    assert f"HTTP 401: {message}\n" in text and advice in text and "My Org, Manage Organization" in text


def test_status_says_how_text_protection_stands():
    health = {"scorer_model": "claude-haiku-4-5", "actions": "live", "active_calls": 0}
    assert "texts: off |" in deploy.describe(health)  # a server from before text protection says nothing of it
    assert "texts: off |" in deploy.describe({**health, "gateway": {"enabled": False}})
    paused = {"enabled": True, "forwarding_enabled": False}
    assert "texts: on, forwarding paused |" in deploy.describe({**health, "gateway": paused})
    assert "texts: on |" in deploy.describe({**health, "gateway": {"enabled": True, "forwarding_enabled": True}})
    wrong = {"enabled": False, "error": "GATEWAY_ENCRYPTION_KEY must be a Fernet key"}
    assert "texts: OFF, a setting is wrong (GATEWAY_ENCRYPTION_KEY must be a Fernet key)" in deploy.describe(
        {**health, "gateway": wrong}
    )


# --- the setup script's Caddy step -----------------------------------------------


def caddy_step(tmp_path, *, running: str, caddy_accepts: bool):
    """Runs the part of deploy/vultr-setup.sh that writes the Caddyfile, with /etc/caddy in a
    temporary folder and a stand-in `caddy` that accepts the new file or does not."""
    script = (deploy.ROOT / "deploy" / "vultr-setup.sh").read_text()
    start, end = script.index('echo "== HTTPS for $HOST"'), script.index("# Vultr's Ubuntu images come with ufw")
    etc, stubs = tmp_path / "etc-caddy", tmp_path / "bin"
    etc.mkdir()
    stubs.mkdir()
    if running:
        (etc / "Caddyfile").write_text(running)
    stand_in = stubs / "caddy"
    stand_in.write_text(f"#!/bin/sh\necho 'adapting config: stand-in says no' >&2\nexit {0 if caddy_accepts else 1}\n")
    stand_in.chmod(0o755)
    step = "set -euo pipefail\nHOST=palisade.example.test\n" + script[start:end].replace("/etc/caddy", str(etc))
    result = subprocess.run(
        ["bash", "-c", step], capture_output=True, text=True, env={"PATH": f"{stubs}:/usr/bin:/bin"}, check=True
    )
    return etc, result


IN_USE = "old.example.test {\n\treverse_proxy 127.0.0.1:8000\n}\n"


def test_setup_closes_the_internal_pages_to_the_outside(tmp_path):
    etc, _ = caddy_step(tmp_path, running=IN_USE, caddy_accepts=True)
    caddyfile = (etc / "Caddyfile").read_text()
    assert caddyfile.startswith("palisade.example.test {")
    assert "@internal path /integrations/*\n\trespond @internal 404\n\treverse_proxy 127.0.0.1:8000" in caddyfile
    assert not (etc / "Caddyfile.new").exists()


def test_setup_keeps_the_caddyfile_in_use_when_caddy_refuses_the_new_one(tmp_path):
    """The server may be taking calls: HTTPS must not go down for a file Caddy cannot load."""
    etc, result = caddy_step(tmp_path, running=IN_USE, caddy_accepts=False)
    assert (etc / "Caddyfile").read_text() == IN_USE
    assert not (etc / "Caddyfile.new").exists()
    assert "the one in use stays" in result.stderr and "stand-in says no" in result.stderr


@pytest.mark.parametrize("packaged", ["", ":80 {\n\troot * /usr/share/caddy\n\tfile_server\n}\n"])
def test_setup_writes_the_caddyfile_on_a_new_server_whatever_the_check_says(tmp_path, packaged):
    """Nothing is running there yet, so there is nothing to protect, and the server needs its HTTPS."""
    etc, _ = caddy_step(tmp_path, running=packaged, caddy_accepts=False)
    assert "reverse_proxy 127.0.0.1:8000" in (etc / "Caddyfile").read_text()
