"""The personal data folder is data/financial-records, not data/stock-management.

The folder was renamed on 2026-11-04 (workspace-governance #212). Until 2026-11-11
the old name still resolved on the ops host; once it stopped, every default here
that still named it failed quietly instead of loudly: the Observatory briefing
dropped out of the digest (its summary file "did not exist"), and shadow-check
reported every settled close as missing because it opened a database path with
nothing at it. Nothing in this repo may name the old folder.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OLD = "data/" + "stock-management"


def test_no_file_names_the_retired_data_folder():
    out = subprocess.run(["git", "grep", "-n", "-F", OLD], cwd=REPO,
                         capture_output=True, text=True)
    assert out.stdout == "", f"retired data folder still referenced:\n{out.stdout}"
