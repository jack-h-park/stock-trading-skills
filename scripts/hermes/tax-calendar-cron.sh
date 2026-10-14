#!/usr/bin/env bash
# tax-calendar-cron.sh — Hermes cron `--no-agent --script` entrypoint for
# scripts/tax_calendar.py (wash-sale windows, the orders that touch them, and
# earnings dates inside planned sales).
#
# Hermes delivers this script's STDOUT verbatim to the trader Telegram topic. The
# notice is awareness, not a decision, which is why it goes there rather than to
# Discord. Silence is the steady state.
#
# SILENT UNLESS THE WINDOW SET CHANGES. A wash-sale window lasts 31 days; reporting
# it on every weekday would put the same lines in the channel twenty times, and
# that is how the one new line gets scrolled past. tax_calendar.py writes a
# `fingerprint` of the set (tickers, sale and end dates, which source reported each,
# buys inside a window, planned-sale conflicts, unavailable sources) that excludes
# what moves on its own (days left, amounts, timestamps). This wrapper keeps the
# same state contract as the Observatory's observatory-refresh-cron.sh: speak when
# the fingerprint changes, say "back to clean" once, otherwise stay quiet.
#
# The full table is always written to logs/tax-calendar/<date>.{json,md}, changed or
# not, so an interactive session reads today's file instead of recomputing.
#
# Exit codes: 0 when the calendar was produced (a source being unavailable is a
# PARTIAL calendar, reported in the message, not a failed run); 1 when the script
# itself failed, so cron-health sees it.
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
  echo "⚠️ tax-calendar $DATE_ISO: configured repo does not exist at $REPO — calendar not built. Set TRADING_AGENT_REPO in the trader profile .env."
  exit 1
fi

# The same interpreter run-review.sh hands tax_guard.py and the proxy: both are
# stdlib-only and Python 3.9 compatible, so the system python3 is enough.
PYTHON="${TRADER_PYTHON:-$(command -v python3 || echo /usr/bin/python3)}"

OUT_DIR="${TAX_CALENDAR_OUT_DIR:-$REPO/logs/tax-calendar}"
JSON="$OUT_DIR/$DATE_ISO.json"
ERR="$(mktemp)"
MESSAGE="$("$PYTHON" "$REPO/scripts/tax_calendar.py" --repo "$REPO" --python "$PYTHON" \
  --date "$DATE_ISO" --out-dir "$OUT_DIR" --print message 2>"$ERR")"
RC=$?

STATE_FILE="${TAX_CALENDAR_STATE:-$HOME/.config/stock-trading-skills/tax-calendar-cron-state}"
PREV=""
FIRST_RUN=1
if [ -r "$STATE_FILE" ]; then
  PREV="$(cat "$STATE_FILE" 2>/dev/null)"
  FIRST_RUN=0
fi

if [ "$RC" -ne 0 ] || [ ! -r "$JSON" ]; then
  FINGERPRINT="failed:${RC}"
  MESSAGE="⚠️ tax-calendar FAILED $DATE_ISO (rc=$RC) — $(tail -c 300 "$ERR" | tr '\n' ' ')"
else
  FINGERPRINT="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["fingerprint"])' "$JSON" 2>/dev/null)"
  [ -n "$FINGERPRINT" ] || FINGERPRINT="unreadable"
fi
rm -f "$ERR"

if [ "$FINGERPRINT" != "$PREV" ]; then
  if [ "$FINGERPRINT" = "clean" ]; then
    # Not on a first run: "back to clean" about windows nobody was told about says nothing.
    [ "$FIRST_RUN" -eq 0 ] && echo "✅ Tax calendar back to clean — no open wash-sale windows or planned-sale conflicts, every source answered."
  else
    printf '%s\n' "$MESSAGE"
  fi
fi

# A state file that cannot be written costs a repeat message, not a missed one.
mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null || true
printf '%s\n' "$FINGERPRINT" > "$STATE_FILE".tmp-$$ 2>/dev/null &&
  mv -f "$STATE_FILE".tmp-$$ "$STATE_FILE" 2>/dev/null || true

[ "$RC" -eq 0 ] || exit 1
exit 0
