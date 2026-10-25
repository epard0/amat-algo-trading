# Source capability inventory

`cli-inventory.json` discovers the current six CLI parser trees and records source hashes. It currently contains 93 leaf endpoints and 243 Python source hashes. Discovery is separate from API action support: inspect `/api/v1/capabilities` for executable adapters. Unsupported engine workflows must remain visibly unsupported rather than accepting a shell string.

Refresh from the workspace root:

```bash
amat/.venv/bin/python frontend/contracts/refresh_inventory.py
```

The refresh constructs parsers only and does not dispatch a command, use broker credentials, or contact a provider. It must run in the existing AMAT environment because importing parser modules requires the engine's dependencies. Defaults are normalized against the AMAT working directory. Source hashes reflect local modifications as well as committed code.

Browser contracts come from the application API's `/openapi.json`; native CLI discovery is not a substitute for server input validation or an order-authority decision.
