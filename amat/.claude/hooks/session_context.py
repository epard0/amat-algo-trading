from __future__ import annotations

from hooklib import PROJECT_ROOT, emit_context, read_event, run


def git_state() -> str:
    branch = run(["git", "branch", "--show-current"], timeout=5)
    status = run(["git", "status", "--porcelain"], timeout=5)
    branch_name = branch.stdout.strip() if branch.returncode == 0 else "no-git"
    dirty = "dirty" if status.returncode == 0 and status.stdout.strip() else "clean"
    return f"{branch_name or 'detached'} ({dirty})"


def main() -> None:
    read_event()
    context = f"""AMAT session context
- Repository: {PROJECT_ROOT}
- Git state: {git_state()}
- Mode: BACKTEST ONLY; AMAT_LIVE_TRADING_ENABLED=0
- Never submit paper or live broker orders, mutate raw/validated evidence, read secrets, or inspect a locked holdout outside protocol.
- Form signals from completed data and execute no earlier than the next permitted session window.
- Use .claude/references/strategy-contract.md, validation-protocol.md, and promotion-gates.md as normative evidence.
- Use specialized agents for bounded research/review and isolated worktrees for implementation.
- Before completion, run fresh applicable tests and report exact commands/results; unrun checks are not passes.
"""
    emit_context("SessionStart", context, sessionTitle="AMAT Backtest")


if __name__ == "__main__":
    main()
