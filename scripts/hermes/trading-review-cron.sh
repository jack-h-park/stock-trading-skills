#!/usr/bin/env bash
# trading-review-cron.sh — Hermes cron `--no-agent --script` entrypoint.
#
# Hermes runs this on schedule and delivers its STDOUT verbatim to the configured
# channel (Telegram). So: run the heavy review (Claude → Robinhood/Drive, which logs
# to its own run log and produces no stdout), then print only the short digest.
#
# Empty stdout = Hermes stays silent (weekend/holiday/skip). That's intentional.
#
# Installed to ~/.hermes/scripts/ by scripts/hermes/install-cron.sh. The trading
# repo location is resolved via $TRADING_AGENT_REPO (default below).

set -uo pipefail

REPO="${TRADING_AGENT_REPO:-$HOME/workspace/ai-assets/jackhpark-trading-agent}"
RUNNER="$REPO/scripts/run-review.sh"

[ -f "$RUNNER" ] || { echo "trading-review-cron: runner not found at $RUNNER" >&2; exit 1; }

# Run the review+reconcile. It self-logs to logs/cron/<date>.run.log and emits no
# stdout of its own; on weekends it skips and writes no digest.
bash "$RUNNER"

# Deliver the digest (if the run produced one) — this is the only stdout.
DIGEST="$REPO/logs/digest/$(date +%F).md"
[ -f "$DIGEST" ] && cat "$DIGEST"

exit 0
