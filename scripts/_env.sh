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
# Prompts A/B/C do the analytical work: live Robinhood MCP reads, dip-signal
# computation, ranking candidate trades, and reconciling broker holdings against
# the manual sheet. That is multi-step quantitative reasoning over money, so it
# runs on the top tier.
#
# This was claude-sonnet-4-6 (the inherited account default) until 2026-07-22.
export TRADER_CLAUDE_MODEL="${TRADER_CLAUDE_MODEL:-claude-opus-4-8}"

# The digest/commit step (prompt D) formats files the analytical jobs already
# wrote and calls no MCP tool, so it does not need the top tier. Kept on the
# previous model — raising it would spend Opus on string formatting.
export TRADER_DIGEST_MODEL="${TRADER_DIGEST_MODEL:-claude-sonnet-4-6}"
