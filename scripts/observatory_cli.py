#!/usr/bin/env python3
"""Run one Observatory CLI (`pnpm <name> --json`) and classify what came back.

The Observatory (stock-portfolio-observatory) owns the tax engine and the database
built from every broker's exports. Its CLIs are the interface; this repo never opens
its files for tax figures. The contract every CLI follows:

  - `--json` prints exactly one JSON object on stdout;
  - exit 0 = ok, 2 = usage error, 3 = a refresh holds `.refresh.lock` (the database
    is being rebuilt in place and must not be read), 1 = anything else;
  - every failure object is {"ok": false, "error": "...", "schemaVersion": 1}.

A caller never fails because the Observatory did. `run()` returns a source record
that says whether the data is usable and, when it is not, why — so a report can say
"the Observatory was not consulted: refresh in progress" instead of silently
showing fewer windows than exist.

As a command, for an interactive session that needs the right PATH without
rebuilding it by hand:

  python3 scripts/observatory_cli.py tax:scenario --scenario lt-now

prints the CLI's JSON object (or {"ok": false, "error": ...}) and exits 0 when it is
usable, 3 while a refresh holds the lock, 1 otherwise. `--json` is added for you.

Python 3.9 compatible (the ops host's system python).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_REPO = Path.home() / "workspace/code/core/jackhpark-stock-observatory"
SCHEMA_VERSION = 1
TIMEOUT_S = 180


def repo_path() -> Path:
    return Path(os.environ.get("OBSERVATORY_REPO") or DEFAULT_REPO)


def toolchain_env() -> dict[str, str]:
    """PATH the way the Observatory's own cron wrappers build it.

    Each directory goes in FRONT, so the last one listed wins; the app toolchain is
    last because its node is the one the app's native modules were built for."""
    env = dict(os.environ)
    path = env.get("PATH", "")
    home = str(Path.home())
    for d in (f"{home}/.local/bin", "/opt/homebrew/bin", "/usr/local/bin",
              f"{home}/.local/app-toolchain/bin"):
        if os.path.isdir(d) and d not in path.split(":"):
            path = f"{d}:{path}" if path else d
    env["PATH"] = path + ":/usr/bin:/bin:/usr/sbin:/sbin"
    return env


def classify(rc: int, stdout: str, stderr: str = "") -> dict[str, Any]:
    """{"status": "ok"|"unavailable", "reason", "data"} from one CLI run."""
    doc: Any = None
    # One object on stdout is the contract; the last line is tried too, in case a
    # package-manager banner reached stdout ahead of it.
    for candidate in (stdout, (stdout.strip().splitlines() or [""])[-1]):
        try:
            doc = json.loads(candidate) if candidate.strip() else None
            break
        except ValueError:
            doc = None
    if rc == 3:
        return {"status": "unavailable", "reason": "refresh-in-progress", "data": None}
    if rc != 0 or not isinstance(doc, dict) or doc.get("ok") is not True:
        err = doc.get("error") if isinstance(doc, dict) else None
        if not err:
            tail = " ".join(stderr.strip().splitlines()[-2:])[:200]
            err = "no JSON object on stdout" + (f" ({tail})" if tail else "")
        return {"status": "unavailable", "reason": f"rc={rc}: {err}", "data": None}
    if int(doc.get("schemaVersion") or 0) > SCHEMA_VERSION:
        return {"status": "unavailable",
                "reason": f"schemaVersion {doc.get('schemaVersion')} is newer than "
                          f"{SCHEMA_VERSION}; refusing to guess at changed fields",
                "data": None}
    return {"status": "ok", "reason": None, "data": doc}


def run(name: str, args: list[str], repo: Path | None = None,
        pnpm: str = "pnpm") -> dict[str, Any]:
    """Run `pnpm <name> [args] --json` in the Observatory repo; never raises."""
    repo = repo or repo_path()
    if not (repo / "package.json").is_file():
        return {"status": "unavailable", "reason": f"Observatory repo not found at {repo}",
                "data": None}
    try:
        proc = subprocess.run([pnpm, "--silent", name, *args, "--json"], cwd=str(repo),
                              env=toolchain_env(), capture_output=True, text=True,
                              timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"status": "unavailable", "reason": f"could not run pnpm {name}: {e}",
                "data": None}
    return classify(proc.returncode, proc.stdout, proc.stderr)


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        sys.stderr.write("usage: observatory_cli.py <cli-name> [args ...]\n")
        return 2
    args = [a for a in argv[1:] if a != "--json"]
    res = run(argv[0], args)
    if res["status"] == "ok":
        print(json.dumps(res["data"], indent=1))
        return 0
    print(json.dumps({"ok": False, "error": res["reason"], "schemaVersion": SCHEMA_VERSION}))
    return 3 if res["reason"] == "refresh-in-progress" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
