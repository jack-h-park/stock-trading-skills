#!/usr/bin/env bash
# agentic-execute-cron.sh — Hermes cron `--no-agent --script` entrypoint.
#
# Runs scripts/scheduled_orders.py at 07:05 PT (10:05 ET), the session after the
# afternoon review. It submits the review's auto-eligible buys for approval in the
# Robinhood app, or reports what it would have submitted when
# config/execution.local.json does not switch it on. docs/decisions.md §21.
#
# Hermes delivers STDOUT verbatim. The script always prints one report on a
# trading day; the approval itself arrives as Robinhood's own app notification,
# so this message is awareness and goes to Telegram like the review digest.
#
# Exit 1 when the run could not finish (broker unreadable, account unresolved),
# so cron-health-watchdog sees a failure rather than a quiet ok — the same rule
# trading-review-cron.sh documents under "Exit codes".
#
# Installed to ~/.hermes/profiles/trader/scripts/ by scripts/hermes/install-cron.sh.

set -uo pipefail

REPO="${TRADING_AGENT_REPO:-$HOME/workspace/ai-assets/jackhpark-stock-trading-skills}"
DATE_ISO="$(date +%F)"

for _d in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  [ -d "$_d" ] && case ":$PATH:" in *":$_d:"*) ;; *) PATH="$_d:$PATH";; esac
done
export PATH="$PATH:/usr/bin:/bin"
unset _d

if [ ! -d "$REPO" ]; then
  echo "⚠️ agentic-execute $DATE_ISO: configured repo does not exist at $REPO — nothing submitted. Set TRADING_AGENT_REPO in the trader profile .env to the current checkout path."
  exit 1
fi

# Same contract and the same crash-vs-closed distinction as trading-review-cron.sh,
# with the opposite default: when the calendar check errors, the review runs anyway
# because a skipped review loses a day, but an order run stays put, because
# submitting on a day that turns out to be closed is the costlier mistake.
MARKET_CHECK_ERR="$(mktemp)"
python3 "$REPO/scripts/hermes/market-check.py" 2>"$MARKET_CHECK_ERR"
MARKET_CHECK_RC=$?
if [ "$MARKET_CHECK_RC" -ne 0 ]; then
  if [ -s "$MARKET_CHECK_ERR" ]; then
    echo "⚠️ agentic-execute $DATE_ISO: market-check.py errored (rc=$MARKET_CHECK_RC) — nothing submitted. $(head -c 300 "$MARKET_CHECK_ERR" | tr '\n' ' ')"
    rm -f "$MARKET_CHECK_ERR"
    exit 1
  fi
  rm -f "$MARKET_CHECK_ERR"
  exit 0  # not a trading day; the review already said so
fi
rm -f "$MARKET_CHECK_ERR"

# Pull first: the review that wrote the decision record ran from this checkout, but
# a policy or guardrail change merged since then should bind this run too.
git -C "$REPO" pull --ff-only --quiet >/dev/null 2>&1 || true

mkdir -p "$REPO/logs/cron"
python3 "$REPO/scripts/scheduled_orders.py" --repo "$REPO" \
  2>>"$REPO/logs/cron/${DATE_ISO}.execute.log"
exit $?
