import datetime as dt
import json
from types import SimpleNamespace

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.domain.types import Symbol
from autoresearch.genetic import shadow_campaign as campaign
from autoresearch.genetic.shadow import _prefix


@pytest.fixture
def setup(monkeypatch, tmp_path):
    days = tuple(dt.date(2026, 9, day) for day in (24, 25, 28, 29, 30))
    values = np.arange(5.0).reshape(-1, 1) + 100
    panel = MarketPanel(
        sessions=days,
        symbols=(Symbol("SPY"),),
        source="fixture",
        adjusted_closes=values.copy(),
        raw_closes=values.copy(),
        window_vwaps=values.copy(),
        window_volumes=values.copy(),
        adjusted_opens=values.copy(),
        adjusted_highs=values.copy(),
        adjusted_lows=values.copy(),
        session_volumes=values.copy(),
    )
    baseline = _prefix(panel, days[1])
    manifest = SimpleNamespace(
        feed="sip",
        execution_window_start=dt.time(9, 30),
        execution_window_end=dt.time(9, 35),
        exchange_timezone="America/New_York",
        model_dump=lambda **_: {"feed": "sip"},
    )
    artifact = {
        "payload_sha256": "a" * 64,
        "payload": {
            "candidate": {},
            "universe": ["SPY"],
            "calibration": {"start": str(days[0]), "end": str(days[1])},
        },
    }
    clock = [dt.datetime(2026, 9, 28, 12, tzinfo=dt.UTC)]
    monkeypatch.setattr(campaign, "_now", lambda: clock[0])
    monkeypatch.setattr(campaign, "load_shadow_artifact", lambda _: artifact)
    monkeypatch.setattr(campaign, "load_panel", lambda *_, **__: (baseline, manifest))
    monkeypatch.setattr(campaign, "verify_calibration", lambda *_: None)
    monkeypatch.setattr(
        campaign,
        "Candidate",
        SimpleNamespace(
            model_validate=lambda _: SimpleNamespace(genetic_pattern=SimpleNamespace(pattern={}))
        ),
    )
    monkeypatch.setattr(campaign, "multi_timeframe_report", lambda *_, **__: {"fixture": True})

    def calendar(sessions, **_):
        return {
            "sessions": [
                {"open": f"{day}T13:30:00+00:00", "close": f"{day}T20:00:00+00:00"}
                for day in sessions
            ]
        }

    monkeypatch.setattr(campaign, "audit_sessions", calendar)
    calls = []

    def feed(*_, through, **__):
        calls.append(through)
        return _prefix(panel, through)

    monkeypatch.setattr(campaign, "extend_shadow_panel", feed)

    def infer(*_):
        return {
            "diagnostic_only": True,
            "execution_authority": "none",
            "promotion_authority": "none",
            "artifact_sha256": "a" * 64,
            "recommended_monitor_state": "observing",
            "monitor": {"alerts": []},
            "latency_ns": 100,
            "assets": [
                {
                    "symbol": "SPY",
                    "baseline_score": 0.5,
                    "reported_score": 0.6,
                    "candidate_score": 0.6,
                    "bounded_contribution": 0.1,
                }
            ],
        }

    monkeypatch.setattr(campaign, "infer_shadow", infer)
    path = tmp_path / "campaign.json"
    campaign.register_campaign(tmp_path / "artifact.json", tmp_path / "snapshot", output=path)
    clock[0] = dt.datetime(2026, 9, 30, 21, tzinfo=dt.UTC)
    return SimpleNamespace(
        path=path,
        panel=panel,
        baseline=baseline,
        clock=clock,
        calls=calls,
        ledger=tmp_path / "ledger.json",
        output=tmp_path / "observation.json",
        manifest=manifest,
    )


def poll(setup, **kwargs):
    return campaign.poll_campaign(
        setup.path,
        through=setup.panel.sessions[2],
        ledger=setup.ledger,
        output=setup.output,
        client=object(),
        **kwargs,
    )


def test_prospective_receipt_idempotence_and_publication_recovery(setup):
    record = poll(setup)
    assert record["report"]["evidence_level"] == "prospective_shadow_observation"
    assert record["report"]["multi_timeframe"] == {"fixture": True}
    receipt = campaign._verify_receipt(setup.output.with_suffix(".receipt.json"))
    assert receipt["panel"]["sessions"][-1] == "2026-09-28"
    setup.output.unlink()
    assert poll(setup) == record
    assert len(setup.calls) == 1
    assert json.loads(setup.output.read_text()) == record


@pytest.mark.parametrize("registered", ["2026-09-28T13:30:00+00:00", "2026-09-28T16:00:00+00:00"])
def test_mid_session_or_exact_open_registration_is_backfill(setup, registered):
    envelope = json.loads(setup.path.read_text())
    envelope["payload"]["registered_at"] = registered
    setup.path.write_text(json.dumps(campaign._envelope(envelope["payload"])))
    assert poll(setup)["report"]["evidence_level"] == "backfill_shadow_diagnostic"


def test_campaign_and_receipt_tampering_refused(setup):
    record = poll(setup)
    receipt = setup.output.with_suffix(".receipt.json")
    receipt.write_text(receipt.read_text() + " ")
    with pytest.raises(ValueError, match="receipt changed"):
        poll(setup)
    assert json.loads(setup.output.read_text()) == record


def test_changed_baseline_refuses_even_idempotent_retry(setup):
    poll(setup)
    setup.baseline.adjusted_opens[0, 0] += 1
    with pytest.raises(ValueError, match="baseline snapshot changed"):
        poll(setup)


def test_receipt_survives_interrupted_ledger_publication(setup, monkeypatch):
    original = campaign.record_shadow_observation
    monkeypatch.setattr(
        campaign,
        "record_shadow_observation",
        lambda *_: (_ for _ in ()).throw(RuntimeError("crash")),
    )
    with pytest.raises(RuntimeError, match="crash"):
        poll(setup)
    assert not setup.ledger.exists()
    monkeypatch.setattr(campaign, "record_shadow_observation", original)
    assert poll(setup)["report"]["evidence_level"] == "prospective_shadow_observation"
    assert len(setup.calls) == 1


def test_changed_prior_receipt_history_is_rejected(setup):
    poll(setup)
    setup.panel.adjusted_opens[2, 0] += 1
    with pytest.raises(ValueError, match="previously received"):
        campaign.poll_campaign(
            setup.path,
            through=setup.panel.sessions[3],
            ledger=setup.ledger,
            output=setup.output.with_name("next.json"),
            client=object(),
        )


def test_path_collision_and_campaign_checksum_refused(setup):
    with pytest.raises(ValueError, match="distinct"):
        campaign.poll_campaign(
            setup.path, through=setup.panel.sessions[2], ledger=setup.path, output=setup.output
        )
    value = json.loads(setup.path.read_text())
    value["payload"]["registered_at"] = "2020-01-01T00:00:00+00:00"
    setup.path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="checksum"):
        poll(setup)


def test_latched_suspension_neutralizes_next_report(setup, monkeypatch):
    infer = campaign.infer_shadow

    def suspended(*args):
        report = infer(*args)
        report["recommended_monitor_state"] = "suspended"
        report["assets"][0]["reported_score"] = 0.5
        report["assets"][0]["bounded_contribution"] = 0
        return report

    monkeypatch.setattr(campaign, "infer_shadow", suspended)
    poll(setup)
    monkeypatch.setattr(campaign, "infer_shadow", infer)
    record = campaign.poll_campaign(
        setup.path,
        through=setup.panel.sessions[3],
        ledger=setup.ledger,
        output=setup.output.with_name("next.json"),
        client=object(),
    )
    assert record["effective_monitor_state"] == "suspended"
    assert record["report"]["assets"][0]["bounded_contribution"] == 0


def test_intraday_inputs_are_retained_and_cut_off_at_session_close(setup, monkeypatch, tmp_path):
    envelope = json.loads(setup.path.read_text())
    envelope["payload"]["intraday_reference"] = {
        "universe": ["SPY"],
        "calibration_end": "2026-09-25",
    }
    setup.path.write_text(json.dumps(campaign._envelope(envelope["payload"])))
    minute_input = tmp_path / "minutes.json"
    minute_input.write_text(json.dumps({"fixture": "original minute data"}))
    monkeypatch.setattr(
        campaign, "load_intraday", lambda _: {"collected_at": "2026-09-28T21:00:00+00:00"}
    )
    calls = []

    def report(_pattern, _reference, path, *, as_of):
        calls.append((json.loads(path.read_text()), as_of))
        return {"views": {"15m": {"status": "diagnostic"}, "1h": {"status": "diagnostic"}}}

    monkeypatch.setattr(campaign, "intraday_timeframe_report", report)
    record = poll(setup, intraday_data=minute_input)
    assert calls[0][1] == dt.datetime(2026, 9, 28, 20, tzinfo=dt.UTC)
    receipt = campaign._verify_receipt(setup.output.with_suffix(".receipt.json"))
    assert receipt["intraday_input"] == {"fixture": "original minute data"}
    minute_input.unlink()
    assert poll(setup) == record
    assert len(calls) == 1


def test_intraday_source_requires_pre_registered_reference(setup, tmp_path):
    with pytest.raises(ValueError, match="frozen at registration"):
        poll(setup, intraday_data=tmp_path / "minutes.json")
    assert not setup.calls


@pytest.mark.parametrize("revised", [False, True])
def test_intraday_prefix_parity_survives_missing_intermediate_source(setup, monkeypatch, revised):
    old = {"version": 1, "collected_at": "2026-09-28T21:00:00+00:00"}
    current = {"version": 2 if revised else 1, "collected_at": "2026-09-30T20:30:00+00:00"}
    receipts = {"old": {"intraday_input": {"payload": old}}, "missing": {"intraday_input": None}}
    monkeypatch.setattr(campaign, "_verify_receipt", lambda path, _: receipts[path.name])
    seen = []

    def aggregate(payload, timeframe, *, as_of):
        seen.append((timeframe, as_of))
        panel = _prefix(setup.panel, setup.panel.sessions[2])
        panel.adjusted_opens[-1, 0] = payload["version"]
        return panel

    monkeypatch.setattr(campaign, "aggregate_intraday", aggregate)
    records = [
        {"report": {"receipt_path": path, "receipt_sha256": "fixture", "session_close": stamp}}
        for path, stamp in (
            ("old", "2026-09-28T20:00:00+00:00"),
            ("missing", "2026-09-29T20:00:00+00:00"),
        )
    ]

    def verify():
        campaign._verify_intraday_history(
            current, records, received=setup.clock[0], reference={"calibration_end": "2026-09-25"}
        )

    if revised:
        with pytest.raises(ValueError, match="previously observed intraday"):
            verify()
    else:
        verify()
        assert {timeframe for timeframe, _ in seen} == {"15m", "1h"}
    assert all(as_of == dt.datetime(2026, 9, 28, 20, tzinfo=dt.UTC) for _, as_of in seen)


@pytest.mark.parametrize(
    "collected", ["2026-09-30T22:00:00+00:00", "2026-09-24T20:00:00+00:00", "2026-09-28T21:00:00"]
)
def test_intraday_collection_time_must_fit_receipt_and_calibration(setup, collected):
    with pytest.raises(ValueError, match="collection timestamp"):
        campaign._verify_intraday_history(
            {"collected_at": collected},
            [],
            received=setup.clock[0],
            reference={"calibration_end": "2026-09-25"},
        )
