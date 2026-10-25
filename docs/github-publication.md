# Preparing this repository for GitHub

This procedure prepares a reviewable source upload. It does not publish a repository, change licensing, rewrite history or deploy engines. The working checkout can contain more application code than HEAD, so preserve existing work before selecting files.

## Publication contents

Include all four application packages' source/tests, lockfiles, reviewed nonsecret configs, blank environment templates, maintained Markdown documentation, schemas and small deliberate fixtures. Preserve vendor notices and provenance. `amat` and `executor` retain proprietary package metadata and distribution guards; there is no root open-source license. The owner must choose any change to those terms separately.

Keep actual host/IP, SSH identity and trust settings in [ignored private configuration](remote-configuration.md); the source loader requires them explicitly.

Exclude credentials/keys, private SSH configuration, environments/dependencies/caches, runtime databases and account exports, vendor datasets, generated research output, server archives, backup volumes, browser captures and private operations evidence. Ignore rules preserve these files locally; they do not delete them, and do not untrack already tracked content. Historical docs may retain pointers to excluded evidence.

## Inspect the candidate privately

```sh
git status --short
python scripts/check_publication.py
python scripts/check_publication.py --history
git diff --check
```

The default scan examines tracked working files plus nonignored untracked files. It reports finding categories and paths without matched values. `--history` also scans reachable commits/tags for supported secret patterns. A pass is not a complete audit: inspect custom credentials, URLs, personal/account identifiers, notebook output, binaries and third-party notices. Review remote URLs privately for embedded credentials before setting a GitHub destination; do not paste their values into chat or issues.

If any actual credential ever entered history, revoke/rotate it first and coordinate cleanup. `.gitignore` cannot erase old commits. Do not casually rewrite shared branches or delete private source evidence.

## Prefer a clean source export when local snapshot refs contain private data

The checker can create a new external source directory after the working-file scan passes:

```sh
python scripts/check_publication.py --export ../amat-github-source
```

It refuses to overwrite an existing directory or export inside the working repository. It copies only tracked/nonignored source candidates, preserves file modes, and omits `.git`, ignored private data and all existing history. Initialize a fresh Git repository in that export if you choose this publication route. Do not use a mirror push or copy the old `.git` directory into it.

`--history` intentionally includes all reachable refs, including local tool snapshots. This is stricter than checking branches/tags alone. A local snapshot finding does not prove a key was pushed publicly; it does make a mirror upload unsafe. Keep the original checkout and private baseline intact while deciding whether to publish clean source or perform coordinated history cleanup.

## Stage deliberately

After resolving findings, add explicit source/docs paths and review all newly included files. The original checkout has entire components that were untracked; forgetting these can produce a repository that documents an application it does not contain. Conversely, a blanket force-add can include private runtime state.

```sh
# Example selection; inspect each directory before using it.
git add README.md CONTRIBUTING.md SECURITY.md .gitignore AGENTS.md scripts docs
git add amat executor kalshi-perps frontend research package-server.sh
python scripts/check_publication.py --staged --history
git diff --cached --stat
```

Review the staged patch privately. `--staged` reads index contents, including an older staged secret even if you subsequently edited the working file. A staged check is only complete after all intended files are staged. Keep `.env.example` / `.env.*.example` blank. Do not use `git add -f` to bypass publication exclusions.

## Verify portability

Run [package gates](testing.md). Use a disposable clone or export of the actual candidate to verify that sibling source, lockfiles and maintained doc links are included. A passing build in a long-lived checkout can depend on ignored data, installed packages or files absent from Git.

Review branch protection, contributor permissions, secret scanning and private vulnerability reporting settings in your own GitHub repository. No CI credential should carry broker order or withdrawal permissions. Do not put production secrets into a pull-request job. The repository does not publish to PyPI/npm or deploy services as part of these checks.

## Upload after review

Choose repository ownership, visibility and licensing deliberately. Create the GitHub repository using your account, configure a credential-free remote URL and push only the reviewed branch/tags. No GitHub repository is created or pushed by the preparation work. Retain private baseline and account backups outside the checkout.

See [the preparation verification record](publication-verification-2026-10-25.md) for actual checks performed during this cleanup and remaining limits.
