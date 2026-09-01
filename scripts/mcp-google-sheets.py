#!/usr/bin/env python3
"""
Minimal Google Sheets MCP server — service-account auth, read-only.

Exposes two tools to the Hermes trader agent:
  read_sheet(spreadsheet_id, range)  — read any range as rows
  read_holdings()                    — convenience: read the full holdings sheet

Auth: GOOGLE_APPLICATION_CREDENTIALS env var pointing at the service account JSON.
Run:  GOOGLE_APPLICATION_CREDENTIALS=... python mcp-google-sheets.py

SDK NOTE (2026-08-19): this targets the `mcp` 2.x server API — `MCPServer` with
a `@server.tool()` decorator that derives the input schema from the signature.
The original version used `mcp.server.Server` with `@server.list_tools()` /
`@server.call_tool()`; in 2.x `Server` is the low-level transport object and has
neither decorator, so the module raised AttributeError at import. It never
started once — Hermes logged "MCP server 'google-drive' failed initial
connection after 3 attempts, parking" and carried on without these tools.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any

from mcp.server import MCPServer

# ── Google Sheets client ──────────────────────────────────────────────────────


def _sheets_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS not set")
    creds = service_account.Credentials.from_service_account_file(
        creds_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _read_range(spreadsheet_id: str, range_: str) -> list[list[Any]]:
    svc = _sheets_service()
    result = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_)
        .execute()
    )
    return result.get("values", [])


async def _read_range_async(spreadsheet_id: str, range_: str) -> list[list[Any]]:
    """googleapiclient is blocking — keep it off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(
        None, _read_range, spreadsheet_id, range_
    )


# Holdings sheet config — matches config/holdings-sheet.md
#
# The real fileId lives in config/accounts.local.md, which is gitignored because
# this repo is public. This file is not, so it can only carry the placeholder —
# and it carried it as a literal string, which is what read_holdings then asked
# Google for. Every call 404'd, and the sheet reconcile reported "could not run"
# on 2026-08-24, 08-25, 08-28 and 08-31 without anyone reading it as a config
# fault. Injected by scripts/run-review.sh at launch instead.
#
# Agent-facing placeholders elsewhere are resolved by the agent reading
# accounts.local.md, as that file instructs. An MCP server is a process, not an
# agent: it cannot read and resolve, so it has to be told.
HOLDINGS_SHEET_ID = os.environ.get("HOLDINGS_SHEET_ID", "").strip()

# Name the tab. A bare "A:Z" reads whichever tab is FIRST, and on this workbook
# that is "[양식] 미실현수익 정리" — a two-row blank form whose only populated
# cells are #DIV/0! and #N/A. The 86 real positions, Chase and Fidelity and
# Merrill among them, sit in the second tab, which the observatory's
# publish:us-sheet step rewrites on every refresh.
#
# So the reconcile was not merely blocked by the 404 above: once that was fixed
# it would have read the empty form and reported, with conviction, that the
# sheet has no positions. Both faults had to go for it to see anything, and the
# second is the one that would have survived looking fixed.
HOLDINGS_TAB = os.environ.get("HOLDINGS_TAB", "미실현수익 정리 (자동)").strip()
HOLDINGS_DEFAULT_RANGE = f"'{HOLDINGS_TAB}'!A:Z" if HOLDINGS_TAB else "A:Z"

# ── MCP server ────────────────────────────────────────────────────────────────

server = MCPServer("google-sheets")


@server.tool(
    description=(
        "Read a range from a Google Sheet the service account has access to. "
        "Returns rows as a JSON array of arrays."
    )
)
async def read_sheet(spreadsheet_id: str, range: str = "A:Z") -> str:
    """Read `range` (A1 notation, e.g. 'Sheet1!A1:E50') from `spreadsheet_id`.

    The parameter is named `range` — shadowing the builtin inside this function
    only — because that is the wire name the tool has always exposed.
    """
    try:
        rows = await _read_range_async(spreadsheet_id, range)
        return json.dumps(rows, ensure_ascii=False)
    except Exception as exc:  # surfaced to the agent, not swallowed
        logging.exception("read_sheet failed")
        return f"Error: {exc}"


@server.tool(
    description=(
        "Read the full holdings sheet (미국 주식 보유 현황 및 수익률). "
        "Returns all rows as a JSON array of arrays. No arguments needed."
    )
)
async def read_holdings() -> str:
    """Read the whole holdings sheet."""
    # Say which knob is unset rather than returning Google's 404. The 404 is what
    # made this look like a permissions or sharing problem for four review days.
    if not HOLDINGS_SHEET_ID or HOLDINGS_SHEET_ID.startswith("<"):
        return (
            "Error: HOLDINGS_SHEET_ID is not configured for this server "
            "(got %r). It is injected by scripts/run-review.sh from the "
            "<US_HOLDINGS_SHEET_ID> row of config/accounts.local.md; the sheet "
            "itself is fine. Report this as a configuration fault, and do not "
            "retry with a guessed id." % (HOLDINGS_SHEET_ID or None,)
        )
    try:
        rows = await _read_range_async(HOLDINGS_SHEET_ID, HOLDINGS_DEFAULT_RANGE)
        return json.dumps(rows, ensure_ascii=False)
    except Exception as exc:
        logging.exception("read_holdings failed")
        return f"Error: {exc}"


if __name__ == "__main__":
    # stderr only: stdout is the JSON-RPC channel.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    server.run("stdio")
