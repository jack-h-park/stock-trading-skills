#!/usr/bin/env bash
# trading-review-cron.sh — Hermes cron `--no-agent --script` entrypoint.
#
# Hermes runs this on schedule and delivers its STDOUT verbatim to the configured
# channel (Telegram). So: run the heavy review (Claude → Robinhood/Drive, which logs
# to its own run log and emits no stdout), then print only the short digest.
#
# Empty stdout = Hermes stays silent (weekend/holiday/skip). That's intentional.
# Non-empty stdout on failure = Hermes delivers the error alert to Telegram.
#
# Installed to ~/.hermes/profiles/trader/scripts/ by scripts/hermes/install-cron.sh.
# The trading repo location is resolved via $TRADING_AGENT_REPO (default below).

set -uo pipefail

REPO="${TRADING_AGENT_REPO:-$HOME/workspace/ai-assets/jackhpark-trading-agent}"
RUNNER="$REPO/scripts/run-review.sh"
DATE_ISO="$(date +%F)"
RUNLOG="$REPO/logs/cron/${DATE_ISO}.run.log"

# Enrich PATH so python3 is reachable (hermes cron runs with a minimal PATH).
for _d in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  [ -d "$_d" ] && case ":$PATH:" in *":$_d:"*) ;; *) PATH="$_d:$PATH";; esac
done
export PATH="$PATH:/usr/bin:/bin"
unset _d

# Skip on non-NYSE trading days and notify so the skip is visible in Telegram.
if ! python3 "$REPO/scripts/hermes/market-check.py" 2>/dev/null; then
  echo "📅 ${DATE_ISO} 주식 개장일이 아니어서 오늘 trading review는 진행하지 않았습니다."
  exit 0
fi

[ -f "$RUNNER" ] || { echo "⚠️ trading-review $DATE_ISO: runner not found at $RUNNER" >&2; exit 1; }

# Run the review+reconcile. It self-logs to logs/cron/<date>.run.log and emits no
# stdout of its own; on weekends it skips and writes no digest.
bash "$RUNNER"
RC=$?

if [ "$RC" -ne 0 ]; then
  # Extract the most informative error line from the run log.
  ERR="$(grep -v '^=====' "$RUNLOG" 2>/dev/null | tail -3 | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')"
  echo "⚠️ trading-review FAILED $DATE_ISO (rc=$RC) — ${ERR:-see logs/cron/${DATE_ISO}.run.log}"
  exit 0
fi

# Deliver the digest (if the run produced one) — this is the only stdout on success.
DIGEST="$REPO/logs/digest/${DATE_ISO}.md"
[ -f "$DIGEST" ] && cat "$DIGEST"

exit 0
