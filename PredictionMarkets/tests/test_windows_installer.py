"""The PowerShell installer, checked as a program rather than as a string.

There is no PowerShell on the machines this suite runs on, so nothing here
executes `install.ps1`. What it does instead is assert the rules that are
invisible until someone runs it on the one platform nobody develops on: the
dialect Windows PowerShell 5.1 accepts, the encoding it assumes, and the
handful of Windows-specific ways an installer destroys something.

The cross-checks against ``install.sh.template`` matter most. The two
installers have to agree on the launcher's subcommands and on the install
manifest, and a shell script edited on its own is exactly how they stop
agreeing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install.ps1"
TEMPLATE = ROOT / "storefront" / "web" / "install.sh.template"
DOCS = ROOT / "docs" / "WINDOWS.md"


@pytest.fixture(scope="module")
def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def launcher(script: str) -> str:
    """The kx15.cmd template, which is batch rather than PowerShell.

    Kept separate because the two dialects disagree about almost everything:
    `&&` is a PowerShell 7 pipeline chain operator and an ordinary cmd
    conditional, so a test for one would fire on the other.
    """
    match = re.search(r"\$template = @'\n(.*?)\n'@", script, re.S)
    assert match, "the launcher here-string is no longer recognisable"
    return match.group(1)


@pytest.fixture(scope="module")
def powershell(script: str, launcher: str) -> str:
    """The script with the batch launcher and the comments removed."""
    without_launcher = script.replace(launcher, "")
    without_blocks = re.sub(r"<#.*?#>", "", without_launcher, flags=re.S)
    lines = [ln for ln in without_blocks.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(lines)


# ------------------------------------------------------------- the dialect ---

def test_the_file_is_ascii_only() -> None:
    """Windows PowerShell 5.1 reads a BOM-less .ps1 as the ANSI code page.

    A UTF-8 em dash written on Linux therefore reaches a paying customer's
    screen as three mojibake characters, and a BOM is its own trouble in a file
    that git, a web server and cmd.exe each handle in turn.
    """
    raw = SCRIPT.read_bytes()
    offenders = [
        (index, byte) for index, byte in enumerate(raw) if byte > 0x7F
    ]
    assert offenders == [], f"non-ASCII bytes at {offenders[:5]}"


def test_strict_mode_and_stop_on_error(script: str) -> None:
    assert "Set-StrictMode -Version Latest" in script
    assert "$ErrorActionPreference = 'Stop'" in script


def test_it_declares_the_five_one_floor(script: str) -> None:
    assert re.search(r"^#Requires -Version 5\.1$", script, re.M)


@pytest.mark.parametrize("modern", ["??", "?.", "&&", "||", "-ErrorAction Ignore"])
def test_no_powershell_seven_only_syntax(powershell: str, modern: str) -> None:
    """5.1 is the floor, and it is the copy already on every Windows.

    Null-coalescing, null-conditional and the pipeline chain operators are all
    parse errors there, which means the whole file fails rather than one line.
    """
    assert modern not in powershell


def test_no_ternary(powershell: str) -> None:
    """`$a ? $b : $c` arrived in PowerShell 7 and is a parse error in 5.1."""
    assert not re.search(r"\)\s*\?\s", powershell)


@pytest.mark.parametrize(
    "bashism", ["#!/bin/sh", "[[ ", "esac", "fi\n", "export ", "$(uname"]
)
def test_no_bashisms(powershell: str, bashism: str) -> None:
    assert bashism not in powershell


def test_every_web_request_uses_basic_parsing(powershell: str) -> None:
    """Without it, 5.1 hands the body to the Internet Explorer DOM parser,
    which is absent on Server Core and slow everywhere else."""
    calls = powershell.count("Invoke-WebRequest")
    assert calls >= 2
    assert powershell.count("UseBasicParsing") >= calls


def test_tls_twelve_is_forced(powershell: str) -> None:
    """.NET Framework 4.x does not default to it, so an unpatched 5.1 host
    offers TLS 1.0 and every storefront closes the connection."""
    assert "[Net.ServicePointManager]::SecurityProtocol" in powershell
    assert "Tls12" in powershell


def test_last_exit_code_is_seeded_globally(powershell: str) -> None:
    """A script-scope assignment would shadow the automatic variable that
    native commands update, and every exit code read afterwards would be the
    seeded zero."""
    assert "$global:LASTEXITCODE = 0" in powershell


# ------------------------------------------------------------- the licence ---

def test_the_licence_key_never_appears_in_a_url(powershell: str) -> None:
    """A URL lands in the access log and in every proxy in between."""
    assert not re.search(r"[?&]key=", powershell)
    assert not re.search(r"[?&]license=", powershell, re.I)
    assert "X-KX15-License" in powershell


def test_the_download_is_checksummed(powershell: str) -> None:
    assert "/api/artifact-sha256" in powershell
    assert "Get-FileHash" in powershell
    assert "SkipChecksum" in powershell


def test_an_unrendered_copy_refuses_to_run(powershell: str) -> None:
    """The same guard install.sh has: braces left in the URL mean the server
    never rendered this file, and there is nothing sensible to fetch."""
    assert "$script:RenderedBaseUrl = '${BASE_URL}'" in powershell
    assert "$value.Contains('{')" in powershell


# ----------------------------------------------------------------- the PATH ---

def test_path_is_read_from_the_registry(powershell: str) -> None:
    """$env:Path is the merged process value. Writing it back to the user
    scope bakes every machine entry into the user's own PATH permanently."""
    assert "Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment'" in powershell
    assert "DoNotExpandEnvironmentNames" in powershell


def test_path_writes_preserve_the_value_kind(powershell: str) -> None:
    """SetEnvironmentVariable always writes REG_SZ; on a REG_EXPAND_SZ PATH
    that silently freezes %USERPROFILE% into today's expansion."""
    assert "[Microsoft.Win32.RegistryValueKind]::ExpandString" in powershell


def test_the_change_is_broadcast(powershell: str) -> None:
    assert "SendMessageTimeout" in powershell
    assert "'Environment'" in powershell


# -------------------------------------------------------------- the service ---

def test_service_and_live_are_refused_together(powershell: str) -> None:
    assert "if ($Service -and $Live)" in powershell


def test_the_scheduled_task_never_passes_live(powershell: str, launcher: str) -> None:
    """Live trading is two independent switches on purpose. A background task
    supplying one of them leaves a single unattended setting between a config
    file and real money."""
    task = re.search(
        r"function Install-BackgroundTask \{.*?\n\}", powershell, re.S
    )
    assert task, "Install-BackgroundTask is no longer recognisable"
    assert "--live" not in task.group(0)
    assert "'/c \"\"' + $ShimPath + '\" run\"'" in task.group(0)


def test_the_task_registration_is_idempotent(powershell: str) -> None:
    assert "Unregister-ScheduledTask" in powershell


# ------------------------------------------------------- where state lives ---

def test_the_launcher_pins_state_outside_the_install_directory(launcher: str) -> None:
    """`kx15 update` replaces the program directory and deletes the old one.
    A ledger written there is a trading history destroyed by the next upgrade.
    """
    assert 'set "KX15_STATE_DIR=%KX15_CONFIG%"' in launcher


def test_the_launcher_sets_the_same_environment_as_the_posix_one(
    launcher: str,
) -> None:
    for name in ("KX15_HOME", "KX15_CONFIG", "KX15_BASE_URL", "PYTHONPATH"):
        assert f'set "{name}=' in launcher


def test_the_shim_lives_outside_the_swapped_directory(powershell: str) -> None:
    """cmd.exe holds a batch file open while it runs, and a rename over an open
    file fails on Windows, so a launcher inside the payload would make every
    `kx15 update` fail."""
    assert "$appDir = Join-Path $installRoot 'app'" in powershell
    assert "$binDir = Join-Path $installRoot 'bin'" in powershell


def test_the_install_swaps_rather_than_overwrites(powershell: str) -> None:
    assert '$appNew = "$appDir.new"' in powershell
    assert '$appOld = "$appDir.old"' in powershell


def test_pip_is_never_run_through_its_console_script(powershell: str) -> None:
    """A console-script shim has the virtualenv's path compiled into it and
    stops working the moment the swap renames the directory."""
    assert "Scripts\\pip.exe" not in powershell
    assert "'-m', 'pip', 'install'" in powershell


# ------------------------------------------------- agreement with install.sh ---

def _posix_launcher(template: str) -> str:
    match = re.search(r"<<'LAUNCHER'\n(.*?)\nLAUNCHER\n", template, re.S)
    assert match, "the POSIX launcher here-doc is no longer recognisable"
    return match.group(1)


def test_the_two_launchers_expose_the_same_subcommands(launcher: str) -> None:
    """The shell launcher is the specification. Editing one and not the other
    is how a Windows user ends up without a command the docs promise."""
    posix = _posix_launcher(TEMPLATE.read_text(encoding="utf-8"))
    arms = re.search(r'case "\$cmd" in\n(.*?)\nesac', posix, re.S)
    assert arms, "the POSIX dispatch is no longer recognisable"

    expected: set[str] = set()
    for line in arms.group(1).splitlines():
        found = re.match(r"\s{2}([a-z][a-z|\-]*)\)", line)
        if not found:
            continue
        for name in found.group(1).split("|"):
            expected.add(name)
    expected.discard("help")

    missing = {
        name for name in expected if f'if /i "%SUB%"=="{name}"' not in launcher
    }
    assert missing == set(), f"the Windows launcher is missing {sorted(missing)}"


def test_the_manifest_keys_match_the_posix_installer(powershell: str) -> None:
    """Both uninstallers read a file of the same shape, so a rename on one side
    turns the other's uninstall into a guess at paths."""
    posix = TEMPLATE.read_text(encoding="utf-8")
    block = re.search(r"<<MANIFEST\n(.*?)\nMANIFEST", posix, re.S)
    assert block, "the POSIX manifest here-doc is no longer recognisable"

    for line in block.group(1).splitlines():
        key = line.split("=", 1)[0]
        assert f"{key}=" in powershell, f"the Windows manifest has no {key}"
    assert "'install-manifest'" in powershell


def test_the_licence_file_uses_the_posix_spelling(powershell: str) -> None:
    """install.sh writes KX15_LICENSE=<key> into <config>/licence; `kx15
    update` on either platform has to be able to read the other's."""
    assert '"KX15_LICENSE=$licence' in powershell
    assert "'licence'" in powershell


# -------------------------------------------------------------- uninstalling ---

def test_uninstall_keeps_the_ledger_unless_purged(powershell: str) -> None:
    body = re.search(r"function Invoke-Uninstall \{.*?\n\}", powershell, re.S)
    assert body, "Invoke-Uninstall is no longer recognisable"
    text = body.group(0)

    purge = text.index("if ($PurgeState) {\n        if ($PSCmdlet.ShouldProcess($ConfigDir")
    removal = text.index("Remove-Tree -Path $ConfigDir")
    assert purge < removal, "the config directory is removed outside the -Purge branch"


def test_uninstall_confirms_before_deleting(powershell: str) -> None:
    assert "Read-Host 'Type \"yes\" to continue'" in powershell
    assert "if (-not $NoPrompt -and -not $WhatIfPreference)" in powershell


def test_uninstall_supports_whatif(powershell: str) -> None:
    assert "SupportsShouldProcess = $true" in powershell
    assert powershell.count("$PSCmdlet.ShouldProcess") >= 4


# --------------------------------------------------------------------- help ---

@pytest.mark.parametrize(
    "section", [".SYNOPSIS", ".DESCRIPTION", ".PARAMETER", ".EXAMPLE"]
)
def test_comment_based_help(script: str, section: str) -> None:
    """`Get-Help .\\install.ps1` only works if the block is the first thing in
    the file."""
    assert script.startswith("<#")
    assert section in script


@pytest.mark.parametrize(
    "name",
    [
        "LicenseKey", "BaseUrl", "InstallDir", "ConfigDir", "Python",
        "System", "Service", "ServiceName", "Live", "SkipChecksum",
        "NoPathEdit", "Uninstall", "Purge", "Force", "Help",
    ],
)
def test_every_parameter_is_documented(script: str, name: str) -> None:
    assert f".PARAMETER {name}" in script


# --------------------------------------------------------------- generated ---

def test_generated_files_are_bom_free_and_crlf(powershell: str) -> None:
    """cmd.exe reads a byte-order mark as part of the first command and prints
    a garbled error before running the batch file."""
    assert "UTF8Encoding($false)" in powershell
    assert '$normalised = $normalised -replace "`n", "`r`n"' in powershell


def test_the_store_python_stub_is_rejected(powershell: str) -> None:
    """A zero-length reparse point that opens the Microsoft Store. It answers
    `where python` and sits ahead of everything else on a fresh user's PATH."""
    assert "WindowsApps" in powershell
    assert "$item.Length -eq 0" in powershell


def test_the_tar_fallback_exists(powershell: str) -> None:
    """Windows 10 before 1803 has no tar.exe, and Expand-Archive only knows
    zip."""
    assert "tar.exe" in powershell
    assert "GZipStream" in powershell
    assert "Expand-Archive " not in powershell


def test_the_tar_reader_refuses_to_escape_its_destination(powershell: str) -> None:
    body = re.search(r"function Resolve-TarEntryName \{.*?\n\}", powershell, re.S)
    assert body, "Resolve-TarEntryName is no longer recognisable"
    assert "'..'" in body.group(0)
    assert "'^[A-Za-z]:'" in body.group(0)


# --------------------------------------------------------------------- docs ---

def test_windows_docs_cover_both_routes() -> None:
    text = DOCS.read_text(encoding="utf-8")
    assert "WSL2" in text
    assert "scripts/install.ps1" in text
    # The one thing a Windows buyer must not discover after funding an account.
    assert "st_mode" in text
    assert "Scheduled Task" in text


# ------------------------------------------------------------------ syntax ---

def test_quotes_comments_and_brackets_all_close(script: str) -> None:
    """A rough lexer, not a parser.

    It cannot tell a good script from a bad one, but an unterminated
    here-string turns the whole file into a single syntax error at load time,
    and that is worth catching without a PowerShell to hand.
    """
    text = script
    index = 0
    length = len(text)
    stripped: list[str] = []

    while index < length:
        char = text[index]
        if text.startswith("<#", index):
            depth = 1
            cursor = index + 2
            while cursor < length and depth:
                if text.startswith("<#", cursor):
                    depth += 1
                    cursor += 2
                elif text.startswith("#>", cursor):
                    depth -= 1
                    cursor += 2
                else:
                    cursor += 1
            assert depth == 0, "unterminated block comment"
            index = cursor
            continue
        if text.startswith("@'", index) or text.startswith('@"', index):
            quote = text[index + 1]
            cursor = index + 2
            while cursor < length and text[cursor] in " \t":
                cursor += 1
            if cursor < length and text[cursor] in "\r\n":
                end = text.find("\n" + quote + "@", cursor)
                assert end != -1, "unterminated here-string"
                index = end + 3
                continue
        if char == "'":
            cursor = index + 1
            while cursor < length:
                if text[cursor] == "'":
                    if cursor + 1 < length and text[cursor + 1] == "'":
                        cursor += 2
                        continue
                    break
                assert text[cursor] != "\n", "newline in a single-quoted string"
                cursor += 1
            assert cursor < length, "unterminated single-quoted string"
            index = cursor + 1
            continue
        if char == '"':
            cursor = index + 1
            while cursor < length:
                if text[cursor] == "`":
                    cursor += 2
                    continue
                if text[cursor] == '"':
                    if cursor + 1 < length and text[cursor + 1] == '"':
                        cursor += 2
                        continue
                    break
                assert text[cursor] != "\n", "newline in a double-quoted string"
                cursor += 1
            assert cursor < length, "unterminated double-quoted string"
            index = cursor + 1
            continue
        if char == "#":
            newline = text.find("\n", index)
            index = length if newline == -1 else newline
            continue
        stripped.append(char)
        index += 1

    opening = {")": "(", "}": "{", "]": "["}
    stack: list[str] = []
    for char in stripped:
        if char in "({[":
            stack.append(char)
        elif char in ")}]":
            assert stack, f"unmatched closing {char}"
            assert stack.pop() == opening[char], f"mismatched {char}"
    assert stack == [], f"unclosed {stack}"
