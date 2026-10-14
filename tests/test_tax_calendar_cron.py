"""tax-calendar-cron.sh: speak only when the window set's fingerprint changes.

Drives the real wrapper against a throwaway repo whose tax_calendar.py is a stub that
writes the fingerprint the test chooses, so nothing reaches Robinhood or the Observatory.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "scripts" / "hermes" / "tax-calendar-cron.sh"

STUB = textwrap.dedent("""\
    import json, os, sys
    from pathlib import Path
    args = sys.argv[1:]
    out = Path(args[args.index("--out-dir") + 1]); out.mkdir(parents=True, exist_ok=True)
    date = args[args.index("--date") + 1]
    if os.environ.get("STUB_FAIL"):
        sys.stderr.write("stub exploded\\n"); sys.exit(1)
    fp = os.environ["STUB_FP"]
    (out / f"{date}.json").write_text(json.dumps({"fingerprint": fp}))
    print(f"Tax calendar {date}: message for {fp}")
""")


def _env(tmp: Path, **extra: str) -> dict[str, str]:
    repo = tmp / "repo"
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / "scripts" / "tax_calendar.py").write_text(STUB)
    return {**os.environ, "HOME": str(tmp), "TRADING_AGENT_REPO": str(repo),
            "TRADER_PYTHON": sys.executable, "TAX_CALENDAR_STATE": str(tmp / "state"),
            "TAX_CALENDAR_OUT_DIR": str(tmp / "out"), **extra}


def _run(tmp: Path, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(WRAPPER)], capture_output=True, text=True,
                          env=_env(tmp, **extra))


def test_speaks_on_change_and_stays_quiet_on_repeat(tmp_path):
    first = _run(tmp_path, STUB_FP="aaaa")
    assert first.returncode == 0 and "message for aaaa" in first.stdout
    assert _run(tmp_path, STUB_FP="aaaa").stdout == ""
    assert "message for bbbb" in _run(tmp_path, STUB_FP="bbbb").stdout


def test_clean_is_silent_on_first_run_and_announced_once_after_windows(tmp_path):
    assert _run(tmp_path, STUB_FP="clean").stdout == ""
    _run(tmp_path, STUB_FP="aaaa")
    assert "back to clean" in _run(tmp_path, STUB_FP="clean").stdout
    assert _run(tmp_path, STUB_FP="clean").stdout == ""


def test_a_failed_script_exits_1_and_alerts_once(tmp_path):
    first = _run(tmp_path, STUB_FAIL="1", STUB_FP="x")
    assert first.returncode == 1 and "tax-calendar FAILED" in first.stdout
    assert "stub exploded" in first.stdout
    second = _run(tmp_path, STUB_FAIL="1", STUB_FP="x")
    assert second.returncode == 1 and second.stdout == ""
