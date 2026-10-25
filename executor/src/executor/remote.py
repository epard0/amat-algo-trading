"""Optional Paramiko SSH interface. Install amat-executor[remote] to use it.

Key-only authentication, pinned host identity and bounded concurrent stream draining.
This module does not participate in broker submission or retry unknown commands.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import paramiko

SSH_DEFAULT_PORT = 22


class RemoteError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SSHRunner:
    def __init__(
        self, config: dict[str, Any], client_factory: Callable[[], Any] = paramiko.SSHClient
    ) -> None:
        self.config = config
        self.client_factory = client_factory

    def connect(self) -> Any:
        c = self.config
        if not Path(c["key_path"]).is_file():
            raise RemoteError("key_missing", "SSH private key was not found.")
        if not Path(c["known_hosts"]).is_file():
            raise RemoteError(
                "host_key_missing",
                "SSH known_hosts file is missing; verify the VPS host fingerprint "
                "using OpenSSH first.",
            )
        client = self.client_factory()
        try:
            client.load_host_keys(c["known_hosts"])
            identity = c["host"] if c["port"] == SSH_DEFAULT_PORT else f"[{c['host']}]:{c['port']}"
            if not client.get_host_keys().lookup(identity):
                raise RemoteError(
                    "host_key_missing",
                    "VPS host key is not trusted yet; connect once with OpenSSH "
                    "and verify its fingerprint.",
                )
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            client.connect(
                hostname=c["host"],
                port=c["port"],
                username=c["username"],
                key_filename=c["key_path"],
                passphrase=c.get("key_passphrase") or None,
                allow_agent=False,
                look_for_keys=False,
                timeout=c["connect_timeout"],
                auth_timeout=c["connect_timeout"],
                banner_timeout=c["connect_timeout"],
                channel_timeout=c["connect_timeout"],
            )
            return client
        except RemoteError:
            client.close()
            raise
        except paramiko.BadHostKeyException:
            client.close()
            raise RemoteError(
                "host_key_changed", "VPS host key differs from the trusted key; connection refused."
            ) from None
        except paramiko.PasswordRequiredException:
            client.close()
            raise RemoteError(
                "key_locked", "Enter the private-key passphrase in the local SSH configuration."
            ) from None
        except paramiko.AuthenticationException:
            client.close()
            raise RemoteError(
                "authentication_failed",
                "SSH key authentication failed; verify the configured key "
                "and decryption passphrase.",
            ) from None
        except (OSError, paramiko.SSHException, ValueError):
            client.close()
            raise RemoteError(
                "connection_failed",
                "SSH connection failed; check reachability, host identity and the key passphrase.",
            ) from None

    def execute_command(self, client: Any, command: str, name: str = "command") -> dict[str, Any]:
        """Execute operator-owned shell text with wall-clock and combined output bounds."""
        began = time.monotonic()
        output = bytearray()
        error = bytearray()
        truncated = False
        stdin = stdout = stderr = None
        try:
            stdin, stdout, stderr = client.exec_command(
                command, timeout=self.config["command_timeout"], get_pty=False
            )
            stdin.close()
            channel = stdout.channel
            deadline = began + self.config["command_timeout"]

            def drain(which: Callable[[int], bytes], destination: bytearray) -> None:
                nonlocal truncated
                data = which(4096)
                available = self.config["max_output_bytes"] - len(output) - len(error)
                destination.extend(data[: max(0, available)])
                truncated = truncated or len(data) > available

            while True:
                if time.monotonic() > deadline:
                    return self.result(name, began, output, error, None, "timed_out", truncated)
                if channel.recv_ready():
                    drain(channel.recv, output)
                if channel.recv_stderr_ready():
                    drain(channel.recv_stderr, error)
                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    code = channel.recv_exit_status()
                    return self.result(
                        name,
                        began,
                        output,
                        error,
                        code,
                        "passed" if code == 0 else "needs_attention",
                        truncated,
                    )
                if channel.closed and not channel.recv_ready() and not channel.recv_stderr_ready():
                    return self.result(
                        name, began, output, error, None, "connection_lost", truncated
                    )
                time.sleep(0.01)
        except (OSError, paramiko.SSHException):
            return self.result(name, began, output, error, None, "connection_lost", truncated)
        finally:
            if stdout is not None:
                stdout.channel.close()
            for stream in (stdin, stdout, stderr):
                if stream is not None:
                    stream.close()

    def result(  # noqa: PLR0917 -- shared result hook also used by the gateway policy adapter
        self,
        name: str,
        began: float,
        stdout: bytearray,
        stderr: bytearray,
        code: int | None,
        status: str,
        truncated: bool,
    ) -> dict[str, Any]:
        return {
            "command": name,
            "status": status,
            "exit_code": code,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "truncated": truncated,
            "duration_seconds": round(time.monotonic() - began, 3),
            "observed_at": dt.datetime.now(dt.UTC).isoformat(),
        }

    def run(self, command: str) -> dict[str, Any]:
        client = self.connect()
        try:
            return self.execute_command(client, command)
        finally:
            client.close()
