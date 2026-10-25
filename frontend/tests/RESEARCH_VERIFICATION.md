# Research workspace verification

Verified on 2026-10-18 (America/Detroit).

- Production TypeScript/Vite build passes.
- Frontend gateway and launcher suite: 39 passed.
- Preset regression covers all standard/training-2025 × historical/extended combinations, rejects unknown presets and out-of-bounds iterations, and confirms command construction creates no research workspace or process.
- Playwright against the local gateway: all eight desks navigable without uncaught browser errors; research mode configuration stays in Research; training-2025 + extended produces a reviewable action preview without dispatch.
- Existing walk-forward output populates correlated lifecycle events, elapsed duration, captured output, and structured measured results.
- Search and errors-only filters return no rows for unmatched activity.
- No horizontal body overflow at 1600, 1024, or 768 pixels.
- Native research commands without launch adapters remain disabled; registered actions are configured through gateway schemas.

No research run, model call, broker action, or executor control was dispatched during verification. Worker CPU, model latency, token counts and agent-to-agent topology are not exposed by the gateway; the workspace does not synthesize these measurements. The visual timeline displays actual correlated gateway events and uses agent/tool labels when present in their payloads.
