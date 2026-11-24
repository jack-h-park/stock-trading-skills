"""What the trading-review cron wrapper prints, which Hermes delivers to Telegram.

Since 2026-11-18 (docs/decisions.md §24) a normal day sends the Agentic message to
Discord and prints nothing, which Hermes treats as silent. Stdout still has to carry
the Agentic message when Discord cannot take it, so the proposals are never lost.
"""
from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path

WRAPPER = Path(__file__).resolve().parent.parent / "scripts" / "hermes" / "trading-review-cron.sh"
AGENTIC_TEXT = "**Hermes Trader — Agentic**\nproposals here\n"


def _run(tmp_path: Path, sender_rc: int | None, pinned: str | None = None,
         extra_env: dict | None = None) -> subprocess.CompletedProcess:
    today = datetime.date.today().isoformat()
    repo = tmp_path / "repo"
    (repo / "scripts" / "hermes").mkdir(parents=True)
    (repo / "scripts" / "hermes" / "market-check.py").write_text("import sys; sys.exit(0)\n")
    # The runner writes today's Agentic message, and a Telegram digest of the kind an
    # older run left behind: the wrapper must not deliver that file any more.
    (repo / "scripts" / "run-review.sh").write_text(
        "#!/bin/bash\n"
        f'mkdir -p "{repo}/logs/digest" "{repo}/logs/cron"\n'
        f"printf '%s' '{AGENTIC_TEXT}' > \"{repo}/logs/digest/{today}.agentic.md\"\n"
        f"echo 'US accounts: stale' > \"{repo}/logs/digest/{today}.md\"\n"
        f"echo 'A 0 ok' > \"{repo}/logs/cron/{today}.status\"\n"
        f"command -v codex > \"{tmp_path}/codex-used\"\n"
    )
    home = tmp_path / "home"
    home.mkdir()
    if pinned is not None:
        bindir = home / ".local/share/codex-trader-review-cli" / pinned / "node_modules/.bin"
        bindir.mkdir(parents=True)
        (bindir / "codex").write_text("#!/bin/sh\n")
        (bindir / "codex").chmod(0o755)
    env_file = tmp_path / "trader.env"
    if sender_rc is not None:
        sender = home / "workspace/ai-assets/jackhpark-hermes-control-plane/gateway/scripts"
        sender.mkdir(parents=True)
        (sender / "send_discord.py").write_text(
            f"import sys; sys.stdin.read(); sys.exit({sender_rc})\n")
        env_file.write_text("DISCORD_BOT_TOKEN=fake\nDISCORD_HOME_CHANNEL=1\n")
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "TRADING_AGENT_REPO": str(repo),
           "TRADER_PROFILE_ENV": str(env_file), **(extra_env or {})}
    proc = subprocess.run(["bash", str(WRAPPER)], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def _codex_used(tmp_path: Path) -> str:
    return (tmp_path / "codex-used").read_text().strip()


def test_a_normal_day_prints_nothing_for_telegram(tmp_path):
    assert _run(tmp_path, sender_rc=0).stdout == ""


def test_the_agentic_message_falls_back_to_telegram_when_discord_fails(tmp_path):
    out = _run(tmp_path, sender_rc=1).stdout
    assert "could not be sent to Discord" in out
    assert "proposals here" in out
    assert "US accounts: stale" not in out


def test_the_agentic_message_falls_back_when_there_are_no_discord_credentials(tmp_path):
    out = _run(tmp_path, sender_rc=None).stdout
    assert "proposals here" in out
    assert "US accounts: stale" not in out


# The review's codex CLI is pinned in this wrapper (2026-11-18). The pin lived only in
# the deployed copy at first, where the next copy of the repo file would have erased it.
def test_the_review_runs_the_pinned_codex_when_it_is_installed(tmp_path):
    _run(tmp_path, sender_rc=0, pinned="0.169.1")
    assert _codex_used(tmp_path).endswith(
        "codex-trader-review-cli/0.169.1/node_modules/.bin/codex")


def test_the_pinned_version_can_be_moved_from_the_profile_env(tmp_path):
    _run(tmp_path, sender_rc=0, pinned="0.170.0",
         extra_env={"TRADER_REVIEW_CODEX_VERSION": "0.170.0"})
    assert "codex-trader-review-cli/0.170.0/" in _codex_used(tmp_path)


def test_a_host_without_the_pinned_install_keeps_the_path_codex(tmp_path):
    proc = _run(tmp_path, sender_rc=0)
    assert "codex-trader-review-cli" not in (tmp_path / "codex-used").read_text()
    assert proc.stderr == ""


def test_a_configured_version_that_is_not_installed_says_so(tmp_path):
    proc = _run(tmp_path, sender_rc=0, pinned="0.169.1",
                extra_env={"TRADER_REVIEW_CODEX_VERSION": "9.9.9"})
    assert "codex-trader-review-cli" not in (tmp_path / "codex-used").read_text()
    assert "TRADER_REVIEW_CODEX_VERSION=9.9.9 has no codex" in proc.stderr
