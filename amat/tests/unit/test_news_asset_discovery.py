from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from amat.config.loading import load_strategy_config
from amat.errors import ConfigurationError
from amat.news_assets import (
    NEWS_CLUSTER_CAP,
    effective_config_digest,
    expand_strategy_with_news_assets,
    load_news_asset_overlay,
)
from newsdesk.discovery import (
    DiscoveryParameters,
    carry_retired_candidates,
    discover_assets,
    load_themes,
)
from newsdesk.store import ArticleRecord, NewsStore

ROOT = Path(__file__).resolve().parents[2]
THEMES = ROOT / "configs" / "news" / "themes.toml"
STRATEGY = ROOT / "configs" / "strategy" / "amat_v1.toml"
AS_OF = dt.datetime(2026, 8, 24, 16, 0, tzinfo=dt.UTC)


def _store_story(
    store: NewsStore, *, title: str, sentiment: float | None, ingested_at: str
) -> None:
    provider = store.register_provider("testwire", "example.test")
    run = store.open_run(provider)
    store.store(
        [
            ArticleRecord(
                vendor_uuid="story-1",
                published_at="2026-08-24T14:00:00.000Z",
                title=title,
                url="https://example.test/story-1",
                snippet="The Department of Defense selected suppliers.",
                symbols={"SPY": (None, sentiment)},
            )
        ],
        provider_id=provider,
        run_id=run,
        ingested_at=ingested_at,
    )


def test_positive_defense_story_discovers_reviewed_companies(tmp_path: Path) -> None:
    with NewsStore(tmp_path / "news.sqlite3") as store:
        _store_story(
            store,
            title="Trump announces rocket deal for the military",
            sentiment=0.7,
            ingested_at="2026-08-24T15:00:00.000Z",
        )
        artifact = discover_assets(store, themes=load_themes(THEMES), as_of=AS_OF)

    symbols = {candidate["symbol"] for candidate in artifact["candidates"]}
    assert symbols == {"LMT", "RTX", "NOC", "GD", "LHX", "HII", "BA"}
    assert all(candidate["theme"] == "us_defense" for candidate in artifact["candidates"])
    assert all(candidate["sentiment"] == pytest.approx(0.7) for candidate in artifact["candidates"])


def test_story_not_yet_ingested_cannot_create_candidates(tmp_path: Path) -> None:
    with NewsStore(tmp_path / "news.sqlite3") as store:
        _store_story(
            store,
            title="Pentagon awards missile contract",
            sentiment=0.8,
            ingested_at="2026-08-24T17:00:00.000Z",
        )
        artifact = discover_assets(store, themes=load_themes(THEMES), as_of=AS_OF)
    assert artifact["candidates"] == []


def test_lexical_fallback_rejects_negative_event(tmp_path: Path) -> None:
    with NewsStore(tmp_path / "news.sqlite3") as store:
        _store_story(
            store,
            title="Pentagon cancels delayed missile contract",
            sentiment=None,
            ingested_at="2026-08-24T15:00:00.000Z",
        )
        artifact = discover_assets(
            store,
            themes=load_themes(THEMES),
            as_of=AS_OF,
            parameters=DiscoveryParameters(min_sentiment=0.15),
        )
    assert artifact["candidates"] == []


def _write_artifact(path: Path, *, expires_at: str = "2026-08-25T22:00:00+00:00") -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "generated_at": "2026-08-24T16:00:00+00:00",
                "as_of": "2026-08-24T16:00:00+00:00",
                "expires_at": expires_at,
                "taxonomy_sha256": "a" * 64,
                "parameters": {},
                "candidates": [
                    {
                        "symbol": "LMT",
                        "theme": "us_defense",
                        "sentiment": 0.7,
                        "article_count": 1,
                        "evidence": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_artifact_expands_a_separately_capped_cluster_and_changes_digest(
    tmp_path: Path,
) -> None:
    path = tmp_path / "assets.json"
    _write_artifact(path)
    base = load_strategy_config(STRATEGY)
    overlay = load_news_asset_overlay(path, now=AS_OF, base_symbols=base.strategy.universe.symbols)
    expanded = expand_strategy_with_news_assets(base.strategy, overlay)

    cluster = expanded.universe.clusters[-1]
    assert cluster.name == "news_sentiment"
    assert cluster.gross_cap == NEWS_CLUSTER_CAP
    assert cluster.symbols == ("LMT",)
    assert effective_config_digest(base.content_sha256, overlay) != base.content_sha256


def test_expired_artifact_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "assets.json"
    _write_artifact(path, expires_at="2026-08-24T15:59:59+00:00")
    with pytest.raises(ConfigurationError, match="expired"):
        load_news_asset_overlay(path, now=AS_OF)


def test_previous_candidate_is_carried_as_exit_only(tmp_path: Path) -> None:
    themes = load_themes(THEMES)
    current = {
        "candidates": [],
    }
    previous = {
        "candidates": [
            {
                "symbol": "LMT",
                "theme": "us_defense",
                "sentiment": 0.7,
                "article_count": 1,
                "evidence": [],
                "active": True,
            }
        ]
    }
    carried = carry_retired_candidates(current, previous, themes=themes, as_of=AS_OF)
    assert carried["candidates"][0]["symbol"] == "LMT"
    assert carried["candidates"][0]["active"] is False

    path = tmp_path / "assets.json"
    _write_artifact(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["candidates"][0]["active"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    overlay = load_news_asset_overlay(path, now=AS_OF)
    assert overlay.retired_symbols == ("LMT",)
    assert overlay.active_symbols == ()
