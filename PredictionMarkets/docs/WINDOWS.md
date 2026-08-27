# KX15 on Windows

There are two ways to run KX15 on a Windows machine, and they are not
equivalent. Read the first section before you pick one.

- **Native Windows** — `scripts/install.ps1`. A PowerShell installer, a
  `kx15.cmd` launcher on PATH, an optional Scheduled Task. No virtual machine,
  no Linux.
- **WSL2** — the ordinary POSIX installer, `install.sh`, run inside an Ubuntu
  distribution. This is what `install.sh` tells you to do, because it refuses
  to run on native Windows.

---

## Which one

| | Native Windows | WSL2 |
| --- | --- | --- |
| Paper trading | Yes | Yes |
| **Live trading** | **Not yet — see below** | Yes |
| Dashboard (`kx15 dashboard`) | Yes, `http://127.0.0.1:8787` | Yes, but through the WSL network adapter |
| Background operation | Scheduled Task, survives reboot | `systemd` inside WSL, needs WSL itself running |
| Disk and start-up cost | None beyond Python | ~1 GB, a VM starts with the shell |
| Identical to the tested Linux deployment | No | Yes |

**The private key check.** `config.KalshiSettings.validate_live` refuses to
start if the Kalshi private key is readable by anyone but its owner. On POSIX
that is the mode bits. On Windows it cannot be: Python synthesises `st_mode`
from the read-only attribute alone and reports `0o666` for any writable file
and `0o444` for a read-only one, so the POSIX test

```python
if self.private_key_path.stat().st_mode & 0o077:      # POSIX only
```

refused **every** key file on Windows regardless of how tightly the ACL was
locked down, and live trading was impossible there.

`config._check_key_permissions` now branches on `os.name`. On Windows it reads
the real DACL through `icacls` — present on every supported version, so no
pywin32 dependency for one check — and refuses a key granted to `Everyone`,
`Users`, `Authenticated Users` or `INTERACTIVE`. The built-in administrators
group and `SYSTEM` are unavoidable on Windows and are not what the check is
about. A machine where the ACL cannot be read at all is a refusal, not a pass.

So:

- **Paper trading, research, the dashboard, the ledger and the decision log all
  work natively.**
- **Live trading now works natively too**, provided the key's ACL is what
  `install.ps1` sets. It has not been exercised on a real Windows host — see the
  note at the end of this document — so treat WSL2 as the better-tested route
  for real money until someone has run it.

`install.ps1` still applies a proper ACL to the config directory, the `.env` and
the licence file. That is the correct thing to do regardless, and it is what the
future `config.py` branch will be checking.

---

## Native install

### Requirements

- Windows 10 1809 or later, or Windows 11. Windows Server 2019+ works too.
- Windows PowerShell **5.1** (in the box) or PowerShell **7+**. Both are
  supported; the installer is written to 5.1's dialect so that neither needs
  installing first.
- **Python 3.13 or newer.** The Kalshi SDK requires it.

  ```powershell
  winget install --id Python.Python.3.13 --source winget
  ```

  or python.org's installer with *Add python.exe to PATH* ticked.

### Install

```powershell
# read it first - you are about to run a script from the internet
Invoke-WebRequest -UseBasicParsing https://your.storefront/install.ps1 -OutFile install.ps1
notepad install.ps1

.\install.ps1 -LicenseKey KX15-XXXXX-XXXXX-XXXXX-XXXXX
```

If PowerShell refuses to run it at all, see *Execution policy* below.

### What it puts where

| Path | What |
| --- | --- |
| `%LOCALAPPDATA%\Programs\KX15\app` | the program and its virtualenv |
| `%LOCALAPPDATA%\Programs\KX15\bin\kx15.cmd` | the launcher, added to PATH |
| `%LOCALAPPDATA%\Programs\KX15\bin\install.ps1` | a copy of the installer, for `kx15 update` and `kx15 uninstall` |
| `%APPDATA%\KX15\.env` | your configuration |
| `%APPDATA%\KX15\trade_ledger.db` | **the ledger — this is the one that matters** |
| `%APPDATA%\KX15\decisions.jsonl` | why every evaluation traded or did not |
| `%APPDATA%\KX15\licence` | your key, so `kx15 update` does not ask for it |
| `%APPDATA%\KX15\install-manifest` | exactly what was installed, for the uninstaller |
| `%USERPROFILE%\Desktop\kx15_Performance_Reports` | logs and reports |

With `-System` the first three move to `%ProgramFiles%\KX15` and the PATH entry
becomes a machine one. **The config directory stays per user either way**: a
licence, an API key and a trading history belong to a person, not to a box.

The program directory and the state directory are separate on purpose.
`kx15 update` replaces the program directory wholesale and deletes the old one.
A ledger written inside it would be a trading history destroyed by the next
upgrade — which is exactly what happened once, and why `KX15_STATE_DIR` exists.

### Options

```
-LicenseKey <key>    your licence; also read from $env:KX15_LICENSE, then from
                     the copy saved by a previous install
-BaseUrl <url>       storefront to install from
-InstallDir <path>   install root
-ConfigDir <path>    config, ledger and licence directory
-Python <path>       an interpreter to use, skipping discovery
-System              machine-wide install (elevates itself if needed)
-Service             register a Scheduled Task that paper-trades
-ServiceName <name>  name of that task (default: KX15)
-SkipChecksum        install without verifying the download (not advised)
-NoPathEdit          leave PATH alone
-Uninstall [-Purge]  remove it
-Force               do not ask before removing
-Help                usage
```

`Get-Help .\install.ps1 -Full` prints the long form of all of these.

---

## The `kx15` command

Identical to the POSIX launcher, because it is the same set of subcommands:

```
kx15 run                 paper trading (default, no funds at risk)
kx15 run --live          live trading (also needs ENABLE_LIVE_TRADING=true)
kx15 once                one scan and exit
kx15 dashboard           live stats from your own ledger, on 127.0.0.1:8787
kx15 trades              dump the ledger and cumulative stats
kx15 decisions --tail 5  why each evaluation traded or did not
kx15 report              write a performance report
kx15 history             every settled Kalshi trade on your account
kx15 logs [lines]        tail the current log
kx15 config              guided Kalshi API key setup
kx15 edit                open .env in %EDITOR%, or Notepad
kx15 where               install, config, log and ledger paths
kx15 licence --status    what the licence server last said
kx15 update              re-run the installer for the latest version
kx15 uninstall [--purge] remove it
kx15 version
```

`kx15.cmd` is a batch file rather than a `.ps1`. Batch is the one interpreter
present on every Windows, it needs no execution-policy exemption, and a
Scheduled Task, a shortcut and an operator's shell can all run it the same way.

It sets `PYTHONPATH`, `KX15_HOME`, `KX15_CONFIG`, `KX15_STATE_DIR` and
`KX15_BASE_URL` before starting Python, and then runs from the config
directory, so every mutable file lands there rather than in the program
directory. Each of those can be overridden by setting it in the environment
first; the launcher only fills in what is not already there.

---

## PATH

The installer adds the `bin` directory to the **user** PATH (or the machine
PATH with `-System`), reading the current value **out of the registry** and
writing the new one back there.

This is worth spelling out, because the obvious alternative is wrong.
`$env:Path` is the *process* value: the machine PATH and the user PATH already
concatenated, plus whatever the current shell added. Writing that back into the
user scope permanently bakes every machine entry into your own PATH, and the
next machine-wide change then leaves two copies that drift apart. It is the
single most common way an installer corrupts a Windows PATH, and it is
irreversible without knowing what the value used to be.

The registry read also asks for the value **unexpanded**, so an entry written as
`%USERPROFILE%\bin` stays that way instead of being frozen into today's
expansion, and the value is written back with the same `REG_SZ` /
`REG_EXPAND_SZ` kind it had.

After writing, the installer broadcasts `WM_SETTINGCHANGE`, which is what tells
Explorer to refresh the environment it hands to programs it launches. It does
nothing for a shell that is already open, so **open a new terminal** regardless.

---

## Python discovery, and the Microsoft Store stub

The installer looks for an interpreter in this order:

1. `py -3.15`, `py -3.14`, `py -3.13`, `py -3`
2. `python3.13`, `python3`, `python` on PATH
3. `%LOCALAPPDATA%\Programs\Python\Python313\python.exe`, the same under
   `%ProgramFiles%`, `%ProgramFiles(x86)%` and `C:\`

Each candidate is asked for `sys.executable` and its version, rather than
trusted by name, so `py -3.13` resolves to the real interpreter and the
virtualenv is built from that.

**The Store stub is detected and rejected.** Windows ships an "app execution
alias" for `python`: a zero-length reparse point under
`%LOCALAPPDATA%\Microsoft\WindowsApps` whose only behaviour is to open the
Microsoft Store. It answers `where python`, it sits ahead of everything else on
a fresh user's PATH, and when a script runs it the failure looks like a broken
Python rather than a missing one. Anything under `WindowsApps`, and anything
zero bytes long, is skipped.

To get rid of it: **Settings > Apps > Advanced app settings > App execution
aliases**, and turn off the `python.exe` and `python3.exe` entries.

---

## Running in the background

```powershell
.\install.ps1 -LicenseKey KX15-... -Service
```

That registers a **Scheduled Task**, not a Windows service.

A real Windows service has to answer the Service Control Manager within 30
seconds of starting and keep answering control codes afterwards. A plain Python
program does not do that, and every recipe for making it look as though it does
goes through `nssm` or `pywin32` — a third-party binary, or a compiled
extension, that this project would then be shipping, hosting and signing. A
Scheduled Task is in the box on every supported Windows, restarts on failure,
survives a reboot, and needs nothing extra. The only thing it gives up is
`services.msc`.

| Scope | Runs as | Trigger | Level |
| --- | --- | --- | --- |
| per user | you, `S4U` (no stored password, runs signed out) | at logon | limited |
| `-System` | `SYSTEM` | at startup | highest |

If `S4U` registration fails — it needs the *Log on as a batch job* right, which
a managed account often lacks — the installer falls back to an interactive task
that starts when you sign in, and says so.

**The task always runs `kx15 run`, which is paper mode.** It can never pass
`--live`, and `-Service -Live` together is a hard error. Live trading is two
independent switches on purpose: `ENABLE_LIVE_TRADING=true` in the config *and*
`--live` on the command line. A background task that supplied one of them would
leave a single unattended setting standing between a config file and real money.

The task is registered but **not started** — the bot has no API key yet, and a
task started now would only fill the log with the same error until someone runs
`kx15 config`.

```powershell
Start-ScheduledTask   -TaskName 'KX15'
Stop-ScheduledTask    -TaskName 'KX15'
Get-ScheduledTaskInfo -TaskName 'KX15'   # last run, last result
```

Re-running the installer with `-Service` unregisters the old task first, so it
is safe to repeat.

---

## Your Kalshi private key

Create the key at **Account > API Keys** in the Kalshi web app. The private key
file is shown to you exactly once.

```powershell
Copy-Item "$env:USERPROFILE\Downloads\kalshi-private-key.pem" "$env:APPDATA\KX15\private_key.pem"
icacls "$env:APPDATA\KX15\private_key.pem" /inheritance:r /grant:r "${env:USERNAME}:(R)"
```

**Copy, do not move.** A move within the same volume carries the source
folder's ACL along with the file, so a key *moved* out of `Downloads` keeps
whatever could read `Downloads`. A copy inherits the destination's ACL instead —
and the installer has already set the config directory to "this user only,
inheritance off", which is the Windows equivalent of `chmod 700`.

Then in `%APPDATA%\KX15\.env`:

```
KALSHI_API_KEY=<the key id>
KALSHI_PRIVATE_KEY_PATH=C:\Users\<you>\AppData\Roaming\KX15\private_key.pem
```

Live trading on native Windows depends on this file's ACL being right; see
the top of this document.

---

## Updating

```powershell
kx15 update
```

Downloads a fresh copy of the installer from your storefront and runs it. If
the storefront does not serve `install.ps1`, it falls back to the copy saved
beside the launcher, and says so.

The update builds the new tree in `app.new`, then swaps it into place and
deletes `app.old`. A failed install therefore never leaves a half-working one on
PATH. Two Windows-specific notes on that swap:

- The launcher lives in `bin`, **outside** the directory that gets swapped.
  `cmd.exe` holds a batch file open for as long as it is running, and a rename
  over an open file fails on Windows — an in-tree launcher would make every
  self-update fail.
- Everything inside the virtualenv is driven through `python.exe -m pip`, never
  `Scripts\pip.exe`. A console-script shim has the virtualenv's path compiled
  into it and stops working the moment the directory is renamed; `python.exe`
  finds its own prefix from the `pyvenv.cfg` beside it and does not care where
  it is.

Your `.env`, ledger, decision log and licence are in `%APPDATA%\KX15` and are
never touched by an update.

---

## Uninstalling

```powershell
kx15 uninstall               # keeps the ledger, .env and logs
kx15 uninstall --purge       # removes those too
```

or, equivalently, from the saved installer:

```powershell
& "$env:LOCALAPPDATA\Programs\KX15\bin\install.ps1" -Uninstall
& "$env:LOCALAPPDATA\Programs\KX15\bin\install.ps1" -Uninstall -Purge -Force
& "$env:LOCALAPPDATA\Programs\KX15\bin\install.ps1" -Uninstall -WhatIf   # preview
```

The uninstaller reads `%APPDATA%\KX15\install-manifest` — written at install
time, listing exactly what was put on the machine — so it removes that and
nothing else. No guessing at paths, and no recursive delete built from a default
the install may not have used.

It asks for a typed `yes` first, unless `-Force`. Without `-Purge` it keeps the
config directory, the ledger and the logs, and tells you where they are.

`kx15 uninstall` copies the installer into `%TEMP%` and runs it from there, and
changes directory out of the tree first, because it is deleting the directory it
is running from. Even so, `cmd.exe` still holds `kx15.cmd` itself open, so the
`bin` directory occasionally survives as an empty husk until that window closes.
The uninstaller retries, then says plainly that it could not finish rather than
claiming a clean removal. Running `install.ps1 -Uninstall` directly avoids it.

---

## Troubleshooting

**"running scripts is disabled on this system"** — the default execution
policy. Either run it for one process only:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -LicenseKey KX15-...
```

or set it for your own account: `Set-ExecutionPolicy -Scope CurrentUser
RemoteSigned`. Nothing in KX15 changes it for you.

**"the file is not digitally signed" / SmartScreen** — a file downloaded with a
browser carries a mark-of-the-web. `Unblock-File .\install.ps1` clears it, after
you have read the file.

**`tar.exe` is missing** — Windows 10 1803 and later ship bsdtar. On anything
older the installer falls back to a tar reader built into itself
(`GZipStream` plus a 512-byte header walk) and tells you which it used. Nothing
extra to install either way.

**PATH does not have `kx15` in it** — open a new terminal. An already-running
shell inherited its environment when it started and no broadcast can change
that. If a new terminal still cannot find it, check
`[Environment]::GetEnvironmentVariable('Path', 'User')`.

**`-System` opens a window that vanishes** — the elevation re-launch runs in a
new console that closes when it finishes. Run the installer from an
Administrator PowerShell prompt instead and the output stays put.

**`Move-Item` fails during an update** — something has a file open in the
program directory: the bot itself, an editor, or real-time antivirus scanning
the virtualenv. Stop the bot (`Stop-ScheduledTask -TaskName 'KX15'`) and re-run.

**The scheduled task shows a result of `0x1` and stops** — run `kx15 run` by
hand in a terminal and read the error. Nine times out of ten it is a missing
`KALSHI_API_KEY` or a private key path that does not exist.

**Nothing appears in `kx15 logs`** — the log directory is on the Desktop, and
Windows Defender's *Controlled folder access* blocks unrecognised programs from
writing there. Either allow `python.exe` through, or set `KX15_LOG_DIR` to
somewhere else before starting the bot.

**The dashboard is not reachable from another machine** — by design. It binds
`127.0.0.1`, because it exposes full position and P&L history. Reach it over an
SSH tunnel or from the machine itself.

---

## The WSL2 route

Use this for live trading, and any time you want the machine to be running the
same thing the Linux deployment runs.

```powershell
wsl --install -d Ubuntu       # then reboot, and set a username and password
```

Inside the Ubuntu shell:

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.13 python3.13-venv
curl -fsSL https://your.storefront/install.sh | sh -s -- KX15-XXXXX-XXXXX-XXXXX-XXXXX
```

From there everything in the main README applies unchanged, including `chmod
600` on the private key, which WSL implements properly.

Three things to know:

- **Keep the bot's files inside the Linux filesystem** (`~/…`), not under
  `/mnt/c`. The 9P bridge to the Windows drive is slow, and its permission
  emulation is exactly what the private-key check exists to reject.
- **WSL stops when its last process exits** unless you keep something running,
  and `systemd` inside WSL needs `systemd=true` in `/etc/wsl.conf`. A bot that
  has to survive a reboot unattended is easier to run natively as a Scheduled
  Task — in paper mode — or on a small Linux VPS, which is what `harden.sh` is
  for.
- **`kx15 dashboard` binds `127.0.0.1` inside WSL.** Recent WSL forwards
  localhost to Windows, so `http://127.0.0.1:8787` in a Windows browser usually
  just works; if it does not, use the address from `hostname -I`.

---

## What is deliberately absent

- **No MSI, no signed installer, no Add/Remove Programs entry.** Those need a
  code-signing certificate and a build pipeline that produces one; a PowerShell
  script that can be read before it is run is the honest version of the same
  thing at this scale.
- **No Windows service.** See above.
- **No registry state beyond the PATH entry.** Everything else is a file, and
  the manifest lists it.
- **No new runtime dependencies.** The tar fallback, the `WM_SETTINGCHANGE`
  broadcast and the ACL work are all done with what Windows and .NET already
  provide, for the same reason the Prometheus exposition and the dashboard
  charts elsewhere in this repository are hand-rolled: an undeclared import has
  broken this project before.
