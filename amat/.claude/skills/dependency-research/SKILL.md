---
name: dependency-research
description: Research a current library, API, data provider, paper, or Claude Code behavior from primary sources for an AMAT implementation decision.
argument-hint: "<specific technical question>"
disable-model-invocation: true
context: fork
agent: dependency-researcher
background: false
effort: high
---

Research: `$ARGUMENTS`

Inspect installed/locked versions first. Use primary sources: official docs, source, changelogs, standards, or original papers. Record version/commit and access date. Separate fact, inference, and recommendation.

Return:

1. direct implementation decision;
2. evidence table with supported claims;
3. version and compatibility constraints;
4. a minimal verified example or interface sketch;
5. security, correctness, and reproducibility risks;
6. local tests needed before adoption.

Do not edit files or rely on third-party summaries when a primary source is available.
