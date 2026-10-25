"""One strategy preference for guided actions; explicit historical choices remain available."""

from pathlib import Path

CURRENT_BEST = "amat_group_selection_volatility.toml"


def default_strategy(root: Path) -> str:
    directory = root / "amat/configs/strategy"
    available = {p.name for p in directory.glob("*.toml") if p.is_file() and not p.is_symlink()}
    return next(
        (
            name
            for name in [
                CURRENT_BEST,
                "amat_group_selection_daily.toml",
                "amat_v13_return_target.toml",
                "amat_v6.toml",
                *sorted(available),
            ]
            if name in available
        ),
        "",
    )


def default_snapshot(snapshots: list[str]) -> str:
    core = "alpaca-sip-core-2026-10-13-verified"
    return core if core in snapshots else snapshots[-1] if snapshots else ""
