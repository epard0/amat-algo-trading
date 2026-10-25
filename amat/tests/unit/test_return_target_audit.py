"""Allow only the expected terminal-decision omission in a prefix audit."""

import copy
import importlib.util
from pathlib import Path

import pytest


def audit_module():
    path = Path(__file__).resolve().parents[2] / "scripts/audit_return_target.py"
    spec = importlib.util.spec_from_file_location("return_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def records():
    first = {
        "equity": "100",
        "cash": "50",
        "fills": [],
        "missed": [],
        "forecast_volatility_pre": 0.1,
        "forecast_volatility_post": 0.06,
        "binding_constraints": ["symbol_cap"],
    }
    training = [
        first,
        {
            **first,
            "forecast_volatility_pre": None,
            "forecast_volatility_post": None,
            "binding_constraints": [],
        },
    ]
    extended = [first, copy.deepcopy(first), copy.deepcopy(first)]
    return copy.deepcopy(training), extended


def test_terminal_decision_omission_is_expected():
    training, extended = records()
    assert audit_module().prefix_check(training, extended)["passed"]


@pytest.mark.parametrize(
    "index,key,value",
    [
        (0, "forecast_volatility_post", 0.2),
        (0, "equity", "101"),
        (1, "equity", "101"),
        (1, "cash", "51"),
        (1, "fills", ["unexpected"]),
        (1, "missed", ["unexpected"]),
    ],
)
def test_changed_financial_or_nonterminal_decision_fails(index, key, value):
    training, extended = records()
    extended[index][key] = value
    assert not audit_module().prefix_check(training, extended)["passed"]
