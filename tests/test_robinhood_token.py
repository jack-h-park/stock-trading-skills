"""Concurrency and durability tests for scripts/robinhood_token.py.

These never touch Robinhood: `_post_refresh` is stubbed. What they pin is the
part that used to be missing — that two processes refreshing at the same expiry
produce exactly ONE refresh, and that an interrupted write cannot destroy the
stored refresh_token.

Run: python3 -m pytest tests/test_robinhood_token.py
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import robinhood_token as rt  # noqa: E402


def write_store(path, expires_at, refresh="refresh-v1"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "access_token": "access-v1",
        "refresh_token": refresh,
        "expires_in": 706620,
        "expires_at": expires_at,
    }))


def test_valid_token_is_returned_without_refreshing(tmp_path, monkeypatch):
    store = tmp_path / "robinhood.json"
    write_store(store, time.time() + 86400)
    monkeypatch.setattr(rt, "_post_refresh", lambda _: pytest_fail())
    assert rt.get_access_token(store) == "access-v1"


def pytest_fail():
    raise AssertionError("_post_refresh must not be called for a live token")


def test_expiring_token_is_refreshed_and_written_atomically(tmp_path, monkeypatch):
    store = tmp_path / "robinhood.json"
    write_store(store, time.time() + 60)  # inside the 300s buffer
    monkeypatch.setattr(rt, "_post_refresh", lambda _: {
        "access_token": "access-v2", "refresh_token": "refresh-v2", "expires_in": 706620,
    })
    assert rt.get_access_token(store) == "access-v2"
    on_disk = json.loads(store.read_text())
    assert on_disk["access_token"] == "access-v2"
    assert on_disk["refresh_token"] == "refresh-v2"
    assert on_disk["expires_at"] > time.time() + 86400
    assert oct(store.stat().st_mode)[-3:] == "600"
    assert not list(tmp_path.glob("*.tmp.*")), "temp file left behind"


def test_a_failed_write_leaves_the_old_store_intact(tmp_path, monkeypatch):
    """The torn-write case: the process dies partway through persisting."""
    store = tmp_path / "robinhood.json"
    write_store(store, time.time() + 60)
    monkeypatch.setattr(rt, "_post_refresh", lambda _: {
        "access_token": "access-v2", "refresh_token": "refresh-v2", "expires_in": 706620,
    })

    def explode(*_args, **_kwargs):
        raise OSError("disk went away mid-write")

    monkeypatch.setattr(rt, "_write_atomic", explode)
    try:
        rt.get_access_token(store)
    except OSError:
        pass
    survived = json.loads(store.read_text())
    assert survived["refresh_token"] == "refresh-v1", "refresh_token was lost"
    assert not list(tmp_path.glob("*.tmp.*"))


CONCURRENT_DRIVER = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["SCRIPTS"])
import robinhood_token as rt

COUNTER = os.environ["COUNTER"]

def fake_refresh(_tok):
    # Record the attempt, then dawdle so a second process is guaranteed to be
    # waiting on the lock while this one holds it.
    with open(COUNTER, "a") as fh:
        fh.write("refresh\n")
    time.sleep(1.5)
    return {"access_token": "access-v2", "refresh_token": "refresh-v2",
            "expires_in": 706620}

rt._post_refresh = fake_refresh
sys.stdout.write(rt.get_access_token(os.environ["STORE"]))
'''


def test_two_processes_refresh_exactly_once(tmp_path):
    store = tmp_path / "robinhood.json"
    write_store(store, time.time() + 60)
    counter = tmp_path / "refresh-calls.log"
    counter.write_text("")
    driver = tmp_path / "driver.py"
    driver.write_text(CONCURRENT_DRIVER)

    env = {**os.environ, "SCRIPTS": str(SCRIPTS), "STORE": str(store),
           "COUNTER": str(counter)}
    procs = [subprocess.Popen([sys.executable, str(driver)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(2)]
    outs = [p.communicate() for p in procs]

    for (out, err), p in zip(outs, procs):
        assert p.returncode == 0, err
        assert out == "access-v2", (out, err)
    calls = [l for l in counter.read_text().splitlines() if l]
    assert len(calls) == 1, "expected one refresh, got %d" % len(calls)
    assert json.loads(store.read_text())["refresh_token"] == "refresh-v2"
