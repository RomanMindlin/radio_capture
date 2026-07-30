"""
Regression tests for the crontab line that start.sh writes to /etc/cron.d.

A crontab command field is not plain shell: an unescaped '%' is turned into a
newline and everything after the first one is fed to the job as stdin. Adding an
unescaped 'date -u +%Y...' to the command once truncated the whole line at
"$(date -u +" — bash died with "unexpected EOF", run_daily_summaries.py never
ran, and because the ">> /proc/1/fd/1" redirect was cut off too, nothing said so.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

START_SH = Path(__file__).resolve().parent.parent / "start.sh"

LOG_TARGET = "/proc/1/fd/1"
CONFIG_PATH = "/config/channels.json"


def _crontab_command() -> str:
    """Return the command field start.sh writes into the cron file."""
    assignments = [
        line
        for line in START_SH.read_text().splitlines()
        if re.match(r"\s*COMMAND=", line)
    ]
    assert assignments, "no COMMAND= assignment found in start.sh"

    script = "\n".join(
        [
            "set -eu",
            "PYTHON_BIN=python",
            f"CONFIG_PATH={CONFIG_PATH}",
            f"LOG_TARGET={LOG_TARGET}",
            *assignments,
            'printf "%s" "$COMMAND"',
        ]
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True
    ).stdout


def _as_cron_runs_it(command: str) -> str:
    """
    Apply Vixie cron's '%' rule to a command field.

    '\\%' becomes a literal '%'; the first unescaped '%' ends the command (the
    remainder is the job's stdin). The return value is what cron hands to $SHELL.
    """
    out = []
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\" and i + 1 < len(command) and command[i + 1] == "%":
            out.append("%")
            i += 2
            continue
        if char == "%":
            break
        out.append(char)
        i += 1
    return "".join(out)


def test_command_survives_cron_percent_handling():
    executed = _as_cron_runs_it(_crontab_command())

    assert "run_daily_summaries.py" in executed, (
        "cron truncates the command before it ever runs run_daily_summaries.py — "
        "escape every '%' as '\\%'"
    )
    assert f"--config {CONFIG_PATH}" in executed
    assert f">> {LOG_TARGET}" in executed, (
        "the output redirect was cut off, so a failing job would be silent"
    )
    assert "date -u +%Y-%m-%dT%H:%M:%SZ" in executed, (
        "the timestamp format must reach the shell intact"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_command_is_valid_shell_after_cron_handling():
    executed = _as_cron_runs_it(_crontab_command())

    result = subprocess.run(
        ["bash", "-n", "-c", executed], capture_output=True, text=True
    )
    assert result.returncode == 0, f"cron would run invalid shell: {result.stderr}"
