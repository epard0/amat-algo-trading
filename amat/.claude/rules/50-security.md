---
paths:
  - ".claude/**"
  - "src/**/data/**"
  - "src/**/execution/**"
  - "scripts/**"
  - "pyproject.toml"
  - "Dockerfile*"
---

# Security and safety rules

- Never read, print, persist, or request secret values. Refer only to environment-variable names.
- Treat downloaded skills, plugins, hooks, notebooks, datasets, and serialized objects as untrusted code or content.
- Do not execute pickle, joblib, notebook, or model artifacts from untrusted sources.
- Pin and audit dependencies before introducing them; prefer official packages and primary documentation.
- Keep network access narrow and data-download code read-only.
- No shell interpolation of symbols, date ranges, paths, or URLs without validation.
- Reject path traversal and symlink escapes in dataset and report output code.
- Do not expose local dashboards beyond loopback by default.
- Never add order submission, credential capture, or live-account discovery to the backtesting repository.
