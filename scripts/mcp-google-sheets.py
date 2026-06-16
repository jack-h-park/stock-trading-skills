#!/usr/bin/env python3
"""
Minimal Google Sheets MCP server — service-account auth, read-only.

Exposes two tools to Hermes trader agent:
  read_sheet(spreadsheet_id, range)  — read any range as rows
  read_holdings()                    — convenience: read the full holdings sheet

Auth: GOOGLE_APPLICATION_CREDENTIALS env var pointing at the service account JSON.
Run:  GOOGLE_APPLICATION_CREDENTIALS=... python mcp-google-sheets.py
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any

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


# Holdings sheet config — matches config/holdings-sheet.md
HOLDINGS_SHEET_ID = "<US_HOLDINGS_SHEET_ID>"
HOLDINGS_DEFAULT_RANGE = "A:Z"

# ── MCP server ────────────────────────────────────────────────────────────────

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

server = Server("google-sheets")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="read_sheet",
            description=(
                "Read a range from a Google Sheet the service account has access to. "
                "Returns rows as a JSON array of arrays."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "spreadsheet_id": {
                        "type": "string",
                        "description": "Google Sheets spreadsheet ID (from the URL)",
                    },
                    "range": {
                        "type": "string",
                        "description": "A1 notation range, e.g. 'Sheet1!A1:E50' or 'A:Z'",
                        "default": "A:Z",
                    },
                },
                "required": ["spreadsheet_id"],
            },
        ),
        types.Tool(
            name="read_holdings",
            description=(
                "Read the full holdings sheet (미국 주식 보유 현황 및 수익률). "
                "Returns all rows as a JSON array of arrays. No arguments needed."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "read_sheet":
            sid = arguments["spreadsheet_id"]
            rng = arguments.get("range", "A:Z")
            rows = await asyncio.get_event_loop().run_in_executor(
                None, _read_range, sid, rng
            )
            return [types.TextContent(type="text", text=json.dumps(rows, ensure_ascii=False))]

        elif name == "read_holdings":
            rows = await asyncio.get_event_loop().run_in_executor(
                None, _read_range, HOLDINGS_SHEET_ID, HOLDINGS_DEFAULT_RANGE
            )
            return [types.TextContent(type="text", text=json.dumps(rows, ensure_ascii=False))]

        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as exc:
        logging.exception("tool error")
        return [types.TextContent(type="text", text=f"Error: {exc}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    asyncio.run(main())
