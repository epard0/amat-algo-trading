"""Unsupervised regime mixture: scoring, blending, and the safety invariants."""

from __future__ import annotations

import json
import math

import pytest

from core.strategy_brain.strategies.regime_model import (
    RegimeMixture,
    RegimeModelError,
    load_optional,
)

FEATURES = ("vol_ratio", "efficiency_ratio")


def artifact(**overrides) -> dict:
    """Two well-separated states along the first feature axis."""
    payload = {
        "schema_version": 1,
        "feature_names": list(FEATURES),
        "feature_means": [0.0, 0.0],
        "feature_scales": [1.0, 1.0],
        "states": [
            {
                "name": "CALM",
                "weight": 0.5,
                "means": [-2.0, 0.0],
                "variances": [0.25, 1.0],
                "drift_multiplier": 1.0,
                "momentum_half_life_minutes": 6.0,
                "volatility_multiplier": 0.9,
                "extra_shrinkage": 0.0,
            },
            {
                "name": "WILD",
                "weight": 0.5,
                "means": [2.0, 0.0],
                "variances": [0.25, 1.0],
                "drift_multiplier": 0.2,
                "momentum_half_life_minutes": 2.0,
                "volatility_multiplier": 1.4,
                "extra_shrinkage": 0.30,
            },
        ],
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------ scoring --

def test_posterior_sums_to_one() -> None:
    mixture = RegimeMixture.from_dict(artifact())
    posterior = mixture.posterior({"vol_ratio": 0.4, "efficiency_ratio": 0.0})

    assert math.isclose(sum(posterior.values()), 1.0, rel_tol=1e-9)


def test_posterior_favours_the_nearer_state() -> None:
    mixture = RegimeMixture.from_dict(artifact())

    calm = mixture.posterior({"vol_ratio": -2.0, "efficiency_ratio": 0.0})
    wild = mixture.posterior({"vol_ratio": 2.0, "efficiency_ratio": 0.0})

    assert calm["CALM"] > 0.99
    assert wild["WILD"] > 0.99


def test_ambiguous_point_splits_the_posterior() -> None:
    """A bar exactly between two states must not be assigned to either.

    This is the whole reason for a soft posterior: the hand classifier would
    pick one label and apply its multipliers at full strength.
    """
    mixture = RegimeMixture.from_dict(artifact())
    posterior = mixture.posterior({"vol_ratio": 0.0, "efficiency_ratio": 0.0})

    assert math.isclose(posterior["CALM"], 0.5, abs_tol=1e-9)
    assert math.isclose(posterior["WILD"], 0.5, abs_tol=1e-9)


def test_blend_is_a_convex_combination() -> None:
    mixture = RegimeMixture.from_dict(artifact())
    blend = mixture.blend({"vol_ratio": 0.0, "efficiency_ratio": 0.0})

    # Halfway between drift 1.0 and 0.2.
    assert math.isclose(blend.drift_multiplier, 0.6, abs_tol=1e-9)
    assert math.isclose(blend.volatility_multiplier, 1.15, abs_tol=1e-9)
    assert math.isclose(blend.extra_shrinkage, 0.15, abs_tol=1e-9)
    assert blend.confidence == pytest.approx(0.5)


def test_blend_at_a_pure_state_reproduces_that_state() -> None:
    mixture = RegimeMixture.from_dict(artifact())
    blend = mixture.blend({"vol_ratio": -2.0, "efficiency_ratio": 0.0})

    assert blend.dominant_state == "CALM"
    assert blend.drift_multiplier == pytest.approx(1.0, abs=1e-3)


def test_missing_feature_falls_back_to_the_training_mean() -> None:
    """An absent feature must read as uninformative, not as an extreme.

    Substituting 0.0 after standardisation would be a real value; substituting
    the mean is the only choice that adds no information.
    """
    mixture = RegimeMixture.from_dict(
        artifact(feature_means=[5.0, 0.0], feature_scales=[1.0, 1.0])
    )
    absent = mixture.vectorise({"efficiency_ratio": 0.0})

    assert absent[0] == 0.0   # (mean - mean) / scale


def test_non_finite_feature_is_treated_as_missing() -> None:
    mixture = RegimeMixture.from_dict(artifact())
    vector = mixture.vectorise({"vol_ratio": float("nan"), "efficiency_ratio": 1.0})

    assert vector[0] == 0.0
    assert math.isfinite(vector[1])


def test_far_outlier_still_produces_a_valid_posterior() -> None:
    """Log-sum-exp must not underflow to zero for an extreme point."""
    mixture = RegimeMixture.from_dict(artifact())
    posterior = mixture.posterior({"vol_ratio": 1e6, "efficiency_ratio": 0.0})

    assert math.isclose(sum(posterior.values()), 1.0, rel_tol=1e-9)
    assert all(math.isfinite(v) for v in posterior.values())


# --------------------------------------------------------------- invariants --

def test_negative_drift_multiplier_is_refused() -> None:
    """A regime response may damp measured drift but never invert it.

    Enforced at load time rather than by a penalty: a sign flip is a second,
    untested strategy hiding inside the first.
    """
    payload = artifact()
    payload["states"][0]["drift_multiplier"] = -0.5

    with pytest.raises(RegimeModelError, match="never invert"):
        RegimeMixture.from_dict(payload)


def test_blended_drift_can_never_go_negative() -> None:
    """Any convex blend of non-negative multipliers stays non-negative."""
    mixture = RegimeMixture.from_dict(artifact())
    for x in (-10.0, -1.0, 0.0, 1.0, 10.0):
        blend = mixture.blend({"vol_ratio": x, "efficiency_ratio": 0.0})
        assert blend.drift_multiplier >= 0.0
        assert blend.volatility_multiplier > 0.0
        assert 0.0 <= blend.extra_shrinkage <= 1.0


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"states": []}, "no states"),
        ({"feature_names": []}, "no features"),
        ({"feature_scales": [0.0, 1.0]}, "positive"),
        ({"schema_version": 99}, "schema_version"),
        ({"feature_means": [0.0]}, "do not match"),
    ],
)
def test_malformed_artifacts_are_refused(mutation: dict, message: str) -> None:
    with pytest.raises(RegimeModelError, match=message):
        RegimeMixture.from_dict(artifact(**mutation))


def test_non_positive_variance_is_refused() -> None:
    payload = artifact()
    payload["states"][0]["variances"] = [0.0, 1.0]

    with pytest.raises(RegimeModelError, match="variance"):
        RegimeMixture.from_dict(payload)


def test_shrinkage_outside_unit_interval_is_refused() -> None:
    payload = artifact()
    payload["states"][1]["extra_shrinkage"] = 1.5

    with pytest.raises(RegimeModelError, match="extra_shrinkage"):
        RegimeMixture.from_dict(payload)


def test_state_width_must_match_feature_names() -> None:
    payload = artifact()
    payload["states"][0]["means"] = [0.0]

    with pytest.raises(RegimeModelError, match="wrong width"):
        RegimeMixture.from_dict(payload)


# ------------------------------------------------------------------ loading --

def test_load_optional_returns_none_when_unset_or_absent(tmp_path) -> None:
    assert load_optional(None) is None
    assert load_optional("") is None
    assert load_optional(tmp_path / "nope.json") is None


def test_load_optional_reads_a_valid_artifact(tmp_path) -> None:
    path = tmp_path / "mixture.json"
    path.write_text(json.dumps(artifact()), encoding="utf-8")

    mixture = load_optional(path)

    assert mixture is not None
    assert len(mixture.states) == 2


def test_corrupt_artifact_raises_rather_than_silently_degrading(tmp_path) -> None:
    path = tmp_path / "mixture.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(RegimeModelError):
        load_optional(path)


# --------------------------------------------------------------- integration --

def test_predictor_without_a_mixture_is_unchanged() -> None:
    """The default path must not touch the hand-tuned classifier."""
    from core.strategy_brain.strategies.crypto_15min_predictor import (
        Crypto15MinutePredictor,
    )

    assert Crypto15MinutePredictor().regime_mixture is None


def test_predictor_blend_falls_back_when_the_artifact_misbehaves() -> None:
    """A model artifact is an optimisation; it must never stop the bot pricing."""
    from core.strategy_brain.strategies.crypto_15min_predictor import (
        Crypto15MinutePredictor,
    )
    from core.strategy_brain.strategies.market_regime import RegimeAssessment

    class Exploding:
        def blend(self, _features):
            raise RuntimeError("artifact is broken")

    predictor = Crypto15MinutePredictor()
    predictor.regime_mixture = Exploding()  # type: ignore[assignment]
    original = RegimeAssessment(
        regime="TREND",
        confidence=0.9,
        drift_multiplier=1.0,
        momentum_half_life_minutes=6.0,
        volatility_multiplier=1.0,
        extra_shrinkage=0.0,
        reasons=("hand",),
        features=None,  # type: ignore[arg-type]
    )

    assert predictor._blend_with_mixture(original) is original


# --------------------------------------------------------------------------
# Per-asset artifacts
# --------------------------------------------------------------------------


def _artifact(drifts: list[float]) -> dict:
    """A minimal valid artifact with one state per requested drift multiplier."""
    return {
        "schema_version": 1,
        "feature_names": ["efficiency_ratio", "vol_ratio"],
        "feature_means": [0.5, 1.0],
        "feature_scales": [0.2, 0.3],
        "states": [
            {
                "name": f"S{i}",
                "weight": 1.0 / len(drifts),
                "means": [0.0, 0.0],
                "variances": [1.0, 1.0],
                "drift_multiplier": d,
                "momentum_half_life_minutes": 3.0,
                "volatility_multiplier": 1.0,
                "extra_shrinkage": 0.05,
            }
            for i, d in enumerate(drifts)
        ],
        "metadata": {"independent_samples": 400},
    }


def test_a_directory_yields_one_mixture_per_asset(tmp_path) -> None:
    from core.strategy_brain.strategies.regime_model import load_for_assets

    for asset in ("BTC", "ETH"):
        (tmp_path / f"regime_mixture_{asset}.json").write_text(
            json.dumps(_artifact([0.4, 0.9]))
        )
    loaded = load_for_assets(tmp_path, ["BTC", "ETH", "SOL"])
    assert set(loaded) == {"BTC", "ETH"}


def test_an_asset_without_an_artifact_is_absent_not_an_error(tmp_path) -> None:
    """A partial rollout is the state you want while shadowing one asset."""
    from core.strategy_brain.strategies.regime_model import load_for_assets

    (tmp_path / "regime_mixture_BTC.json").write_text(json.dumps(_artifact([0.4, 0.9])))
    loaded = load_for_assets(tmp_path, ["BTC", "DOGE"])
    assert "DOGE" not in loaded
    assert loaded["BTC"] is not None


def test_a_single_file_is_shared_by_every_asset(tmp_path) -> None:
    from core.strategy_brain.strategies.regime_model import load_for_assets

    path = tmp_path / "one.json"
    path.write_text(json.dumps(_artifact([0.4, 0.9])))
    loaded = load_for_assets(path, ["BTC", "ETH"])
    assert loaded["BTC"] is loaded["ETH"]


def test_no_path_means_no_mixtures(tmp_path) -> None:
    from core.strategy_brain.strategies.regime_model import load_for_assets

    assert load_for_assets(None, ["BTC"]) == {}


def test_a_broken_artifact_raises_rather_than_falling_back(tmp_path) -> None:
    """Pointing at a file is a statement that you meant to use it."""
    from core.strategy_brain.strategies.regime_model import (
        RegimeModelError,
        load_for_assets,
    )

    bad = _artifact([0.4])
    bad["states"][0]["drift_multiplier"] = -1.0
    (tmp_path / "regime_mixture_BTC.json").write_text(json.dumps(bad))
    with pytest.raises(RegimeModelError):
        load_for_assets(tmp_path, ["BTC"])
