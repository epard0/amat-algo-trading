"""Configuration loading and content addressing.

Loading a configuration returns both the validated model and the SHA-256 digest
of the exact bytes that produced it. The digest goes into the run manifest, so a
report can be traced to the literal configuration text rather than to a file
name that may since have changed.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from amat.config.models import StrategyConfig
from amat.errors import ConfigurationError

__all__ = ["ResolvedConfig", "load_strategy_config", "parse_strategy_config"]


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    """A validated configuration plus the provenance of its source bytes.

    Attributes:
        strategy: The validated strategy definition.
        source_path: File the bytes came from, or ``None`` for in-memory text.
        content_sha256: Digest of the exact source bytes.
    """

    strategy: StrategyConfig
    source_path: Path | None
    content_sha256: str


def parse_strategy_config(text: str, *, source_path: Path | None = None) -> ResolvedConfig:
    """Parse and validate TOML configuration text.

    Args:
        text: TOML document.
        source_path: Optional path recorded for provenance.

    Returns:
        The validated configuration and its content digest.

    Raises:
        ConfigurationError: if the document is not valid TOML or violates the
            strategy contract. Unknown keys are errors, not warnings.
    """
    raw = text.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    where = f" in {source_path}" if source_path is not None else ""

    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        message = f"invalid TOML{where}: {exc}"
        raise ConfigurationError(message) from exc

    try:
        strategy = StrategyConfig.model_validate(document)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()
        )
        message = f"invalid strategy configuration{where}: {details}"
        raise ConfigurationError(message) from exc

    return ResolvedConfig(strategy=strategy, source_path=source_path, content_sha256=digest)


def load_strategy_config(path: Path | str) -> ResolvedConfig:
    """Load and validate a strategy configuration file.

    Args:
        path: Path to a TOML strategy configuration.

    Returns:
        The validated configuration and its content digest.

    Raises:
        ConfigurationError: if the file is missing, unreadable, not valid UTF-8,
            or violates the strategy contract.
    """
    resolved = Path(path).expanduser()
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        message = f"strategy configuration not found: {resolved}"
        raise ConfigurationError(message) from exc
    except (OSError, UnicodeDecodeError) as exc:
        message = f"cannot read strategy configuration {resolved}: {exc}"
        raise ConfigurationError(message) from exc
    return parse_strategy_config(text, source_path=resolved)
