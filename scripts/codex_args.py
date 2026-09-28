#!/usr/bin/env python3
"""
codex_args.py — the `codex exec` argument list for one review job.

This is where the review's safety envelope is written down, so it lives in one
module the tests import rather than in shell arrays they would have to scrape.
Every guarantee below was checked against a live `codex exec` before it was
relied on:

  * TOOLS. Each MCP server gets `enabled_tools`: codex exposes only the listed
    tools to the model, and the server never receives a call for any other (a
    stub server offering place_equity_order was not called, and the model
    reported the tool unavailable). This is the codex counterpart of the
    `--allowedTools` whitelist `claude -p` took. Robinhood is ALSO reached
    through mcp-robinhood-proxy.py --read-only, so a write needs two separate
    failures to get through.
  * APPROVAL. `approval_policy="never"` alone makes codex cancel every MCP call
    ("user cancelled MCP tool call") — there is no one to ask. Each server
    therefore sets `default_tools_approval_mode="approve"`, which is safe only
    because `enabled_tools` has already cut the list to reads.
  * SHELL. codex, unlike the old whitelist, can run shell commands. Under
    `--sandbox workspace-write` with network access off, `git add`/`git commit`
    fail on `.git/index.lock` (Operation not permitted) and `curl` cannot resolve
    a host. So A/B/C still cannot commit or race on the index, and nothing in a
    job can reach Robinhood except through the proxy. MCP servers are separate
    processes outside the sandbox, which is how the proxy reaches the network.
  * CONFIG. `--ignore-user-config` keeps the runtime user's ~/.codex/config.toml
    (models, MCP servers of its own) out of the review; auth still comes from
    CODEX_HOME. `--ephemeral` keeps no session files.

Usage (one argument per output line, for a bash `while read` loop):

    codex_args.py read   --model M --repo R --rh-python P --rh-proxy X \\
                         --sheets-python P --sheets-script S --sa-key K --sheet-id I
    codex_args.py digest --model M --repo R
"""

from __future__ import annotations

import argparse
import json
import sys

# The seven Robinhood reads the review needs. Every one must also clear the
# proxy's own allow-list (tests/test_robinhood_proxy_read_only.py checks both).
ROBINHOOD_READ_TOOLS = (
    "get_accounts",
    "get_portfolio",
    "get_equity_positions",
    "get_equity_quotes",
    "get_equity_historicals",
    "get_equity_orders",
    "get_equity_tradability",
)
SHEETS_READ_TOOLS = ("read_holdings", "read_sheet")


def toml_str(value: str) -> str:
    """A TOML basic string. JSON's escaping is a valid subset for these values."""
    if any(ord(ch) < 0x20 for ch in value):
        raise ValueError(f"control character in config value {value!r}")
    return json.dumps(value)


def toml_list(values: tuple[str, ...] | list[str]) -> str:
    return "[" + ",".join(toml_str(v) for v in values) + "]"


def _config(key: str, value: str) -> list[str]:
    return ["-c", f"{key}={value}"]


def base_args(model: str, repo: str) -> list[str]:
    return [
        "exec",
        "-m",
        model,
        "-C",
        repo,
        "--sandbox",
        "workspace-write",
        "--ephemeral",
        "--ignore-user-config",
        "--json",
        *_config("approval_policy", toml_str("never")),
        *_config("sandbox_workspace_write.network_access", "false"),
    ]


def mcp_args(
    rh_python: str,
    rh_proxy: str,
    sheets_python: str,
    sheets_script: str,
    sa_key: str,
    sheet_id: str,
) -> list[str]:
    rh = "mcp_servers.robinhood"
    gd = "mcp_servers.google-drive"
    env = (
        "{"
        + ",".join(
            [
                f"GOOGLE_APPLICATION_CREDENTIALS={toml_str(sa_key)}",
                f"HOLDINGS_SHEET_ID={toml_str(sheet_id)}",
            ]
        )
        + "}"
    )
    return [
        *_config(f"{rh}.command", toml_str(rh_python)),
        *_config(f"{rh}.args", toml_list([rh_proxy, "--read-only"])),
        *_config(f"{rh}.enabled_tools", toml_list(ROBINHOOD_READ_TOOLS)),
        *_config(f"{rh}.default_tools_approval_mode", toml_str("approve")),
        *_config(f"{gd}.command", toml_str(sheets_python)),
        *_config(f"{gd}.args", toml_list([sheets_script])),
        *_config(f"{gd}.env", env),
        *_config(f"{gd}.enabled_tools", toml_list(SHEETS_READ_TOOLS)),
        *_config(f"{gd}.default_tools_approval_mode", toml_str("approve")),
    ]


def build(argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(prog="codex_args.py")
    parser.add_argument("job", choices=["read", "digest"])
    parser.add_argument("--model", required=True)
    parser.add_argument("--repo", required=True)
    for name in ("rh-python", "rh-proxy", "sheets-python", "sheets-script", "sa-key", "sheet-id"):
        parser.add_argument(f"--{name}", default="")
    ns = parser.parse_args(argv)
    args = base_args(ns.model, ns.repo)
    if ns.job == "read":
        missing = [
            n
            for n in ("rh_python", "rh_proxy", "sheets_python", "sheets_script")
            if not getattr(ns, n)
        ]
        if missing:
            parser.error("read jobs need " + ", ".join("--" + m.replace("_", "-") for m in missing))
        args += mcp_args(
            ns.rh_python, ns.rh_proxy, ns.sheets_python, ns.sheets_script, ns.sa_key, ns.sheet_id
        )
    # The digest formats files A/B/C already wrote. It gets no MCP server at all,
    # which is the codex form of the old DIGEST_ALLOWED list (files + `git status`).
    return args


def main(argv: list[str]) -> int:
    for arg in build(argv):
        if "\n" in arg:
            raise SystemExit(f"codex_args: newline in argument {arg!r}")
        print(arg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
