# Contributing to KX15

Thank you for improving the project. KX15 includes a real-money order path, so a
small-looking change to time, units, rounding, retries, or state can have a large
operational effect. Contributions should make the behavior easier to reproduce
and audit, not only make a test pass.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing module
boundaries and [docs/DEVELOPING.md](docs/DEVELOPING.md) before changing a model,
risk gate, asset, dashboard, or release path.

## Repository publication prerequisite

At the 2026-08-27 audit, this local repository had no commit, branch, or remote,
and `.gitignore` excluded several local packaging/source directories. A
maintainer must publish a canonical repository and decide which of those local
subsystems are in scope before the clone commands below can use a concrete URL.

The maintainer's initial publication should, at minimum:

1. review all untracked files and exclude credentials, `.env`, PEM keys,
   ledgers, JSONL logs, customer/storefront state, reports, and archives;
2. choose which source directories belong in the public repository;
3. add an appropriate `LICENSE` file;
4. create the initial `main` commit and configure `origin`;
5. verify a clean clone in a new directory, including tests and a paper scan.

Do not use `git add -A` on the current workspace without that review.

## Development setup

Requirements:

- Git
- Python 3.13+
- `uv` (recommended)
- no live credentials for normal development

Fork the canonical project in GitHub, then clone your fork:

```bash
git clone https://github.com/<YOUR-USER>/Polymarket-BTC-15-Minute-Trading-Bot.git
cd Polymarket-BTC-15-Minute-Trading-Bot
git remote -v
```

`origin` should point to your fork. Add the canonical project as `upstream` so
you can update your branch without changing where you push:

```bash
git remote add upstream \
  https://github.com/<PROJECT-OWNER>/Polymarket-BTC-15-Minute-Trading-Bot.git
git fetch upstream
```

Install the project and create a paper-safe environment:

```bash
uv sync
cp .env.example .env
uv run pytest -q
```

Keep `KALSHI_ENVIRONMENT=demo` and `ENABLE_LIVE_TRADING=false` for development.
The tests mock their network/order boundaries. A source scan with `--once` uses
public endpoints read-only but does write local paper/decision state.

## Create a branch

Start from an up-to-date `main` and use a descriptive branch name:

```bash
git switch main
git fetch upstream
git pull --ff-only upstream main
git switch -c docs/improve-contributor-guide
```

Common prefixes are `feat/`, `fix/`, `docs/`, `test/`, `refactor/`, and
`chore/`. Keep one coherent behavior change per branch.

If you are a maintainer working directly against the canonical repository,
replace `upstream` with `origin` in the update commands; still use a feature
branch rather than committing to `main`.

## Make the change safely

### Architecture rules

- Use `Decimal` for money, price, fees, balances, P&L, and contract quantities.
- Keep risk/model calculations pure where possible. Pass time and state in as
  arguments rather than reading globals.
- Treat `execution/trade_ledger.py` as the only writer of the operational
  database. Schema changes need idempotent migrations and upgrade tests.
- Finish every decision trace, including refused and error paths.
- Re-read book depth and indexed cash immediately before a money-bearing order.
- Never blindly retry an ambiguous order submission; reconcile by client ID.
- Continue servicing positions when discovery or an auxiliary feed is degraded.
- Keep monitoring, reporting, licensing, and offline fitting out of `bot.py`'s
  import graph.
- Keep Polymarket integrations read-only unless a separately reviewed execution
  architecture is explicitly proposed.
- Do not revive the legacy upstream tree for new runtime behavior. Port useful
  logic into the active tree with tests.

### Configuration changes

When adding a setting:

1. add the typed field/default and `from_env` parsing in `config.py`;
2. validate impossible or unsafe combinations at startup;
3. document it in `.env.example` with the same default;
4. add boundary/default tests;
5. state whether it is active, experimental, deprecated, or compatibility-only.

### Documentation changes

Update all affected entry points, not just an isolated paragraph:

- `README.md` for operator-visible behavior;
- `docs/ARCHITECTURE.md` for ownership/data flow/source layout;
- `.env.example` for configuration behavior;
- `docs/DEVELOPING.md` for contributor invariants;
- the relevant implementation plan when completing or changing a planned phase.

Mermaid diagrams must render on GitHub and should reflect actual import/data
flow. Avoid claims of profitability or “production-grade” status that are not
supported by current evidence.

## Verify the change

Run the smallest focused test during development, then the whole suite before
opening a pull request:

```bash
# Examples of focused suites.
uv run pytest tests/test_risk_gate.py -q
uv run pytest tests/test_monitoring.py tests/test_monitoring_mode_api.py -q
uv run pytest tests/test_dataset.py tests/test_replay_events.py -q

# Required before a normal PR.
uv run pytest -q
uv run ruff check bot.py config.py core/strategy_brain/strategies execution \
  data_sources/kraken data_sources/yahoo data_sources/pyth \
  data_sources/polymarket data_sources/venues monitoring reporting research tests
```

The whole-workspace `uv run ruff check .` currently reports 48 pre-existing
findings confined to the legacy upstream tree/smoke scripts and generated
notebooks. Run it when changing those areas, identify the baseline in the PR,
and do not add to it. New or modified Python files should be clean.

For changes that touch relevant boundaries, also run:

```bash
# Import/CLI smoke checks.
uv run python bot.py --help
uv run python -m monitoring.server --help

# Paper-mode public scan (requires network; never places an order).
uv run python bot.py --once --assets BTC --strategy predictor

# Validate Compose syntax after deployment edits. Ensure the shell environment
# does not inject live credentials into captured output.
docker compose config --quiet
```

Do not test live placement from a contributor PR. A maintainer should use a
separate, explicit live-validation procedure with a bounded account and budget.

## Commit your work

Inspect the exact files first:

```bash
git status --short
git diff --check
git diff
```

Stage explicit source paths. This is particularly important in this project
because the workspace can contain private trading and storefront data:

```bash
git add README.md CONTRIBUTING.md docs/ARCHITECTURE.md
git commit -m "docs: describe the current architecture"
```

Suggested commit subjects are imperative and scoped, for example:

- `fix: preserve position supervision during discovery outage`
- `feat: export dataset freshness metrics`
- `docs: add observability recovery plan`
- `test: cover decision-log rotation`

## Push to `origin`

Push the new branch to your fork, not directly to `main`:

```bash
git push -u origin docs/improve-contributor-guide
```

The `-u` option records the upstream tracking branch, so later updates can use
plain `git push`.

If the branch has fallen behind while you worked:

```bash
git fetch upstream
git rebase upstream/main
uv run pytest -q
git push --force-with-lease
```

Use `--force-with-lease`, never an unqualified force push. Do not rewrite a
branch other contributors are using without coordinating with them.

## Open a pull request to `main`

On GitHub, open your fork, select the pushed branch, and choose **Compare & pull
request**. Set:

- **base repository:** the canonical project;
- **base branch:** `main`;
- **head repository:** your fork;
- **compare branch:** your feature branch.

If the GitHub CLI is installed, the equivalent is:

```bash
gh pr create \
  --base main \
  --head <YOUR-USER>:docs/improve-contributor-guide \
  --title "docs: describe the current architecture" \
  --body-file /path/to/pr-description.md
```

The pull request description should include:

1. the problem and user/operator impact;
2. the design and important tradeoffs;
3. execution, state, security, or migration risk;
4. test and manual-verification commands with results;
5. screenshots for UI/Grafana changes;
6. sample schema/metrics and compatibility notes for data changes;
7. rollback behavior for anything that can affect a running bot.

### Pull request checklist

- [ ] Paper mode remains the default.
- [ ] No credentials, keys, account IDs, ledgers, logs, or customer records are included.
- [ ] Money and quantities do not cross through binary floats.
- [ ] Order retries preserve idempotency and reconcile ambiguous outcomes.
- [ ] Position supervision still runs under degraded discovery/data conditions.
- [ ] New settings are parsed, validated, documented, and tested.
- [ ] Schema changes migrate an old fixture and preserve existing rows.
- [ ] Metrics use bounded-cardinality labels and have HELP/TYPE definitions.
- [ ] Documentation and diagrams match the implemented paths.
- [ ] Focused tests and the full test suite pass.
- [ ] The PR targets `main` and contains no unrelated generated artifacts.

## Review and merge

Respond to review with additional commits while discussion is active. Resolve a
thread only when the concern is addressed or the reviewer agrees with the
decision. The maintainer may squash on merge; preserve useful rationale in the
PR description or source comments rather than depending on temporary commits.

After merge, update your fork and delete the feature branch:

```bash
git switch main
git fetch upstream
git merge --ff-only upstream/main
git push origin main
git branch -d docs/improve-contributor-guide
git push origin --delete docs/improve-contributor-guide
```

## Reporting security issues

Do not open a public issue containing credentials, private keys, exploit details
against a live deployment, or account/trading history. Use the repository's
private security-reporting channel once the maintainer publishes one. Until then,
contact the maintainer privately and provide only the minimum reproduction data.
