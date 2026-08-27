from dataclasses import dataclass
from decimal import Decimal

from execution.provenance import (
    canonical_config_json,
    config_fingerprint,
    model_fingerprint,
    source_revision,
)


@dataclass
class Settings:
    api_key_id: str
    private_key_path: str
    trade_budget_usd: Decimal
    assets: tuple[str, ...]
    regime_model_path: str = ""
    commodity_model_path: str = ""


def test_configuration_hash_is_stable_and_ignores_secrets() -> None:
    first = Settings("secret-a", "/secret/a.pem", Decimal("2"), ("BTC",))
    second = Settings("secret-b", "/secret/b.pem", Decimal("2"), ("BTC",))
    changed = Settings("secret-b", "/secret/b.pem", Decimal("3"), ("BTC",))

    assert config_fingerprint(first) == config_fingerprint(second)
    assert config_fingerprint(first) != config_fingerprint(changed)
    public = canonical_config_json(first)
    assert "secret-a" not in public
    assert "/secret/a.pem" not in public
    assert '"trade_budget_usd":"2"' in public


def test_source_and_model_revisions_are_nonempty() -> None:
    settings = Settings("x", "x", Decimal("2"), ("BTC",))
    assert ":" in source_revision()
    assert model_fingerprint(settings).startswith("builtin:")


def test_model_fingerprint_includes_directional_artifact(tmp_path) -> None:
    artifact = tmp_path / "directional.json"
    artifact.write_text('{"version": 1}')
    settings = Settings(
        "x", "x", Decimal("2"), ("HYPE",), commodity_model_path=str(artifact)
    )
    first = model_fingerprint(settings)
    artifact.write_text('{"version": 2}')
    second = model_fingerprint(settings)
    assert first.startswith("bundle:")
    assert first != second
