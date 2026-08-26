---
paths:
  - "**/*.py"
  - "pyproject.toml"
  - "uv.lock"
---

# Python engineering rules

- Target the Python version declared by `pyproject.toml`; do not silently broaden compatibility.
- Use type hints for public functions, domain records, configuration, and boundaries.
- Keep domain logic pure where possible. Inject clocks, data sources, random generators, and execution models.
- Use `Decimal` or integer minor units where exact cash/fee arithmetic requires it; document deliberate float boundaries.
- Use timezone-aware datetimes. Store event timestamps in UTC and retain explicit exchange session dates.
- Prefer Polars lazy scans for large Parquet datasets; do not materialize an entire history without need.
- Avoid hidden module state, implicit global configuration, mutable default arguments, and nondeterministic iteration.
- Raise domain-specific errors with identifiers, dates, and expected invariants.
- Keep framework adapters thin; signal and portfolio formulas belong in shared framework-neutral modules.
- Format and lint with the repository's declared tools. Do not add a formatter, linter, or type checker merely to satisfy this rule.
