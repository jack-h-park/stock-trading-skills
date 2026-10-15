"""install-skills.sh: dry run writes nothing, real run is idempotent and ops-host-only,
runtime residue is reported and kept, and no skill reaches for an order tool it should not.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hermes" / "install-skills.sh"
SKILLS = sorted(p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").is_file())


def _run(home: Path, *args: str, role: str | None = None) -> subprocess.CompletedProcess:
    if role is not None:
        (home / ".hermes").mkdir(parents=True, exist_ok=True)
        (home / ".hermes" / "role").write_text(role + "\n")
    env = {**os.environ, "HOME": str(home), "HERMES_REAL_HOME": "",
           "HERMES_HOME_TRADER": str(home / "profile")}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)


def test_dry_run_lists_every_skill_and_writes_nothing(tmp_path):
    res = _run(tmp_path, "--dry-run")
    assert res.returncode == 0, res.stderr
    for name in SKILLS:
        assert f"[add] {name}/SKILL.md" in res.stdout
    assert "[add] DESCRIPTION.md" in res.stdout
    assert "[add] tax-desk/references/db-glossary.md" in res.stdout
    assert not (tmp_path / "profile").exists()


def test_a_real_run_needs_the_ops_role(tmp_path):
    (tmp_path / "profile").mkdir()
    res = _run(tmp_path, role="dev")
    assert res.returncode == 1 and "ops-host-only" in res.stderr
    assert not (tmp_path / "profile" / "skills").exists()


def test_install_is_idempotent_and_keeps_runtime_residue(tmp_path):
    (tmp_path / "profile").mkdir()
    first = _run(tmp_path, role="ops")
    assert first.returncode == 0, first.stderr
    dest = tmp_path / "profile" / "skills" / "trading"
    for name in SKILLS:
        source = ROOT / "skills" / name / "SKILL.md"
        assert (dest / name / "SKILL.md").read_bytes() == source.read_bytes()

    note = dest / "tax-desk" / "references" / "agent-note.md"
    note.write_text("written by the agent")
    second = _run(tmp_path, role="ops")
    assert second.returncode == 0
    assert "[add]" not in second.stdout and "[update]" not in second.stdout
    assert "0 file(s) changed" in second.stdout
    assert "[residue] tax-desk/references/agent-note.md" in second.stdout
    assert note.exists()

    (dest / "log" / "SKILL.md").write_text("stale")
    third = _run(tmp_path, "--dry-run")
    assert "[update] log/SKILL.md" in third.stdout
    assert (dest / "log" / "SKILL.md").read_text() == "stale"   # dry run left it


ORDER_TOOLS = re.compile(
    r"\b(place|cancel|review)_(equity|option|crypto)_order\b|\bexercise_option\b")


def test_research_skills_name_no_order_tool_and_only_the_read_only_server():
    for name in ("tax-desk", "analyst-facts"):
        text = "\n".join(p.read_text() for p in (ROOT / "skills" / name).rglob("*.md"))
        assert not ORDER_TOOLS.search(text), name
        assert "robinhood-orders" not in text, name
        assert "read-only" in text, name


def test_skills_cite_scripts_by_repo_path_and_every_cited_script_exists():
    for md in (ROOT / "skills").rglob("*.md"):
        for rel in re.findall(r"\$REPO/(scripts/[\w./-]+\.(?:py|sh))", md.read_text()):
            assert (ROOT / rel).is_file(), f"{md.name} cites missing {rel}"
        for rel in re.findall(r"jackhpark-stock-trading-skills/(scripts/[\w./-]+\.(?:py|sh))",
                              md.read_text()):
            assert (ROOT / rel).is_file(), f"{md.name} cites missing {rel}"
