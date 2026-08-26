#!/usr/bin/env bash
set -uo pipefail

MODE="${1:-fast}"
if [[ "$MODE" != "fast" && "$MODE" != "full" ]]; then
  echo "usage: $0 [fast|full]" >&2
  exit 2
fi

CLAUDE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$CLAUDE_DIR/.." && pwd)"
cd "$ROOT"

PASS=0
FAIL=0
SKIP=0

run_check() {
  local label="$1"
  shift
  printf '\n==> %s\n' "$label"
  printf '+ '
  printf '%q ' "$@"
  printf '\n'
  if "$@"; then
    printf '[PASS] %s\n' "$label"
    PASS=$((PASS + 1))
  else
    local rc=$?
    printf '[FAIL] %s (exit %s)\n' "$label" "$rc" >&2
    FAIL=$((FAIL + 1))
  fi
}

skip_check() {
  printf '[SKIP] %s: %s\n' "$1" "$2"
  SKIP=$((SKIP + 1))
}

has_uv_tool() {
  command -v uv >/dev/null 2>&1 && uv run --no-sync "$1" --version >/dev/null 2>&1
}

# Resolve the interpreter once. A bare `python3` may be the system interpreter
# rather than the locked environment's, which makes the gate's verdict depend on
# whatever happens to be on PATH -- and some environments intercept bare
# `python3` entirely, turning a passing check into a spurious failure.
if command -v uv >/dev/null 2>&1; then
  PY=(uv run --no-sync python3)
elif [[ -x ./.venv/bin/python3 ]]; then
  PY=(./.venv/bin/python3)
else
  PY=(python3)
fi

run_check "Claude bundle validation" "${PY[@]}" .claude/scripts/validate_bundle.py
run_check "Repository secret scan" "${PY[@]}" .claude/scripts/scan_repository.py

if git rev-parse --git-dir >/dev/null 2>&1; then
  run_check "Git whitespace validation" git diff --check
else
  skip_check "Git whitespace validation" "not a git repository"
fi

PY_PATHS=()
[[ -d src ]] && PY_PATHS+=(src)
[[ -d tests ]] && PY_PATHS+=(tests)
if [[ ${#PY_PATHS[@]} -gt 0 ]]; then
  run_check "Python syntax compilation" "${PY[@]}" -m compileall -q "${PY_PATHS[@]}"
else
  skip_check "Python syntax compilation" "src/ and tests/ do not exist yet"
fi

if has_uv_tool ruff; then
  run_check "Ruff lint" uv run --no-sync ruff check .
  run_check "Ruff format check" uv run --no-sync ruff format --check .
elif command -v ruff >/dev/null 2>&1; then
  run_check "Ruff lint" ruff check .
  run_check "Ruff format check" ruff format --check .
else
  skip_check "Ruff" "not available in the locked environment"
fi

if has_uv_tool mypy; then
  if [[ -d src ]]; then
    run_check "Mypy" uv run --no-sync mypy src
  else
    skip_check "Mypy" "src/ does not exist yet"
  fi
elif command -v mypy >/dev/null 2>&1; then
  if [[ -d src ]]; then
    run_check "Mypy" mypy src
  else
    skip_check "Mypy" "src/ does not exist yet"
  fi
else
  skip_check "Mypy" "not available in the locked environment"
fi

PYTEST_CMD=()
if has_uv_tool pytest; then
  PYTEST_CMD=(uv run --no-sync pytest)
elif command -v pytest >/dev/null 2>&1; then
  PYTEST_CMD=(pytest)
fi

if [[ ${#PYTEST_CMD[@]} -eq 0 ]]; then
  skip_check "Pytest" "not available in the locked environment"
elif [[ ! -d tests ]]; then
  skip_check "Pytest" "tests/ does not exist yet"
elif [[ "$MODE" == "fast" && -d tests/unit ]]; then
  run_check "Pytest unit suite" "${PYTEST_CMD[@]}" -q --maxfail=1 tests/unit
elif [[ "$MODE" == "fast" ]]; then
  run_check "Pytest fast suite" "${PYTEST_CMD[@]}" -q --maxfail=1
else
  run_check "Pytest full suite" "${PYTEST_CMD[@]}" -q
fi

if [[ "$MODE" == "full" ]]; then
  if has_uv_tool pyright; then
    run_check "Pyright" uv run --no-sync pyright
  elif command -v pyright >/dev/null 2>&1; then
    run_check "Pyright" pyright
  else
    skip_check "Pyright" "not configured or unavailable"
  fi
fi

printf '\nQuality gate summary: %s passed, %s failed, %s skipped\n' "$PASS" "$FAIL" "$SKIP"
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
