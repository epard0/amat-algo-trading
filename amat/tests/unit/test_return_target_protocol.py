"""The target is frozen at registration and applied to both historical periods."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/return_target_study.py"
    spec = importlib.util.spec_from_file_location("target_study", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def registered(tmp_path, monkeypatch, target=0.20):
    study = module()
    monkeypatch.setattr(study, "ROOT", tmp_path / "source")
    monkeypatch.setattr(study, "RECIPES", {"baseline": {}, "candidate": {}})
    monkeypatch.setattr(study, "source_hashes", lambda: {})
    monkeypatch.setattr(study, "load_manifest", lambda _: SimpleNamespace(partition_sha256={}))
    monkeypatch.setattr(study, "make_config", lambda *_: SimpleNamespace(model_dump=lambda **_: {}))
    study.register(tmp_path, target_cagr=target)
    return study


def test_target_frozen_in_protocol_and_rule(tmp_path, monkeypatch):
    study = registered(tmp_path, monkeypatch)
    value = study.read_protocol(tmp_path)
    assert value["target_cagr"] == 0.20
    assert "20.00%" in value["historical_target_rule"]
    with pytest.raises(ValueError, match="already exists"):
        study.register(tmp_path, target_cagr=0.10)
    value["target_cagr"] = 0.10
    study.write(tmp_path / "protocol.json", value)
    with pytest.raises(ValueError, match="changed"):
        study.read_protocol(tmp_path)


@pytest.mark.parametrize("target", [0, -0.1, 20, "nan", "inf"])
def test_invalid_target_rejected_before_writing(tmp_path, target):
    with pytest.raises(ValueError, match="target CAGR"):
        module().register(tmp_path, target_cagr=target)
    assert not (tmp_path / "protocol.json").exists()


@pytest.mark.parametrize(
    ("target", "later", "passes"), [(0.1, 0.15, True), (0.2, 0.15, False), (0.2, 0.21, True)]
)
def test_validation_uses_registered_target(tmp_path, monkeypatch, target, later, passes):
    study = registered(tmp_path, monkeypatch, target)
    study.write(
        tmp_path / "development.json",
        {"candidate": {c: {"annualized_return": 0.21} for c in ("2", "6")}},
    )
    study.write(
        tmp_path / "selection.json",
        {
            "selected": "candidate",
            "development_sha256": hashlib.sha256(
                (tmp_path / "development.json").read_bytes()
            ).hexdigest(),
        },
    )
    fake = SimpleNamespace(protocol=study.read_protocol(tmp_path), run=lambda *_, **__: None)
    monkeypatch.setattr(study, "Study", lambda *_: fake)
    monkeypatch.setattr(study, "measured", lambda *_: None)
    metrics = Mock(return_value={"annualized_return": later, "max_drawdown": -0.1})
    monkeypatch.setattr(study, "details", metrics)
    study.validate(tmp_path)
    decision = json.loads((tmp_path / "decision.json").read_text())
    assert decision["historical_target_met"] is passes
    assert decision["target_cagr"] == target
    assert decision["future_target_established"] is False
    assert all(call.kwargs["target_cagr"] == target for call in metrics.call_args_list)


def test_run_does_not_open_validation_without_eligible_candidate(tmp_path, monkeypatch):
    study = module()
    register = Mock()
    validate = Mock()
    monkeypatch.setattr(study, "register", register)
    monkeypatch.setattr(study, "validate", validate)
    monkeypatch.setattr(
        study, "screen", lambda output: study.write(output / "selection.json", {"selected": None})
    )
    study.run_study(tmp_path, None, target_cagr=0.2)
    assert register.call_args.kwargs["target_cagr"] == 0.2
    validate.assert_not_called()
    assert json.loads((tmp_path / "decision.json").read_text())["validation_opened"] is False
