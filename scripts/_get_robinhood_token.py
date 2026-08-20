#!/usr/bin/env python3
"""
Print a valid Robinhood MCP access_token to stdout, refreshing if it is due.

Thin CLI over robinhood_token.get_access_token — the refresh, the exclusive
lock and the atomic write all live there, shared with mcp-robinhood-proxy.py so
the two writers of the token store cannot race or tear the file. See that
module's docstring for what went wrong without it.

Usage:
    python3 _get_robinhood_token.py
Exit code:
    0 — valid token on stdout (no trailing newline)
    1 — refresh failed (error message on stderr)
"""

import sys

from robinhood_token import TokenError, get_access_token


def main():
    try:
        sys.stdout.write(get_access_token())
    except TokenError as exc:
        sys.stderr.write("%s\n" % exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
