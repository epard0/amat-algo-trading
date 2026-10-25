# Private remote-server and SSH configuration

The application has no built-in operator host/IP, SSH username, SSH port, private-key path or trust-store path. Every connection identity must be supplied explicitly in an operator-owned private configuration file. Missing or invalid fields stop configuration loading before SSH is attempted. The real private key stays in a separate local file; source code and TOML never contain its key material.

## File locations and Git exclusions

| Item | Location / selection | Publication rule |
| --- | --- | --- |
| Public placeholder template | `frontend/config/remote.example.toml` | Track this example; it contains no working endpoint or credentials |
| Default private connection config | `frontend/.runtime/remote.toml` | Ignored with the whole runtime directory |
| Alternative private connection config | `TERMINAL_REMOTE_CONFIG=/absolute/path/remote.toml` | Prefer outside the checkout; `remote.toml`, `ssh.toml` and `*.local.toml` are ignored at any repository depth |
| Default with custom terminal storage | `$TERMINAL_STORAGE/remote.toml` | Keep storage outside Git or under an ignored runtime directory |
| SSH private key | Your private SSH directory outside the checkout | Reference it with `key_path`; never copy key bytes into config or source |
| SSH trusted host keys | Your private OpenSSH trust store | Reference it with `known_hosts`; repository copies of trust files are ignored |

The Git exclusions also cover `.KEYS/`, `.ssh/`, common private-key names/extensions, public-key files, `known_hosts` variants and `authorized_keys`. Git ignores only affect untracked files: inspect staged content and history before publication. A custom override with an unrelated filename inside the checkout needs its own ignore rule; using an external path avoids that ambiguity.

## Create the private configuration

From the repository root:

```sh
mkdir -p frontend/.runtime
chmod 700 frontend/.runtime
cp -n frontend/config/remote.example.toml frontend/.runtime/remote.toml
chmod 600 frontend/.runtime/remote.toml
```

Edit the copied file locally. Do not print or paste its contents into chat, logs, screenshots or issue reports. The template uses placeholder infrastructure values. Replace them with your own configuration:

```toml
host = "your-vps.example.com"
port = 22
username = "your-operator"
key_path = "~/.ssh/amat-vps"
known_hosts = "~/.ssh/known_hosts"
key_passphrase = ""
```

All five identity fields (`host`, `port`, `username`, `key_path`, `known_hosts`) are mandatory. The key passphrase is optional for an unencrypted key, or decrypts the configured key when required; it is not a login password. Put the real passphrase only in the private copy. Blank/invalid key paths are rejected. `~` expands to the local user's home; other relative key/trust paths resolve from the gateway process's working directory, so absolute paths or `~` are preferable.

The loader accepts only a regular configuration file owned by the current user, at most 64 KiB, with no group/other permission bits. Symlinks, public permissions, malformed TOML and missing required fields fail explicitly. The standard application container/state names and bounded timeout/output defaults are operational settings, not a fallback connection identity.

For an external private file:

```sh
TERMINAL_REMOTE_CONFIG=/absolute/path/remote.toml \
  TERMINAL_PERPS_TARGET=remote \
  frontend/.venv/bin/python frontend/scripts/start.py --production
```

Use a file you have already created and protected. Setting the override does not create it or copy credentials. A missing configuration leaves remote observations unavailable; it does not discover a host, choose a key from the agent, or fall back to a local account.

## Trust and authentication

Verify the server's SSH fingerprint through an independent trusted channel, then establish the corresponding OpenSSH known-host entry before enabling the application. For nonstandard ports the trusted entry uses OpenSSH's bracketed host/port form. Unknown or changed keys are refused. Do not use automatic host-key acceptance, password fallback or disabled verification to make setup succeed.

`executor.remote.SSHRunner` receives only the validated explicit configuration. It uses the configured key, rejects unknown host keys, disables agent authentication and key discovery, and applies connection/authentication/command/output bounds. Monitoring uses registered named operations and remains read-only. The browser cannot supply arbitrary hosts, keys, shell text or environment dictionaries.

The authenticated loopback Remote view may show connection metadata such as the configured host and username to the operator. It does not return private-key contents or the passphrase. Keep screenshots of private connection settings out of source publication.

## Other SSH entry points

The registered gateway diagnostics, VPS actions, paper-session reader and backup puller share the same private configuration loader/transport. The legacy `package-server.sh` packages locally and prints a deployment command; it does not connect. If using that manual printer, supply an OpenSSH alias defined in your private `~/.ssh/config`, with its host/user/key settings outside this repository. Do not insert your endpoint or key into the script. Remote host-side helpers use their own process environment and native account state; they do not embed workstation SSH credentials.

## Verify without disclosing values

```sh
git check-ignore frontend/.runtime/remote.toml
python scripts/check_publication.py
python scripts/check_publication.py --staged
```

The publication checker rejects private-config/key/trust filenames, common key/token material and embedded non-loopback IPv4 addresses in eligible source/docs, reporting finding categories and paths without matched values. Loopback UI/API URLs and placeholder DNS names remain allowed. This remains a heuristic: review custom DNS names, custom key filenames and encoded/binary material privately.

Historical deployment notes containing real infrastructure identity remain ignored and local, alongside private `operations/` evidence. They are not sanitized into invented receipts. The existing Git history includes local tool snapshots with private material; a source-only export excludes those refs. Ignore rules cannot erase history. Use [the publication procedure](github-publication.md), [security guidance](../SECURITY.md), and [remote interface details](../frontend/docs/REMOTE.md).
