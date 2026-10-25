"""Economic-controller lifecycle checks, using isolated temporary campaign roots."""

import datetime as dt
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/run_incremental_research_cycle.py"
spec = importlib.util.spec_from_file_location("incremental_cycle", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def seal(path, value):
    module.save(path, value)
    path.with_suffix(".sha256").write_text(module.canonical_sha(value))
    return value


def campaign(
    tmp_path, *, seconds=30, remaining=3600, minimum=0, command=None, receipt_updates=False
):
    protocol = seal(
        tmp_path / "protocol.json",
        {
            "deadline_utc": (module.utcnow() + dt.timedelta(seconds=remaining)).isoformat(),
            "max_cycle_seconds": seconds,
            "min_start_remaining_seconds": minimum,
            "promotion_policy_id": "incumbent-incremental-v1",
            "enable_receipt_baseline_updates": receipt_updates,
        },
    )
    module.save(
        tmp_path / "state.json",
        {"status": "ready", "active_run": None, "baseline": {"profile_sha256": "unchanged"}},
    )
    source = tmp_path / "frozen.py"
    source.write_text(command or "print('measured')")
    return seal(
        tmp_path / "manifest.json",
        {
            "schema": "incremental-research-cycle/1",
            "campaign_root": str(tmp_path),
            "cycle_id": "001-engineering",
            "protocol_sha256": module.canonical_sha(protocol),
            "inputs": [{"path": str(source), "sha256": module.byte_sha(source)}],
            "commands": [{"argv": [sys.executable, str(source)], "cwd": str(tmp_path)}],
        },
    )


def state(path):
    return json.loads((path / "state.json").read_text())


def test_complete_records_receipt_and_preserves_baseline(tmp_path):
    campaign(tmp_path)
    assert module.run(tmp_path / "manifest.json") == 0
    value = state(tmp_path)
    assert value["baseline"] == {"profile_sha256": "unchanged"}
    assert value["active_run"] is None
    receipt = module.sealed(Path(value["last_cycle_receipt"]))
    assert receipt["status"] == "cycle_complete"
    assert receipt["commands"][0]["returncode"] == 0
    assert receipt["execution_authority"] == "none"
    with pytest.raises(FileExistsError):
        module.run(tmp_path / "manifest.json")


def test_near_deadline_requires_finalization_without_launch(tmp_path):
    campaign(tmp_path, remaining=10, minimum=1200)
    assert module.run(tmp_path / "manifest.json") == 0
    assert state(tmp_path)["status"] == "finalization_required"
    assert not (tmp_path / "cycles").exists()


def test_expired_campaign_never_launches(tmp_path):
    campaign(tmp_path, remaining=-1)
    assert module.run(tmp_path / "manifest.json") == 0
    assert state(tmp_path)["status"] == "finalization_required"


def test_hard_maximum_and_shell_string_refused(tmp_path):
    campaign(tmp_path, seconds=2401)
    with pytest.raises(ValueError, match="40 minutes"):
        module.run(tmp_path / "manifest.json")
    manifest = campaign(tmp_path)
    manifest["commands"][0]["argv"] = "echo pretend"
    seal(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="argv"):
        module.run(tmp_path / "manifest.json")


def test_frozen_source_tampering_refused_before_run(tmp_path):
    campaign(tmp_path)
    (tmp_path / "frozen.py").write_text("print('changed')")
    with pytest.raises(ValueError, match="Frozen input"):
        module.run(tmp_path / "manifest.json")
    assert state(tmp_path)["baseline"]["profile_sha256"] == "unchanged"


def test_child_nonzero_fails_without_promotion(tmp_path):
    campaign(tmp_path, command="raise SystemExit(7)")
    assert module.run(tmp_path / "manifest.json") == 1
    assert state(tmp_path)["status"] == "failed"
    assert state(tmp_path)["baseline"]["profile_sha256"] == "unchanged"


def test_timeout_kills_descendants_and_checkpoints(tmp_path):
    campaign(
        tmp_path,
        seconds=3,
        command="""import subprocess,sys,time
from pathlib import Path
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
Path('child.pid').write_text(str(p.pid))
time.sleep(60)
""",
    )
    before = time.monotonic()
    assert module.run(tmp_path / "manifest.json") == 124
    assert time.monotonic() - before < 4
    value = state(tmp_path)
    assert value["status"] == "timed_out"
    assert value["active_run"] is None
    pid = int((tmp_path / "child.pid").read_text())
    for _ in range(50):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        except FileNotFoundError:
            break
        if fields[0] == "Z":
            break
        time.sleep(0.01)
    else:
        pytest.fail("Grandchild remains running after timeout")


def test_stale_run_cannot_be_silently_relabelled_success(tmp_path):
    campaign(tmp_path)
    value = state(tmp_path)
    value["active_run"] = {
        "run_id": "missing-test-token",
        "writer": {"pid": 99999999},
        "child": None,
    }
    module.save(tmp_path / "state.json", value)
    assert module.run(tmp_path / "manifest.json") == 76
    assert state(tmp_path)["status"] == "interrupted_review_required"
    assert not (tmp_path / "cycles").exists()


def test_live_identity_refuses_overlap(tmp_path):
    campaign(tmp_path)
    value = state(tmp_path)
    value["active_run"] = {"run_id": "live-token", "writer": module.process_identity(os.getpid())}
    module.save(tmp_path / "state.json", value)
    assert module.run(tmp_path / "manifest.json") == 75


def test_held_file_lock_refuses_second_writer(tmp_path):
    import fcntl

    campaign(tmp_path)
    with (tmp_path / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert module.run(tmp_path / "manifest.json") == 75


def test_promotion_requires_every_gate_and_preserves_parent(tmp_path):
    manifest = campaign(tmp_path, receipt_updates=True)
    profile = tmp_path / "profile.toml"
    profile.write_text('version="test"\n')
    proof = seal(tmp_path / "proof.json", {"verified": True, "kind": "independent-test"})
    receipt = {
        "schema": "incremental-research-promotion/1",
        "verified": True,
        "policy_id": "incumbent-incremental-v1",
        "parent_baseline_sha256": "unchanged",
        "profile_path": str(profile),
        "profile_sha256": module.byte_sha(profile),
        "gates": dict.fromkeys(module.PROMOTION_GATES, True),
        "evidence": [{"path": str(tmp_path / "proof.json"), "sha256": module.canonical_sha(proof)}],
    }
    receipt["gates"]["native_parity"] = False
    seal(tmp_path / "promotion.json", receipt)
    manifest["promotion_receipt"] = str(tmp_path / "promotion.json")
    seal(tmp_path / "manifest.json", manifest)
    assert module.run(tmp_path / "manifest.json") == 1
    assert state(tmp_path)["baseline"]["profile_sha256"] == "unchanged"
    # Even all-true legacy receipts cannot authorize a research-pointer update.
    receipt["gates"]["native_parity"] = True
    seal(tmp_path / "promotion.json", receipt)
    manifest["cycle_id"] = "002-verified"
    seal(tmp_path / "manifest.json", manifest)
    assert module.run(tmp_path / "manifest.json") == 1
    assert state(tmp_path)["baseline"]["profile_sha256"] == "unchanged"
    failure = module.sealed(Path(state(tmp_path)["last_cycle_receipt"]))["failure"]
    assert "Receipt booleans are unsupported" in failure


def test_frozen_inputs_changed_by_command_fail_postcheck(tmp_path):
    campaign(tmp_path, command="from pathlib import Path;Path('frozen.py').write_text('#changed')")
    assert module.run(tmp_path / "manifest.json") == 1
    assert state(tmp_path)["status"] == "failed"


def test_aggregate_budget_is_shared_by_commands(tmp_path):
    manifest = campaign(tmp_path, seconds=3, command="import time; time.sleep(.55)")
    second = tmp_path / "second.py"
    second.write_text("import time;time.sleep(60)")
    manifest["inputs"].append({"path": str(second), "sha256": module.byte_sha(second)})
    manifest["commands"].append({"argv": [sys.executable, str(second)], "cwd": str(tmp_path)})
    seal(tmp_path / "manifest.json", manifest)
    before = time.monotonic()
    assert module.run(tmp_path / "manifest.json") == 124
    assert time.monotonic() - before < 3
    receipt = module.sealed(Path(state(tmp_path)["last_cycle_receipt"]))
    assert len(receipt["commands"]) == 1


def test_command_background_descendants_are_not_success(tmp_path):
    campaign(
        tmp_path,
        command="""import subprocess,sys
subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],close_fds=True)
""",
    )
    assert module.run(tmp_path / "manifest.json") == 1
    receipt = module.sealed(Path(state(tmp_path)["last_cycle_receipt"]))
    assert "background descendants" in receipt["failure"]


def test_missing_or_changed_promotion_evidence_rejected(tmp_path):
    campaign(tmp_path, receipt_updates=True)
    receipt = {
        "schema": "incremental-research-promotion/1",
        "verified": True,
        "policy_id": "incumbent-incremental-v1",
        "parent_baseline_sha256": "unchanged",
        "gates": dict.fromkeys(module.PROMOTION_GATES, True),
        "evidence": [],
    }
    seal(tmp_path / "promotion.json", receipt)
    with pytest.raises(ValueError, match="Receipt booleans are unsupported"):
        module.validate_promotion(
            module.sealed(tmp_path / "protocol.json"), state(tmp_path), tmp_path / "promotion.json"
        )


def test_controller_sigterm_cleans_up_and_records_failure(tmp_path):
    campaign(tmp_path, command="import time;time.sleep(60)")
    process = subprocess.Popen(  # noqa: S603 - fixed test interpreter and local script
        [sys.executable, str(SCRIPT), "--manifest", str(tmp_path / "manifest.json")]
    )
    try:
        limit = time.monotonic() + 3
        while time.monotonic() < limit:
            active = state(tmp_path).get("active_run")
            if active and active.get("child"):
                break
            time.sleep(0.01)
        else:
            pytest.fail("Controller did not launch test child")
        child = active["child"]
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=3) == 1
        assert not module.identity_alive(child)
        assert state(tmp_path)["status"] == "failed"
        assert state(tmp_path)["active_run"] is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def detached_source(*, wait):
    return f"""import subprocess,sys,time
from pathlib import Path
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],
    start_new_session=True,close_fds=True)
Path('detached.pid').write_text(str(p.pid))
{"time.sleep(60)" if wait else ""}
"""


def assert_detached_dead(tmp_path):
    pid = int((tmp_path / "detached.pid").read_text())
    assert module.process_identity(pid) is None


def test_detached_success_cannot_leave_worker_running(tmp_path):
    campaign(tmp_path, command=detached_source(wait=False))
    assert module.run(tmp_path / "manifest.json") == 1
    assert_detached_dead(tmp_path)
    receipt = module.sealed(Path(state(tmp_path)["last_cycle_receipt"]))
    assert "detached" in receipt["failure"]
    assert receipt["cleanup_survivors"] == []


def test_timeout_kills_detached_worker(tmp_path):
    campaign(tmp_path, seconds=3, command=detached_source(wait=True))
    before = time.monotonic()
    assert module.run(tmp_path / "manifest.json") == 124
    assert time.monotonic() - before < 3
    assert_detached_dead(tmp_path)


def test_sigterm_kills_detached_worker(tmp_path):
    campaign(tmp_path, command=detached_source(wait=True))
    process = subprocess.Popen(  # noqa: S603 - fixed test interpreter and controller
        [sys.executable, str(SCRIPT), "--manifest", str(tmp_path / "manifest.json")]
    )
    try:
        limit = time.monotonic() + 3
        while not (tmp_path / "detached.pid").exists():
            if time.monotonic() >= limit:
                pytest.fail("Detached test worker did not start")
            time.sleep(0.01)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=3) == 1
        assert_detached_dead(tmp_path)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_identity_mismatch_never_signals_reused_pid(monkeypatch):
    identity = module.process_identity(os.getpid())
    identity["start_ticks"] = str(int(identity["start_ticks"]) + 1)
    sent = []
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    module.signal_identity(identity, signal.SIGKILL)
    assert sent == []


def test_receipt_baseline_updates_disabled_by_default(tmp_path):
    campaign(tmp_path)
    with pytest.raises(ValueError, match="disabled"):
        module.validate_promotion(
            module.sealed(tmp_path / "protocol.json"),
            state(tmp_path),
            tmp_path / "untrusted-promotion.json",
        )
    assert state(tmp_path)["baseline"]["profile_sha256"] == "unchanged"


@pytest.mark.parametrize("field", ["max_cycle_seconds", "min_start_remaining_seconds"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_time_limits_refused(tmp_path, field, value):
    campaign(tmp_path)
    protocol = json.loads((tmp_path / "protocol.json").read_text())
    protocol[field] = value
    # A valid seal cannot contain nonfinite numbers, so validate fail-closed
    # even if an external serializer emitted a non-standard JSON number.
    (tmp_path / "protocol.json").write_text(json.dumps(protocol))
    with pytest.raises(ValueError):
        module.run(tmp_path / "manifest.json")


@pytest.mark.parametrize("field", ["max_cycle_seconds", "min_start_remaining_seconds"])
@pytest.mark.parametrize("value", ["nan", "inf"])
def test_sealed_nonfinite_string_time_limits_refused(tmp_path, field, value):
    manifest = campaign(tmp_path)
    protocol = module.sealed(tmp_path / "protocol.json")
    protocol[field] = value
    seal(tmp_path / "protocol.json", protocol)
    manifest["protocol_sha256"] = module.canonical_sha(protocol)
    seal(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError):
        module.run(tmp_path / "manifest.json")
