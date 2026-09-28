"""The review's codex safety envelope, as scripts/codex_args.py builds it.

What these pin is the envelope run-review.sh hands to `codex exec`, parsed the
way codex parses it: every `-c key=value` value is read back as TOML, because a
quoting mistake there does not fail loudly — codex would take the literal string
and the MCP server, the tool list or the sandbox flag would silently not apply.

Run: python3 -m pytest tests/test_codex_args.py
"""

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import codex_args as ca  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "mcp_robinhood_proxy", REPO / "scripts" / "mcp-robinhood-proxy.py"
)
assert _spec and _spec.loader
proxy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proxy)

READ = [
    "read",
    "--model",
    "gpt-6-sol",
    "--repo",
    "/srv/review",
    "--rh-python",
    "/usr/bin/python3",
    "--rh-proxy",
    "/srv/review/scripts/mcp-robinhood-proxy.py",
    "--sheets-python",
    "/venv/bin/python",
    "--sheets-script",
    "/srv/review/scripts/mcp-google-sheets.py",
    "--sa-key",
    "/keys/sa.json",
    "--sheet-id",
    "sheet-id_123",
]


def config(args):
    """Every -c override as {key: parsed TOML value}, the way codex reads them."""
    out = {}
    for flag, kv in zip(args, args[1:]):
        if flag == "-c":
            key, _, raw = kv.partition("=")
            out[key] = tomllib.loads(f"v = {raw}")["v"]
    return out


def test_every_override_is_valid_toml():
    for job in (READ, ["digest", "--model", "gpt-6-sol", "--repo", "/srv/review"]):
        args = ca.build(job)
        assert config(args), job[0]


def test_sandbox_and_approval_hold_for_both_jobs():
    for job in (READ, ["digest", "--model", "gpt-6-sol", "--repo", "/srv/review"]):
        args = ca.build(job)
        cfg = config(args)
        assert args[:5] == ["exec", "-m", "gpt-6-sol", "-C", "/srv/review"]
        assert args[args.index("--sandbox") + 1] == "workspace-write"
        assert cfg["sandbox_workspace_write.network_access"] is False
        assert cfg["approval_policy"] == "never"
        assert "--ignore-user-config" in args and "--ephemeral" in args and "--json" in args
        assert "--dangerously-bypass-approvals-and-sandbox" not in args


def test_read_job_enables_exactly_the_review_reads():
    cfg = config(ca.build(READ))
    assert cfg["mcp_servers.robinhood.enabled_tools"] == list(ca.ROBINHOOD_READ_TOOLS)
    assert cfg["mcp_servers.google-drive.enabled_tools"] == list(ca.SHEETS_READ_TOOLS)


def test_no_enabled_tool_is_a_write():
    """The codex list and the proxy floor must agree that everything enabled is a read."""
    for name in ca.ROBINHOOD_READ_TOOLS:
        assert proxy.is_read_tool(name), name
    for name in ca.SHEETS_READ_TOOLS:
        assert name.startswith("read_"), name


def test_auto_approval_is_only_granted_alongside_an_allow_list():
    """`approve` without `enabled_tools` would auto-approve whatever the server offers."""
    cfg = config(ca.build(READ))
    for server in ("robinhood", "google-drive"):
        assert cfg[f"mcp_servers.{server}.default_tools_approval_mode"] == "approve"
        assert cfg[f"mcp_servers.{server}.enabled_tools"], server


def test_robinhood_goes_through_the_read_only_proxy():
    cfg = config(ca.build(READ))
    assert cfg["mcp_servers.robinhood.command"] == "/usr/bin/python3"
    assert cfg["mcp_servers.robinhood.args"] == [
        "/srv/review/scripts/mcp-robinhood-proxy.py",
        "--read-only",
    ]
    assert not any(k.startswith("mcp_servers.robinhood.url") for k in cfg)


def test_sheet_env_reaches_the_server():
    cfg = config(ca.build(READ))
    assert cfg["mcp_servers.google-drive.env"] == {
        "GOOGLE_APPLICATION_CREDENTIALS": "/keys/sa.json",
        "HOLDINGS_SHEET_ID": "sheet-id_123",
    }


def test_digest_gets_no_mcp_server():
    cfg = config(ca.build(["digest", "--model", "gpt-6-sol", "--repo", "/srv/review"]))
    assert not any(k.startswith("mcp_servers.") for k in cfg)


def test_read_job_refuses_to_build_without_its_servers():
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "codex_args.py"),
            "read",
            "--model",
            "m",
            "--repo",
            "/r",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "--rh-python" in result.stderr


def test_awkward_path_survives_the_round_trip():
    tricky = '/Users/a b/"quoted"\\dir/proxy.py'
    cfg = config(ca.mcp_args("/py", tricky, "/py", "/s.py", "k", "id"))
    assert cfg["mcp_servers.robinhood.args"][0] == tricky


def test_cli_prints_one_argument_per_line():
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "codex_args.py"), *READ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert out == ca.build(READ)


def test_review_keeps_its_codex_login_out_of_the_directory_hermes_adopts_from():
    """Hermes adopts a Codex login it finds in ~/.codex when a profile's own breaks.

    Two programs on one refresh-token family log each other out, so the review's
    login must live somewhere Hermes never reads. Resolved with bash, not a regex,
    so a default that expands to ~/.codex is caught too.
    """
    script = (REPO / "scripts" / "run-review.sh").read_text()
    line = next(x for x in script.splitlines() if x.startswith("export CODEX_HOME="))
    resolved = subprocess.run(
        ["bash", "-c", f'HOME=/home/t; unset TRADER_CODEX_HOME; {line}; echo "$CODEX_HOME"'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert resolved.startswith("/home/t/")
    assert resolved.rstrip("/") != "/home/t/.codex"
