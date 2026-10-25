# FinceptTerminal source reuse

Source: https://github.com/Fincept-Corporation/FinceptTerminal
Pinned revision: `3f444cb7ab712d85a57d270d5edd6e7a2ef50d55`.

These are actual unmodified upstream code files, copied at the user's request. [the source manifest](../../../../frontend/vendor/fincept/manifest.json) records every source path and SHA-256. They are reference material, not a working Qt module or a React library; transitive Qt and Fincept dependencies are deliberately not vendored.

| Copied source | Planned use in AMAT Terminal |
|---|---|
| `ThemeTokens.h` | Port applicable surface, spacing, typography and semantic color tokens into the web theme. |
| `DashboardStatusBar.cpp/.h` | Port compact footer composition; replace feed/ready placeholders with measured AMAT/executor observations. |
| `EquityWatchlist.cpp/.h` | Port named-watchlist organization, dense columns, instrument selection and quote-update behavior. |
| `EquityOrderBook.cpp/.h` | Reference bid/ask arrangement and depth display; expose only when the configured provider supplies depth. |

Current upstream is C++20/Qt6. Do not assume older React/Tauri examples describe this revision. React/Electron implementation requires a deliberate port; broker submission and account integration stay with the existing executor rather than upstream trading code.

Preserve the complete copied [LICENSE](../../../../frontend/vendor/fincept/LICENSE), which declares AGPL-3.0-or-later and includes an additional commercial/dual-license notice. The executor README describes the workspace as proprietary. Track provenance for every derived file and resolve applicable licensing terms before integrating derived code into a distributable or hosted application. This planning delivery does not relicense the workspace or claim that reference files are permissively licensed. No upstream account services, telemetry, or external broker routing are activated.
