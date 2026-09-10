"""Export pair convergence diagnostics from an immutable snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amat.config import load_strategy_config  # noqa: E402
from amat.data.snapshot import load_manifest, load_panel  # noqa: E402
from amat.stat_arb import PairSelectionConfig, scan_pairs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, default=None, help="Optional strategy universe subset."
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lookback", type=int, default=504)
    parser.add_argument("--minimum-correlation", type=float, default=0.50)
    parser.add_argument("--maximum-adf", type=float, default=-2.86)
    parser.add_argument("--maximum-half-life", type=float, default=63.0)
    args = parser.parse_args()

    manifest = load_manifest(args.snapshot)
    symbols = (
        load_strategy_config(args.config).strategy.universe.symbols
        if args.config is not None
        else manifest.symbols
    )
    panel, _ = load_panel(args.snapshot, symbols)
    rules = PairSelectionConfig(
        minimum_abs_return_correlation=args.minimum_correlation,
        maximum_adf_t_statistic=args.maximum_adf,
        maximum_half_life_sessions=args.maximum_half_life,
    )
    results = scan_pairs(
        panel.adjusted_closes,
        tuple(str(symbol) for symbol in panel.symbols),
        lookback_sessions=args.lookback,
        config=rules,
    )
    payload = {
        "schema_version": 1,
        "snapshot_id": manifest.snapshot_id,
        "last_session": panel.sessions[-1].isoformat(),
        "lookback_sessions": args.lookback,
        "pairs_tested": len(results),
        "eligible_by_descriptive_gates": sum(result.eligible for result in results),
        "warning": (
            "ADF threshold is an approximate descriptive screen, not a corrected p-value; "
            "no pair is authorized for trading by this artifact."
        ),
        "rules": {
            "minimum_abs_return_correlation": rules.minimum_abs_return_correlation,
            "maximum_adf_t_statistic": rules.maximum_adf_t_statistic,
            "minimum_half_life_sessions": rules.minimum_half_life_sessions,
            "maximum_half_life_sessions": rules.maximum_half_life_sessions,
            "minimum_zero_crossings": rules.minimum_zero_crossings,
            "require_positive_hedge_ratio": rules.require_positive_hedge_ratio,
        },
        "pairs": [result.as_dict() for result in results],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"tested {len(results)} pairs; {payload['eligible_by_descriptive_gates']} "
        "passed descriptive gates"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
