"""Provider-neutral LLM routing: ordered fallback chains with per-provider health.

Stdlib plus the pinned provider SDKs. Never imports executor, subprocess, or an
HTTP client of its own; every network call goes through an official SDK with
retries disabled so failover stays observable and bounded here.
"""
