"""The no-brainer path: a Desktop icon, and nothing else to know.

These two PowerShell files are Windows-only and CI is Linux, so almost
everything here reads the text and asserts the things that break silently --
same standing as ``tests/test_desk_agent_launcher.py``. The one exception is
at the foot of this file: GitHub's Ubuntu runners ship ``pwsh``, so the
scripts are handed to a real PowerShell parser. That catches the failure text
analysis never can -- a file the language will not accept, which from a
double-clicked icon is a window that flashes and vanishes with nothing to
paste back.

Four things went wrong in one sitting on 2026-09-02, each of which read as
"karaoke is broken" rather than "that command was wrong":

1. a stale checkout, so the running code was not the fixed code
2. a VPN address published into the QR that no phone in the room resolved
3. Windows Firewall dropping every phone, with no message anywhere
4. a ``Read-Host`` prompt that returned empty straight into a ``--host`` flag

The launcher exists so none of the four is ever typed again, and the
assertions below are one per guard.

And the trap that outranks all of them: **one non-ASCII byte in a BOM-less
.ps1 stops PowerShell 5.1 parsing the file at all.** It decodes as
Windows-1252, the stray byte closes a string, no line runs, and the script
prints nothing whatsoever -- from a double-clicked icon that is a console
window that flashes and disappears.
"""

import pathlib
import re
import shutil
import subprocess

import pytest

from tools.karaoke_server import queue_server

REPO = pathlib.Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "tools" / "karaoke_server" / "start_karaoke.ps1"
INSTALLER = REPO / "tools" / "karaoke_server" / "install_shortcut.ps1"
PS_FILES = (LAUNCHER, INSTALLER)


def read(path):
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def launcher():
    return read(LAUNCHER)


@pytest.fixture(scope="module")
def installer():
    return read(INSTALLER)


# ------------------------------------------------- the PowerShell 5.1 traps --


@pytest.mark.parametrize("path", PS_FILES, ids=lambda p: p.name)
def test_the_file_is_ascii_only(path):
    """The byte check, not a lint: this is the one that produces no output."""
    raw = path.read_bytes()
    offenders = sorted({b for b in raw if b > 127})
    assert not offenders, (
        f"{path.relative_to(REPO)} carries non-ASCII bytes {offenders}. "
        "PowerShell 5.1 reads a BOM-less .ps1 as Windows-1252 and the decoded "
        "byte acts as a string delimiter, so the script runs no lines and "
        "prints nothing at all. Use '--' for a dash, plain quotes, no emoji."
    )


@pytest.mark.parametrize("path", PS_FILES, ids=lambda p: p.name)
def test_no_bash_chaining(path):
    assert "&&" not in read(path), "PowerShell 5.1 rejects && outright"


@pytest.mark.parametrize("path", PS_FILES, ids=lambda p: p.name)
def test_no_tilde_for_home(path):
    text = read(path)
    assert not re.search(r"~[\\/]", text), (
        "'~' is not home on Windows PowerShell -- use $HOME, $env:USERPROFILE "
        "or $env:LOCALAPPDATA"
    )


@pytest.mark.parametrize("path", PS_FILES, ids=lambda p: p.name)
def test_no_here_strings(path):
    """A here-string opens the '>>' continuation prompt and eats the paste."""
    for number, line in enumerate(read(path).splitlines(), start=1):
        assert not re.search(r"@[\"']\s*$", line), (
            f"{path.name}:{number} opens a here-string; build strings with "
            "concatenation and `r`n instead"
        )


@pytest.mark.parametrize("path", PS_FILES, ids=lambda p: p.name)
def test_no_hard_coded_user_path(path):
    text = read(path)
    assert "C:\\Users\\" not in text, (
        "a hard-coded home directory pins this to one machine and one of the "
        "two checkouts; derive from $PSScriptRoot and $env:LOCALAPPDATA"
    )


def test_the_launcher_does_not_take_a_host_parameter(launcher):
    """$Host is an automatic variable; a -Host parameter collides with it."""
    params = launcher.split("param(", 1)[1].split(")\n", 1)[0]
    assert "$Host" not in params
    assert "$Address" in params


# ------------------------------------------------------ it finds the repo --


def test_the_launcher_finds_its_own_checkout(launcher):
    # It must follow the folder it was installed from: there are two
    # pwb-toolbox checkouts on this machine and the folder can also be copied.
    assert "$PSScriptRoot" in launcher
    assert re.search(
        r"\$RepoRoot\s*=\s*Split-Path\s*\(Split-Path\s*\$PSScriptRoot", launcher
    )
    # and it says so plainly rather than throwing when the folder was moved
    assert "queue_server.py" in launcher
    assert "could not find its own program files" in launcher.lower()


def test_the_installer_points_the_icon_at_the_checkout_it_ran_from(installer):
    assert "$PSScriptRoot" in installer
    assert "start_karaoke.ps1" in installer


# ------------------------------------------------------------ finding python --


def test_python_is_looked_for_in_every_place_it_lives(launcher):
    assert ".venv\\Scripts\\python.exe" in launcher
    assert "'python'" in launcher
    assert "'py'" in launcher and "'-3'" in launcher
    assert "Programs\\Python\\Python312\\python.exe" in launcher
    assert "$env:LOCALAPPDATA" in launcher


def test_a_python_candidate_must_prove_it_is_really_python(launcher):
    """A Windows box with no Python still answers to 'python'.

    It is the Microsoft Store stub: it prints nothing, exits 9009, and opens
    the Store. A launcher that checks only whether the command resolved hands
    that stub the whole night.
    """
    body = launcher.split("function Test-PythonCandidate", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "'--version'" in body
    assert "$LASTEXITCODE" in body
    assert "Python 3" in body
    assert "-ge 10" in body


def test_no_python_is_one_plain_sentence_and_a_download_link(launcher):
    missing = launcher.split("if (-not $pythonExe)", 1)[1].split("}", 1)[0]
    assert "python.org" in missing
    assert "Add python.exe to PATH" in missing
    assert "Stop-Here" in missing


def test_every_failure_pauses_so_the_window_cannot_flash_and_vanish(launcher):
    """The shortcut runs powershell -File; the console closes on exit."""
    body = launcher.split("function Stop-Here", 1)[1].split("\nfunction ", 1)[0]
    assert "Read-Host" in body
    assert "Press Enter to close" in body
    assert "exit 1" in body


# --------------------------------------------------------------- busy port --


def test_a_busy_port_is_a_sentence_not_a_traceback(launcher):
    assert "Test-PortBusy" in launcher
    assert "Net.Sockets.TcpListener" in launcher
    message = launcher.split("if (Test-PortBusy $Port)", 1)[1].split("}", 1)[0]
    assert "already running" in message.lower()
    assert "close the other karaoke window" in message.lower()


def test_the_server_itself_refuses_a_busy_port_in_plain_english():
    """The launcher is not the only way in -- 'python -m ...' and the
    one-file karaoke_os.py hit the same wall, so the message lives in the
    server where all three paths reach it."""
    text = queue_server.port_in_use_message(8772)
    assert "already running" in text.lower()
    assert "close the other karaoke window" in text.lower()
    assert "8772" in text


def test_serve_returns_rather_than_raising_when_the_port_is_taken(monkeypatch, capsys):
    def refuse(*args, **kwargs):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(queue_server, "ThreadingHTTPServer", refuse)
    monkeypatch.setattr(queue_server, "lan_addresses", lambda: ["192.168.1.50"])
    assert queue_server.serve(port=8772, profiles_path="ignored.json") == 1
    out = capsys.readouterr().out
    assert "already running" in out.lower()
    assert "Traceback" not in out


# ---------------------------------------------------------------- firewall --


def test_the_firewall_is_checked_and_the_fix_is_printed_not_performed(launcher):
    assert "Get-NetFirewallRule" in launcher
    assert "New-NetFirewallRule" in launcher
    # No silent elevation. A double-clicked icon that raises UAC and rewrites
    # the host firewall is not a thing to ship.
    assert "-Verb RunAs" not in launcher
    assert "RunAs" not in launcher
    assert "AS ADMINISTRATOR" in launcher


def test_a_firewall_check_that_cannot_run_says_so(launcher):
    """'-EA SilentlyContinue' turning a failed check into a clean pass has
    cost this project real time. Three states, never two."""
    body = launcher.split("function Get-FirewallState", 1)[1].split("\nfunction ", 1)[0]
    assert "'unknown'" in body
    assert "'missing'" in body
    assert "'ok'" in body
    assert "Get-Command Get-NetFirewallRule" in body


def test_a_rule_for_the_wrong_port_does_not_count_as_allowed(launcher):
    body = launcher.split("function Get-FirewallState", 1)[1].split("\nfunction ", 1)[0]
    assert "Get-NetFirewallPortFilter" in body
    assert "LocalPort" in body


def test_the_rule_name_and_the_fix_command_match_the_server():
    """A check looking for one name and a fix creating another reads as
    'already allowed' forever."""
    assert queue_server.FIREWALL_RULE_NAME == "Karaoke Queue"
    text = read(LAUNCHER)
    assert "$RuleName = 'Karaoke Queue'" in text
    expected = queue_server.firewall_command(8772)
    built = (
        "New-NetFirewallRule -DisplayName '$RuleName' -Direction Inbound "
        "-Action Allow -Protocol TCP -LocalPort $Port -Profile Any"
    )
    assert built in text
    assert (
        built.replace("$RuleName", "Karaoke Queue").replace("$Port", "8772") == expected
    )


def test_the_server_prints_the_same_command_on_startup(monkeypatch, capsys):
    """A room that never opens the launcher still gets told the fix."""

    class Fake:
        def __init__(self, *a, **k):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(queue_server, "ThreadingHTTPServer", Fake)
    monkeypatch.setattr(queue_server, "lan_addresses", lambda: ["192.168.1.50"])
    assert queue_server.serve(port=8772, profiles_path="ignored.json") == 0
    out = capsys.readouterr().out
    assert queue_server.firewall_command(8772) in out
    assert "administrator" in out.lower()


# ------------------------------------------- the address the QR publishes --


def test_the_launcher_asks_the_server_which_address_phones_can_reach(launcher):
    """Never a second copy of the ranking.

    queue_server puts 192.168.* above 10.* because a VPN tunnel published
    10.5.0.2 into the QR on 2026-09-02. A PowerShell reimplementation is a
    second place for that to be wrong, on a path no test covers.
    """
    assert "--print-address" in launcher
    # below the comment-based help, where the .EXAMPLE legitimately shows one
    body = launcher.split("#>", 1)[1]
    assert "192.168" not in body
    assert "Get-NetIPAddress" not in body
    assert "getaddrinfo" not in body


def test_print_address_prints_the_ranked_best(monkeypatch, capsys):
    monkeypatch.setattr(queue_server, "lan_address", lambda: "192.168.1.50")
    assert queue_server.main(["--print-address"]) == 0
    assert capsys.readouterr().out.strip() == "192.168.1.50"


def test_print_address_honours_a_pinned_host(capsys):
    assert queue_server.main(["--print-address", "--host", "192.168.1.77"]) == 0
    assert capsys.readouterr().out.strip() == "192.168.1.77"


def test_print_address_never_answers_empty(monkeypatch, capsys):
    monkeypatch.setattr(queue_server, "lan_address", lambda: None)
    assert queue_server.main(["--print-address"]) == 0
    assert capsys.readouterr().out.strip() == "localhost"


def test_an_empty_address_never_reaches_a_url(launcher):
    """'http://:8772/' is the shape of the bug that started this script: an
    empty Read-Host answer went straight into a --host flag."""
    assert "IsNullOrWhiteSpace($reachable)" in launcher
    # ${reachable}: and not $reachable: -- PowerShell reads 'name:' as a drive
    # qualifier on a variable and silently produces nonsense.
    assert '"http://${reachable}:${Port}/"' in launcher
    assert '"http://$reachable:' not in launcher


def test_a_read_host_answer_is_never_passed_on_as_an_argument(launcher):
    """The fourth failure of 2026-09-02, pinned."""
    answers = re.findall(r"\$answer\b[^\r\n]*", launcher)
    assert answers, "the firewall offer no longer prompts"
    for line in answers:
        assert "-Port" not in line and "--host" not in line and "-Address" not in line
    # empty means yes, explicitly, rather than falling through to a flag
    assert "$answer -eq ''" in launcher


# ------------------------------------------------- starting and stopping --


def test_the_screen_is_opened_only_after_the_port_answers(launcher):
    assert "Test-PortAnswers" in launcher
    order = launcher.index("Test-PortAnswers $probe $Port")
    assert order < launcher.index("Start-Process $screenUrl")
    assert "/screen" in launcher


def test_the_join_address_is_printed_big(launcher):
    assert "Read this out to the room" in launcher
    assert "'=' * $line.Length" in launcher


def test_nothing_is_left_holding_the_port(launcher):
    """An orphaned python is what makes the NEXT double-click say
    'already running' when nothing is."""
    tail = launcher.split("} finally {", 1)[1]
    assert "Stop-Process" in tail
    # the server shares this console, so closing the window reaches it too
    assert "-NoNewWindow" in launcher


def test_an_argument_containing_a_space_survives_start_process(launcher):
    """A user folder with a space in it would otherwise split one argument
    into two and the server would start with no --profiles at all."""
    assert "$argLine" in launcher
    assert "-ArgumentList $argLine" in launcher
    assert "'\"' + $_ + '\"'" in launcher


def test_singer_memory_lands_outside_the_checkout(launcher):
    """Memory written into the repo shows up in git status and eventually in
    a commit; it also belongs to the machine, not to one of two clones."""
    assert "$env:LOCALAPPDATA" in launcher
    assert "karaoke-profiles.json" in launcher
    assert "'--profiles'" in launcher


def test_the_profiles_file_is_not_in_the_repository():
    assert not (REPO / "karaoke-profiles.json").exists()
    assert "karaoke-profiles.json" in read(REPO / ".gitignore")


# ------------------------------------------------------- the Desktop icon --


def test_the_shortcut_bypasses_the_execution_policy(installer):
    """This machine blocks unsigned .ps1 files; without Bypass the icon dies
    with a red wall about 'running scripts is disabled on this system'."""
    assert "-ExecutionPolicy Bypass" in installer
    assert "-File" in installer
    assert "-NoProfile" in installer


def test_the_shortcut_is_a_real_shortcut(installer):
    assert "WScript.Shell" in installer
    assert "CreateShortcut" in installer
    assert "GetFolderPath('Desktop')" in installer
    assert "$Name + '.lnk'" in installer


def test_running_the_installer_twice_updates_rather_than_duplicates(installer):
    """CreateShortcut opens an existing .lnk for update, so the path is
    computed once and both runs write the same file."""
    assert installer.count("$linkPath = Join-Path $desktop") == 1
    assert installer.count("$shortcut.Save()") == 1
    # one .lnk path, written in place: no timestamp, no counter, no New-Item
    assert not re.search(r"\$linkPath\s*=\s*.*Get-Date", installer)


def test_the_installer_reads_the_shortcut_back(installer):
    """Its own 'created it' message is not evidence -- OneDrive Desktop
    backup can redirect the folder out from under Save()."""
    assert "$check = $shell.CreateShortcut($linkPath)" in installer
    assert "$checkTarget -ne $powershell" in installer
    assert "$checkArgs -ne $arguments" in installer


def test_the_installer_points_at_the_icon_rather_than_describing_it(installer):
    assert "explorer.exe /select," in installer
    assert "$linkPath" in installer


# --------------------------------------------------------------------------
# the check text analysis cannot make: does PowerShell accept these files
# --------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell not installed")
@pytest.mark.parametrize("name", ["start_karaoke.ps1", "install_shortcut.ps1"])
def test_powershell_itself_parses_the_script(name):
    """Every other assertion here reads the file as text; this one runs a parser.

    A `.ps1` that does not parse emits nothing at all -- from a double-clicked
    shortcut that is a window that flashes and vanishes, with no error to
    report and nothing to paste back. The owner would be the first person to
    find out. GitHub's Ubuntu runners ship pwsh, so this runs in CI; it is
    skipped rather than failed where PowerShell is absent, because its absence
    is not a defect in the script.

    Parsing is not execution and pwsh 7 is not Windows PowerShell 5.1, so a
    pass here does not promise the script *runs* -- only that it is syntax the
    language accepts, which is the failure that costs a round trip.
    """
    path = REPO / "tools" / "karaoke_server" / name
    script = (
        "$errors = $null; $tokens = $null; "
        "$null = [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{path}', [ref]$tokens, [ref]$errors); "
        "if ($errors.Count) { foreach ($e in $errors) "
        "{ Write-Output ('line ' + $e.Extent.StartLineNumber + ': ' + $e.Message) }; exit 1 } "
        "else { exit 0 }"
    )
    done = subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, f"{name} does not parse:\n{done.stdout}{done.stderr}"
