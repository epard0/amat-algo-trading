"""Check the full declared proposal domain, including inherited inactive fields."""

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "joint_correction", SCRIPTS / "nested_joint_correction.py"
)
correction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(correction)


def test_inherited_single_family_cannot_erase_joint_weights():
    inherited = [correction.study.baseline_recipe()]
    inherited += [
        correction.study.proposal(family, 0, 31)
        for family in correction.study.FAMILIES
        if family != "portfolio"
    ]
    for seed in range(100):
        broad = [correction.proposal(i, seed, joint_seeds=inherited) for i in range(12)]
        for recipe in broad:
            correction.validate(recipe)
        assert broad == [correction.proposal(i, seed, joint_seeds=inherited) for i in range(12)]


def test_local_mutation_keeps_two_distinct_active_features():
    for seed in range(100):
        parents = [correction.proposal(i, seed) for i in (0, 1)]
        for i in range(4):
            result = correction.proposal(12 + i, seed, parents=parents, progress=i / 3)
            correction.validate(result)


def test_scalar_cannot_masquerade_as_two_feature_configuration():
    import pytest

    recipe = correction.proposal(0, 31)
    with pytest.raises(ValueError, match="numeric domain"):
        correction.validate({**recipe, "mix": 1.0})
    with pytest.raises(ValueError, match="numeric domain"):
        correction.validate({**recipe, "effect": 0.0})
    with pytest.raises(ValueError, match="distinct active"):
        correction.validate({**recipe, "feature_b": recipe["feature_a"]})
