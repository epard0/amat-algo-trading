"""Validate news discovery artifacts and expand a strategy universe safely.

``amat`` never imports ``newsdesk`` and never reads its database.  The only
bridge is a short-lived JSON artifact whose bytes are content-addressed into the
effective plan digest.  Symbols in that artifact enter one dedicated cluster
with a hard-coded 10% gross cap; artifact content cannot raise the cap.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from amat.config.models import ClusterSpec, StrategyConfig, UniverseSpec
from amat.domain.types import parse_symbol
from amat.errors import ConfigurationError

__all__ = [
    "NEWS_CLUSTER_CAP",
    "NewsAssetCandidate",
    "NewsAssetOverlay",
    "effective_config_digest",
    "expand_strategy_with_news_assets",
    "load_news_asset_overlay",
]

NEWS_CLUSTER_CAP = 0.10
NEWS_CLUSTER_NAME = "news_sentiment"
MAX_NEWS_ASSETS = 8
MAX_TRACKED_NEWS_ASSETS = 64
MIN_NEWS_SENTIMENT = 0.15
_HASH_HEX_LENGTH = 64
_MAX_TTL_HOURS = 96


@dataclass(frozen=True, slots=True)
class NewsAssetCandidate:
    symbol: str
    theme: str
    sentiment: float
    article_count: int
    active: bool


@dataclass(frozen=True, slots=True)
class NewsAssetOverlay:
    candidates: tuple[NewsAssetCandidate, ...]
    source_sha256: str
    taxonomy_sha256: str
    as_of: dt.datetime
    expires_at: dt.datetime

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(candidate.symbol for candidate in self.candidates)

    @property
    def active_symbols(self) -> tuple[str, ...]:
        return tuple(candidate.symbol for candidate in self.candidates if candidate.active)

    @property
    def retired_symbols(self) -> tuple[str, ...]:
        return tuple(candidate.symbol for candidate in self.candidates if not candidate.active)


def _aware(value: Any, *, field: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigurationError(f"news asset artifact has invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ConfigurationError(f"news asset artifact {field} must carry a timezone")
    return parsed.astimezone(dt.UTC)


def _load_payload(source: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = source.read_bytes()
        decoded = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read news asset artifact {source}: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ConfigurationError("news asset artifact must be a JSON object")
    return raw, decoded


def _validate_window(
    payload: dict[str, Any], moment: dt.datetime
) -> tuple[dt.datetime, dt.datetime]:
    generated = _aware(payload.get("generated_at"), field="generated_at")
    as_of = _aware(payload.get("as_of"), field="as_of")
    expires = _aware(payload.get("expires_at"), field="expires_at")
    future_tolerance = dt.timedelta(minutes=5)
    if generated > moment + future_tolerance or as_of > moment + future_tolerance:
        raise ConfigurationError("news asset artifact claims knowledge from the future")
    if expires <= moment:
        raise ConfigurationError(
            f"news asset artifact expired at {expires.isoformat()}; regenerate it before planning"
        )
    if expires - generated > dt.timedelta(hours=_MAX_TTL_HOURS):
        raise ConfigurationError(
            f"news asset artifact lifetime exceeds the {_MAX_TTL_HOURS}-hour hard limit"
        )
    return as_of, expires


def _parse_candidate(row: Any, *, index: int) -> NewsAssetCandidate:
    if not isinstance(row, dict):
        raise ConfigurationError(f"news asset candidate {index} must be an object")
    try:
        symbol = str(parse_symbol(str(row["symbol"])))
        theme = str(row["theme"]).strip()
        sentiment = float(row["sentiment"])
        article_count = int(row["article_count"])
        active_raw = row.get("active", True)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"malformed news asset candidate {index}: {row!r}") from exc
    if not theme:
        raise ConfigurationError(f"news asset candidate {symbol} has no theme")
    if not math.isfinite(sentiment) or not MIN_NEWS_SENTIMENT <= sentiment <= 1.0:
        raise ConfigurationError(
            f"news asset candidate {symbol} sentiment {sentiment!r} is outside "
            f"[{MIN_NEWS_SENTIMENT}, 1]"
        )
    if article_count < 1:
        raise ConfigurationError(f"news asset candidate {symbol} has no supporting articles")
    if not isinstance(active_raw, bool):
        raise ConfigurationError(f"news asset candidate {symbol} active must be boolean")
    return NewsAssetCandidate(
        symbol=symbol,
        theme=theme,
        sentiment=sentiment,
        article_count=article_count,
        active=active_raw,
    )


def _parse_candidates(rows: Any, *, existing: set[str]) -> tuple[NewsAssetCandidate, ...]:
    if not isinstance(rows, list):
        raise ConfigurationError("news asset artifact candidates must be an array")
    if len(rows) > MAX_TRACKED_NEWS_ASSETS:
        raise ConfigurationError(
            f"news asset artifact tracks {len(rows)} candidates; hard limit is "
            f"{MAX_TRACKED_NEWS_ASSETS}"
        )
    candidates: list[NewsAssetCandidate] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        candidate = _parse_candidate(row, index=index)
        if candidate.symbol in seen:
            raise ConfigurationError(f"duplicate news asset candidate {candidate.symbol}")
        seen.add(candidate.symbol)
        if candidate.symbol not in existing:
            candidates.append(candidate)
    active_count = sum(candidate.active for candidate in candidates)
    if active_count > MAX_NEWS_ASSETS:
        raise ConfigurationError(
            f"news asset artifact has {active_count} active candidates; hard limit is "
            f"{MAX_NEWS_ASSETS}"
        )
    return tuple(candidates)


def load_news_asset_overlay(
    path: Path | str,
    *,
    now: dt.datetime | None = None,
    base_symbols: tuple[str, ...] = (),
) -> NewsAssetOverlay:
    """Read an untrusted discovery artifact, rejecting stale or malformed input."""
    raw, payload = _load_payload(Path(path))
    schema = str(payload.get("schema_version", ""))
    if schema.split(".", 1)[0] != "1":
        raise ConfigurationError(f"unsupported news asset artifact schema {schema!r}")

    moment = now or dt.datetime.now(tz=dt.UTC)
    if moment.tzinfo is None:
        raise ConfigurationError("news asset validation time must carry a timezone")
    moment = moment.astimezone(dt.UTC)
    as_of, expires = _validate_window(payload, moment)

    taxonomy_sha256 = str(payload.get("taxonomy_sha256", ""))
    if len(taxonomy_sha256) != _HASH_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in taxonomy_sha256
    ):
        raise ConfigurationError("news asset artifact has no valid taxonomy_sha256")
    existing = {str(parse_symbol(symbol)) for symbol in base_symbols}
    candidates = _parse_candidates(payload.get("candidates"), existing=existing)
    return NewsAssetOverlay(
        candidates=candidates,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        taxonomy_sha256=taxonomy_sha256,
        as_of=as_of,
        expires_at=expires,
    )


def expand_strategy_with_news_assets(
    strategy: StrategyConfig, overlay: NewsAssetOverlay
) -> StrategyConfig:
    """Return a fully revalidated strategy with one capped news cluster."""
    if not overlay.candidates:
        return strategy
    clusters = (
        *strategy.universe.clusters,
        ClusterSpec(
            name=NEWS_CLUSTER_NAME,
            gross_cap=NEWS_CLUSTER_CAP,
            symbols=overlay.symbols,
        ),
    )
    document = strategy.model_dump(mode="python")
    document["universe"] = UniverseSpec(clusters=clusters).model_dump(mode="python")
    return StrategyConfig.model_validate(document)


def effective_config_digest(base_sha256: str, overlay: NewsAssetOverlay | None) -> str:
    """Digest the base configuration and exact news artifact into one plan id."""
    if overlay is None:
        return base_sha256
    material = f"{base_sha256}\nnews-assets:{overlay.source_sha256}\n"
    return hashlib.sha256(material.encode("ascii")).hexdigest()
