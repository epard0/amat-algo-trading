import json
import shlex
from types import SimpleNamespace

import paramiko
import pytest
from fastapi.testclient import TestClient

from server.app import Service, create_app
from server.remote import RemoteError, RemoteMonitor, Runner, commands, load_config, safe_output


def configured(tmp_path):
    path = tmp_path / "remote.toml"
    key = tmp_path / "private-key"
    hosts = tmp_path / "known_hosts"
    key.write_text("mock key, never used for live auth")
    hosts.write_text("mock trusted key")
    path.write_text(
        'host = "vps.example.invalid"\nport = 2200\nusername = "operator"\n'
        f'key_path = "{key}"\nknown_hosts = "{hosts}"\nkey_passphrase = "private-passphrase"\n'
    )
    path.chmod(0o600)
    return path, load_config(path)


class Channel:
    def __init__(self, out=b'{"healthy": true}', err=b"", code=0):
        self.out, self.err, self.code = out, err, code
        self.closed = False

    def recv_ready(self):
        return bool(self.out)

    def recv_stderr_ready(self):
        return bool(self.err)

    def recv(self, n):
        data, self.out = self.out[:n], self.out[n:]
        return data

    def recv_stderr(self, n):
        data, self.err = self.err[:n], self.err[n:]
        return data

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return self.code

    def close(self):
        self.closed = True


class Client:
    def __init__(self, channel=None, trusted=True):
        self.channel = channel or Channel()
        self.trusted = trusted
        self.kwargs = None
        self.closed = False

    def load_host_keys(self, path):
        self.hosts = path

    def get_host_keys(self):
        return SimpleNamespace(lookup=lambda identity: {"key": "trusted"} if self.trusted else None)

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, **kwargs):
        self.kwargs = kwargs

    def close(self):
        self.closed = True

    def exec_command(self, command, **kwargs):
        self.command, self.exec_options = command, kwargs
        stream = SimpleNamespace(channel=self.channel, close=lambda: None)
        return stream, stream, stream


def test_private_config_and_safe_command_builders(tmp_path):
    path, config = configured(tmp_path)
    assert (config["username"], config["host"], config["port"]) == (
        "operator",
        "vps.example.invalid",
        2200,
    )
    argv = shlex.split(commands(config)["health"])
    assert argv == ["docker", "exec", "amat-executor", "executor", "health"]
    inspect = commands(config)["container"]
    assert ".Config.Env" not in inspect and ".RestartCount" in inspect
    # Validate remote Python syntax without running host or container probes.
    compile(shlex.split(commands(config)["host"])[-1], "<host>", "exec")
    compile(shlex.split(commands(config)["performance"])[-1], "<performance>", "exec")
    path.chmod(0o644)
    with pytest.raises(RemoteError, match="chmod 600"):
        load_config(path)
    path.chmod(0o600)
    path.write_text('key_passphrase = "unterminated')
    with pytest.raises(RemoteError) as exc:
        load_config(path)
    assert exc.value.code == "config_invalid" and "unterminated" not in str(exc.value)


def test_auth_uses_only_explicit_key_passphrase_and_verified_host(tmp_path):
    _, config = configured(tmp_path)
    client = Client()
    assert Runner(config, lambda: client).connect() is client
    assert client.kwargs["passphrase"] == "private-passphrase"
    assert "password" not in client.kwargs
    assert client.kwargs["allow_agent"] is False and client.kwargs["look_for_keys"] is False
    assert isinstance(client.policy, paramiko.RejectPolicy)
    untrusted = Client(trusted=False)
    with pytest.raises(RemoteError) as exc:
        Runner(config, lambda: untrusted).connect()
    assert exc.value.code == "host_key_missing" and untrusted.closed
    assert untrusted.kwargs is None


def test_results_exit_status_stderr_bounds_and_redaction(tmp_path):
    _, config = configured(tmp_path)
    channel = Channel(
        b'{"healthy":false,"token":"remote-secret","detail":"private-passphrase"}',
        b"permission denied",
        0,
    )
    client = Client(channel)
    result = Runner(config).execute(client, "health")
    assert result["status"] == "needs_attention" and result["exit_code"] == 0
    assert result["stderr"] == "permission denied"
    assert "remote-secret" not in json.dumps(result) and "private-passphrase" not in json.dumps(
        result
    )
    assert result["data"]["token"] == "[redacted]" and channel.closed
    config["max_output_bytes"] = 1024
    result = Runner(config).execute(Client(Channel(b"x" * 5000, b"error", 1)), "logs")
    assert result["truncated"] and len(result["stdout"]) + len(result["stderr"]) <= 1024
    assert result["status"] == "needs_attention"
    with pytest.raises(RemoteError):
        Runner(config).execute(Client(), "submit")
    assert "secret-value" not in safe_output('token="secret-value"', config)


def test_absolute_timeout_closes_channel(tmp_path, monkeypatch):
    _, config = configured(tmp_path)
    channel = Channel()
    client = Client(channel)
    times = iter([0, 100, 100])
    monkeypatch.setattr("server.remote.time.monotonic", lambda: next(times))
    result = Runner(config).execute(client, "health")
    assert result["status"] == "timed_out" and result["exit_code"] is None and channel.closed


def test_remote_api_csrf_names_and_secret_free_config(tmp_path, monkeypatch):
    path, _ = configured(tmp_path)
    monkeypatch.setenv("TERMINAL_REMOTE_CONFIG", str(path))
    service = Service(tmp_path, storage=tmp_path / "store")
    client = TestClient(create_app(service))
    assert client.get("/api/v1/remote").status_code == 401
    csrf = client.get("/api/v1/session").json()["csrf_token"]
    response = client.get("/api/v1/remote")
    assert response.status_code == 200 and response.json()["passphrase_configured"]
    assert "private-passphrase" not in response.text
    assert client.post("/api/v1/remote/run", json={}).status_code == 403
    headers = {"X-CSRF-Token": csrf}
    assert (
        client.post("/api/v1/remote/run", headers=headers, json={"command": "rm -rf /"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/remote/run", headers=headers, json={"checks": ["not_registered"]}
        ).status_code
        == 422
    )
    # No SSH is executed by these request validation tests.


def test_restart_marks_incomplete_run_without_losing_last_complete(tmp_path):
    service = Service(tmp_path, storage=tmp_path / "store")
    monitor = RemoteMonitor(service)
    service.db.execute(
        "INSERT OR REPLACE INTO observations VALUES (?,?)",
        ("remote.executor.completed", json.dumps({"id": "old", "status": "passed"})),
    )
    service.db.commit()
    monitor.save({"id": "new", "status": "running"})
    restarted = RemoteMonitor(service).describe()
    assert restarted["last_run"]["status"] == "interrupted"
    assert restarted["last_completed_run"]["id"] == "old"


def test_closed_channel_drains_buffered_output_before_classifying_connection(tmp_path):
    _, config = configured(tmp_path)
    channel = Channel(b'{"orders": "' + b"x" * 20000 + b'"}')
    channel.closed = True
    result = Runner(config).execute(Client(channel), "status")
    assert result["status"] == "passed" and result["exit_code"] == 0
    assert len(result["data"]["orders"]) == 20000


def test_health_summary_uses_isolated_fixed_helper_and_remote_deadline(tmp_path):
    _, config = configured(tmp_path)
    assert shlex.split(commands(config)["health_summary"]) == [
        "timeout",
        "30",
        "python3",
        "-I",
        "/home/operator/.local/bin/amat-vps-control",
        "health",
    ]


@pytest.mark.parametrize("field", ["host", "port", "username", "key_path", "known_hosts"])
def test_connection_identity_has_no_source_defaults(tmp_path, field):
    path, _ = configured(tmp_path)
    path.write_text(
        "\n".join(
            line for line in path.read_text().splitlines() if not line.startswith(field + " =")
        )
        + "\n"
    )
    with pytest.raises(RemoteError) as exc:
        load_config(path)
    assert exc.value.code == "config_invalid"
    assert field in str(exc.value)
    assert "private-passphrase" not in str(exc.value)


@pytest.mark.parametrize("field", ["key_path", "known_hosts"])
@pytest.mark.parametrize("value", ["", "   ", "first\nsecond", "bad\x00path", 42])
def test_ssh_paths_must_be_explicit_nonempty_local_paths(tmp_path, field, value):
    path, _ = configured(tmp_path)
    lines = [line for line in path.read_text().splitlines() if not line.startswith(field + " =")]
    path.write_text("\n".join(lines) + f"\n{field} = {json.dumps(value)}\n")
    with pytest.raises(RemoteError) as exc:
        load_config(path)
    assert exc.value.code == "config_invalid"


def test_missing_private_config_fails_without_fallback(tmp_path):
    with pytest.raises(RemoteError) as exc:
        load_config(tmp_path / "not-created.toml")
    assert exc.value.code == "config_missing"


def test_default_and_override_config_paths(tmp_path, monkeypatch):
    from server.remote import config_path

    monkeypatch.delenv("TERMINAL_REMOTE_CONFIG", raising=False)
    assert config_path(tmp_path) == tmp_path / "remote.toml"
    private = tmp_path / "private" / "remote.toml"
    monkeypatch.setenv("TERMINAL_REMOTE_CONFIG", str(private))
    assert config_path(tmp_path) == private
