"""scripts/trader_model.py: the review's model comes from the trader profile.

The profile config below is shaped like the live one (a top-level `model:` block
among many other keys, an `auxiliary:` block whose tasks carry their own
`model:` lines, a `model_catalog:` key). The parser must pick the top-level
block and nothing else.

Run: python3 -m pytest tests/test_trader_model.py
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import trader_model as tm  # noqa: E402

PROFILE = """\
agent:
  reasoning_effort: medium
auxiliary:
  approval:
    model: ''
    provider: auto
fallback_providers:
  - provider: anthropic
    model: claude-sonnet-5
model:
  default: gpt-6-sol
  provider: openai-codex
  base_url: https://chatgpt.com/backend-api/codex
model_catalog:
  enabled: true
tts:
  openai:
    model: gpt-4o-mini-tts
"""


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_reads_the_top_level_primary(tmp_path):
    assert tm.resolve_model(write(tmp_path, PROFILE), environ={}) == "gpt-6-sol"


def test_quoted_values_and_comments(tmp_path):
    text = PROFILE.replace("default: gpt-6-sol", 'default: "gpt-6-luna"  # step-down')
    assert tm.resolve_model(write(tmp_path, text), environ={}) == "gpt-6-luna"


def test_override_wins_without_reading_the_file(tmp_path):
    assert (
        tm.resolve_model(tmp_path / "missing.yaml", environ={"TRADER_MODEL": "gpt-6-luna"})
        == "gpt-6-luna"
    )


def test_a_non_codex_primary_is_refused(tmp_path):
    """codex exec serves one provider; a profile on another one must stop the run."""
    text = PROFILE.replace("provider: openai-codex", "provider: anthropic")
    with pytest.raises(tm.ModelResolutionError, match="openai-codex"):
        tm.resolve_model(write(tmp_path, text), environ={})


def test_missing_default_is_refused(tmp_path):
    text = PROFILE.replace("  default: gpt-6-sol\n", "")
    with pytest.raises(tm.ModelResolutionError, match="model.default"):
        tm.resolve_model(write(tmp_path, text), environ={})


def test_nested_model_keys_are_not_the_primary(tmp_path):
    text = "auxiliary:\n  s2k:\n    model: gpt-6-luna\n    provider: openai-codex\n"
    with pytest.raises(tm.ModelResolutionError):
        tm.resolve_model(write(tmp_path, text), environ={})


def test_unreadable_config_is_refused(tmp_path):
    with pytest.raises(tm.ModelResolutionError, match="cannot read"):
        tm.resolve_model(tmp_path / "missing.yaml", environ={})


def test_cli_prints_the_model_or_fails_with_a_reason(tmp_path):
    script = str(REPO / "scripts" / "trader_model.py")
    ok = subprocess.run(
        [sys.executable, script, "--config", str(write(tmp_path, PROFILE))],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert ok.returncode == 0 and ok.stdout.strip() == "gpt-6-sol"
    bad = subprocess.run(
        [sys.executable, script, "--config", str(tmp_path / "nope.yaml")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert bad.returncode == 1 and bad.stdout == "" and "cannot read" in bad.stderr
