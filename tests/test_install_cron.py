"""The trading-review installer: role-marker gate, required delivery target, paused create.

The behaviour lives in tests/install_cron_test.sh, which drives the real installer
against a throwaway HOME with a stub Hermes interpreter; this only puts it under pytest.
"""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("install_cron_test.sh")


def test_install_cron_behaviour() -> None:
    result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
