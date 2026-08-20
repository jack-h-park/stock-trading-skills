#!/usr/bin/env python3
"""
Shared Robinhood OAuth token store access — single-flight refresh, atomic write.

TWO PROCESSES WRITE THIS FILE

  mcp-robinhood-proxy.py    spawned by Hermes per MCP connection, and RESPAWNED
                            on every reconnect (a keepalive timeout is enough)
  _get_robinhood_token.py   called by run-review.sh, i.e. the trading-review
                            cron, 13:30 Mon-Fri

Before this module they each did: read file -> if near expiry, POST refresh ->
`open(w)` + `json.dump`. No lock, no atomic write. Two failure modes, both of
which cost the refresh_token outright and force a manual re-auth:

  1. Race. Robinhood may rotate the refresh_token on use. If both processes
     refresh around the same expiry, the second writes a token derived from a
     refresh_token the first already spent — or simply clobbers the newer file.
  2. Torn write. `open(w)` truncates first. A process killed mid-write leaves a
     truncated file with no refresh_token in it. This is not hypothetical: the
     proxy is a child of the gateway and gets killed on every gateway restart,
     and the trader log carries "Force-killed MCP process ... (robinhood) after
     SIGTERM timeout".

Robinhood issues ~8-day tokens, so the refresh path runs about once a week —
rare enough that it had never executed since the store was created on
2026-08-12, and rare enough that a race would look like a random logout.

Keep this module Python 3.9-safe: the proxy runs under the iMac's system
/usr/bin/python3, not the Hermes venv.
"""

import errno
import fcntl
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

TOKEN_FILE = Path.home() / ".hermes/profiles/trader/mcp-tokens/robinhood.json"
CLIENT_ID = "LtLiNmbs9owbYfWgBlC68Z2VujIPuvGoAiSYr8xW"
TOKEN_ENDPOINT = "https://api.robinhood.com/oauth2/token/"
EXPIRY_BUFFER_SECONDS = 300  # refresh 5 min before actual expiry
LOCK_TIMEOUT_SECONDS = 90    # > the 30s refresh timeout, so a real refresh wins
DEFAULT_EXPIRES_IN = 86400


class TokenError(RuntimeError):
    """Token store could not produce a usable access token."""


def _lock_path(path):
    return path.with_name(path.name + ".lock")


@contextmanager
def _exclusive(path, timeout=LOCK_TIMEOUT_SECONDS):
    """flock the sidecar .lock file, or raise TokenError on timeout.

    The lock is a sidecar rather than the token file itself so the lock is held
    across the atomic replace, which swaps the token file's inode.
    """
    lock = _lock_path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.time() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.time() >= deadline:
                    raise TokenError(
                        "timed out after %ds waiting for %s" % (timeout, lock)
                    )
                time.sleep(0.2)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read(path):
    with open(str(path)) as fh:
        return json.load(fh)


def _write_atomic(path, data):
    """Write 0600 via a same-directory temp file + os.replace.

    Same directory so the replace is a rename within one filesystem, which is
    atomic; a reader therefore sees either the old file or the new one, never a
    half-written one.
    """
    tmp = path.with_name("%s.tmp.%d" % (path.name, os.getpid()))
    fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise


def _needs_refresh(token, now=None):
    now = time.time() if now is None else now
    return now + EXPIRY_BUFFER_SECONDS >= float(token.get("expires_at", 0))


def _post_refresh(refresh_tok):
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
            "client_id": CLIENT_ID,
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get_access_token(token_file=TOKEN_FILE):
    """Return a valid access_token, refreshing under an exclusive lock if due."""
    path = Path(token_file)
    try:
        token = _read(path)
    except FileNotFoundError:
        raise TokenError("Token file not found: %s" % path)
    except ValueError as exc:
        raise TokenError("Token file is not valid JSON (%s): %s" % (path, exc))

    if not _needs_refresh(token):
        return token["access_token"]

    with _exclusive(path):
        # Re-read INSIDE the lock. Another process may have refreshed while we
        # waited — and if Robinhood rotated the refresh_token, ours is now
        # spent, so refreshing again would fail and overwrite a good file.
        token = _read(path)
        if not _needs_refresh(token):
            return token["access_token"]

        try:
            new = _post_refresh(token["refresh_token"])
        except Exception as exc:
            raise TokenError("Token refresh failed: %s" % exc)

        token["access_token"] = new["access_token"]
        if "refresh_token" in new:
            token["refresh_token"] = new["refresh_token"]
        token["expires_in"] = new.get("expires_in", DEFAULT_EXPIRES_IN)
        token["expires_at"] = time.time() + float(
            new.get("expires_in", DEFAULT_EXPIRES_IN)
        )
        _write_atomic(path, token)
        return token["access_token"]


if __name__ == "__main__":
    try:
        sys.stdout.write(get_access_token())
    except TokenError as exc:
        sys.stderr.write("%s\n" % exc)
        sys.exit(1)
