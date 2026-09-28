#!/usr/bin/env bash
# Portable PATH for headless cron: launchd and Hermes cron scripts start with a
# minimal PATH, and codex, git and python3 live in per-user or Homebrew prefixes.
#
# This file used to also export CLAUDE_CODE_OAUTH_TOKEN and pin the review's
# models (claude-opus-4-8 for jobs A/B/C, claude-sonnet-4-6 for the digest, set
# 2026-07-22). The review now runs `codex exec`, authenticated by codex's own
# login (`codex login`, stored under CODEX_HOME), and takes its model from the
# trader Hermes profile through scripts/trader_model.py. A pin here would be a
# second copy of that choice, and the second copy is what drifted: the review
# ran two model generations behind the profile's gateway with nothing saying so.
# To step down for one run, set TRADER_MODEL (and TRADER_DIGEST_MODEL) instead.

for d in "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin "$HOME/.hermes/node/bin" "$HOME/.npm-global/bin"; do
  [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH";; esac
done
export PATH="$PATH:/usr/bin:/bin:/usr/sbin:/sbin"
