#!/usr/bin/env python3
"""
trader_model.py — the model the scheduled review runs on, read from the trader
Hermes profile instead of pinned in this repo.

The review used to pin its models in scripts/_env.sh (claude-opus-4-8 for jobs
A/B/C, claude-sonnet-4-6 for the digest, set 2026-07-22). The pin did its job —
the model behind the trade signals could not change without a commit — and that
same property let it sit two model generations behind the trader profile's own
gateway, with nothing on any surface saying so. Reading the profile keeps "no
silent change" (the profile's config.yaml is versioned in the control plane and
deployed on purpose) and removes the second copy that could drift.

Only the profile's PRIMARY route is used. The review runs `codex exec`, which
has one provider and no fallback chain, so the primary must be openai-codex; any
other provider is an error rather than a guess, because it would mean the CLI
and the profile disagree about who serves the model.

Usage:
    trader_model.py [--config PATH]      prints the model id, exits 0
    TRADER_MODEL=<id> trader_model.py    prints the override instead (step-down)

Runs under system python3 (3.9 on the ops host once), with no PyYAML: it reads
the one top-level `model:` block it needs and nothing else.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".hermes" / "profiles" / "trader" / "config.yaml"
REQUIRED_PROVIDER = "openai-codex"


class ModelResolutionError(Exception):
    """The profile does not say, unambiguously, which codex model to run."""


def _unquote(value: str) -> str:
    value = value.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read_model_block(text: str) -> dict[str, str]:
    """The scalar keys directly under the top-level `model:` key.

    `model_catalog:` and any indented `model:` (auxiliary tasks, TTS voices) are
    other keys and must not match, which is why this looks for the exact line at
    column 0 rather than for the word.
    """
    block: dict[str, str] = {}
    inside = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw[0].isspace():
            if inside:
                break
            inside = raw.rstrip() == "model:"
            continue
        if inside:
            key, sep, value = raw.strip().partition(":")
            if sep and value.strip():
                block[key.strip()] = _unquote(value)
    return block


def resolve_model(config_path: Path, environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    override = env.get("TRADER_MODEL", "").strip()
    if override:
        return override
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModelResolutionError(
            f"cannot read trader profile config {config_path}: {exc}"
        ) from None
    block = read_model_block(text)
    provider = block.get("provider", "")
    model = block.get("default", "")
    if not model:
        raise ModelResolutionError(f"{config_path} has no model.default")
    if provider != REQUIRED_PROVIDER:
        raise ModelResolutionError(
            f"{config_path} model.provider is {provider!r}; the review runs `codex exec`, "
            f"which serves only {REQUIRED_PROVIDER!r}"
        )
    return model


def main(argv: list[str]) -> int:
    path = DEFAULT_CONFIG
    if len(argv) == 2 and argv[0] == "--config":
        path = Path(argv[1])
    elif argv:
        print("usage: trader_model.py [--config PATH]", file=sys.stderr)
        return 2
    try:
        print(resolve_model(path))
    except ModelResolutionError as exc:
        print(f"trader_model: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
