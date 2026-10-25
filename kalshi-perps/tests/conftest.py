import json
from pathlib import Path

import pytest

from kalshi_perps.domain import registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def instruments():
    return registry(json.loads((ROOT / "configs/instruments.json").read_text()))


@pytest.fixture
def btc(instruments):
    return instruments["KXBTCPERP"]


@pytest.fixture
def quote_btc():
    return {"bids": [["7.9900", "10000"]], "asks": [["8.0100", "10000"]], "mark": "8"}
