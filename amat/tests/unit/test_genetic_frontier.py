"""A research frontier must not collapse distinct candle candidates as legacy scores."""

import datetime as dt
import json
from pathlib import Path

from amat.config.v6 import load_v6_config
from autoresearch.contracts import Candidate, Program
from autoresearch.genetic.adapter import GeneticAdapterSpec
from autoresearch.genetic.models import canonical_genome_digest, parse_pattern
from autoresearch.search import frontier

ROOT = Path(__file__).resolve().parents[2]


def test_frontier_preserves_distinct_candle_genomes(tmp_path):
    baseline = Candidate(config=load_v6_config(ROOT / "configs/strategy/amat_v6.toml").config)
    program = Program(
        schema_version="strategy-development/8",
        name="genetic-frontier",
        load_start=dt.date(2016, 1, 4),
        measured_start=dt.date(2018, 1, 2),
        train_end=dt.date(2020, 12, 31),
        validation_end=dt.date(2021, 12, 31),
        baseline=baseline,
        frozen_files={},
        dependencies={},
    )
    payload = json.loads((ROOT / "docs/genetic-patterns/candle.example.json").read_text())
    candidates = []
    rows = []
    (tmp_path / "candidates").mkdir()
    for op in ("gt", "lt"):
        payload["genome"] = {
            "kind": "candle_conjunction",
            "maxLag": 0,
            "rules": [
                {
                    "left": {"field": "close", "lag": 0},
                    "op": op,
                    "right": {"field": "open", "lag": 0},
                }
            ],
        }
        pattern = parse_pattern(payload)
        candidate = Candidate(
            config=baseline.config,
            genetic_pattern=GeneticAdapterSpec(
                pattern=pattern, genome_sha256=canonical_genome_digest(pattern)
            ),
        )
        candidates.append(candidate)
        (tmp_path / "candidates" / f"{candidate.digest()}.json").write_text(
            candidate.model_dump_json()
        )
        rows.append(
            {
                "candidate": candidate.digest(),
                "status": "discard",
                "result": json.dumps({"score": 0.1, "failed_gates": []}),
            }
        )
    assert frontier(tmp_path, program, rows) == candidates
