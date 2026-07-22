#!/usr/bin/env bash
# Portable PATH + CLAUDE_CODE_OAUTH_TOKEN for headless cron.
# The actual token lives outside the repo (chmod 600, not committed here).
# Reuses the same credential file as the briefing publisher — same hermes-runner
# account and Claude subscription. Source this before calling `claude -p`.
#
# Token setup: run `claude setup-token` as hermes-runner and save the output to
# ~/.config/stock-portfolio-briefing/anthropic_oauth_token (chmod 600).

for d in "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin "$HOME/.hermes/node/bin" "$HOME/.npm-global/bin"; do
  [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH";; esac
done
export PATH="$PATH:/usr/bin:/bin:/usr/sbin:/sbin"

TOKEN_FILE="${CLAUDE_TOKEN_FILE:-$HOME/.config/stock-portfolio-briefing/anthropic_oauth_token}"
if [ -f "$TOKEN_FILE" ] && [ -s "$TOKEN_FILE" ]; then
  CLAUDE_CODE_OAUTH_TOKEN="$(cat "$TOKEN_FILE" | tr -d '[:space:]')"
  export CLAUDE_CODE_OAUTH_TOKEN
fi

# Model for every `claude -p` call in this repo. Pinned rather than left to the
# CLI default: without it the model is whatever the logged-in account happens to
# select, so the model behind the trade signals can change with no commit and no
# alert.
#
# The value below is what the account default actually resolved to as of
# 2026-07-22, so pinning it is a no-op. Verified from the modelUsage block in
# logs/cron/*.run.log — NOT from trader-usage.jsonl, whose `model` field was
# mislabelled until the _log_trader_usage fix in this same change.
#
# Override to change tiers, e.g. TRADER_CLAUDE_MODEL=claude-opus-4-8.
export TRADER_CLAUDE_MODEL="${TRADER_CLAUDE_MODEL:-claude-sonnet-4-6}"
