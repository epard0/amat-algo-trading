"""The advisory boundary: aggregate statistics out, narrative in, nothing else.

Three properties, each load-bearing:

* the payload is built only from report artifacts -- publishable by
  construction;
* every failure degrades to "no page, reason recorded" without raising;
* no failure message can carry the credential out.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.metrics import summarize
from amat.reporting import build_report
from amat.reporting.advisory import advisory_payload, request_advisory
from amat.reporting.insights import build_insights


@pytest.fixture(scope="module")
def bundle(canonical_config_path):
    strategy = load_strategy_config(canonical_config_path).strategy
    panel = generate_panel(strategy.to_universe(), sessions=300, seed=2, trend_strength=0.5)
    result = run_backtest(strategy, panel, strategy.to_universe(), initial_cash=Decimal("1200000"))
    report = build_report(
        result,
        strategy="amat-etf v1.0.0",
        config_sha256="0" * 64,
        data_source="synthetic",
        engine="v1-frozen",
    )
    return report, summarize(result), build_insights(result, panel)


class TestThePayload:
    def test_it_is_json_serializable_aggregates_only(self, bundle) -> None:
        report, metrics, insights = bundle
        payload = advisory_payload(report, metrics, insights)
        text = json.dumps(payload, default=str)
        assert set(payload) == {
            "strategy",
            "engine",
            "sample",
            "cost_model_one_way_bps",
            "performance",
            "annual_returns",
            "concentration",
            "per_asset",
            "sleeves",
            "episodes",
            "caveats",
        }
        # No environment variable VALUES can appear: the payload is built from
        # report fields alone. Guard the obvious accident anyway.
        assert "GEMINI" not in text

    def test_episode_detail_is_capped_but_counts_are_complete(self, bundle) -> None:
        """A decade of episodes exceeded the API's per-minute token quota in a
        single request; detail rows are capped and totals must survive."""
        import dataclasses

        report, metrics, insights = bundle
        template = (
            insights.whipsaws[0]
            if insights.whipsaws
            else insights.captured_trends[0]
            if insights.captured_trends
            else None
        )
        assert template is not None, "fixture must classify at least one episode"
        many = tuple(dataclasses.replace(template, net_pnl=-float(index)) for index in range(100))
        inflated = dataclasses.replace(insights, whipsaws=many)
        payload = advisory_payload(report, metrics, inflated)
        episodes = payload["episodes"]
        assert episodes["counts"]["whipsaws"] == 100
        assert len(episodes["worst_whipsaws"]) == 12
        assert all("note" not in row for row in episodes["worst_whipsaws"])


class TestDegradedOutcomes:
    def test_a_missing_key_means_no_request_and_a_recorded_reason(
        self, bundle, monkeypatch
    ) -> None:
        report, metrics, insights = bundle
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        outcome = request_advisory(advisory_payload(report, metrics, insights))
        assert not outcome.available
        assert outcome.failure is not None and "GEMINI_API_KEY is not set" in outcome.failure

    def test_an_sdk_failure_degrades_and_scrubs_the_credential(self, bundle, monkeypatch) -> None:
        report, metrics, insights = bundle
        sentinel = "test-credential-sentinel-000"
        monkeypatch.setenv("GEMINI_API_KEY", sentinel)

        import google.genai as genai_module

        def explode(*_args, **_kwargs):
            message = f"connection refused for key {sentinel}"
            raise RuntimeError(message)

        monkeypatch.setattr(genai_module, "Client", explode)
        outcome = request_advisory(
            advisory_payload(report, metrics, insights), attempts=2, retry_sleep_seconds=0.0
        )
        assert not outcome.available
        assert outcome.failure is not None
        assert sentinel not in outcome.failure
        assert "[REDACTED:GEMINI_API_KEY]" in outcome.failure

    def test_a_successful_response_carries_text_and_provenance(self, bundle, monkeypatch) -> None:
        report, metrics, insights = bundle
        monkeypatch.setenv("GEMINI_API_KEY", "test-credential-sentinel-000")

        class StubResponse:
            text = "Measured commentary."

        class StubModels:
            def generate_content(self, **_kwargs):
                return StubResponse()

        class StubClient:
            def __init__(self, *args, **kwargs) -> None:
                self.models = StubModels()

        import google.genai as genai_module

        monkeypatch.setattr(genai_module, "Client", StubClient)
        outcome = request_advisory(
            advisory_payload(report, metrics, insights), model="gemini-3.6-flash"
        )
        assert outcome.available
        assert outcome.text == "Measured commentary."
        assert "gemini-3.6-flash" in outcome.provenance

    def test_an_empty_response_is_a_recorded_failure_not_a_blank_page(
        self, bundle, monkeypatch
    ) -> None:
        report, metrics, insights = bundle
        monkeypatch.setenv("GEMINI_API_KEY", "test-credential-sentinel-000")

        class StubResponse:
            text = "   "

        class StubModels:
            def generate_content(self, **_kwargs):
                return StubResponse()

        class StubClient:
            def __init__(self, *args, **kwargs) -> None:
                self.models = StubModels()

        import google.genai as genai_module

        monkeypatch.setattr(genai_module, "Client", StubClient)
        outcome = request_advisory(
            advisory_payload(report, metrics, insights), attempts=1, retry_sleep_seconds=0.0
        )
        assert not outcome.available
        assert outcome.failure is not None and "empty response" in outcome.failure
