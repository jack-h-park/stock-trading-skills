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

DIGEST="$REPO/logs/digest/${DATE_ISO}.md"
STATUS="$REPO/logs/cron/${DATE_ISO}.status"

# Name the phases that actually failed, from the runner's status file. Reading the
# tail of the run log instead lands on the LAST job's result JSON — normally the
# digest step, which usually succeeds — so the alert used to announce a failure
# while quoting a success payload.
if [ "$RC" -ne 0 ]; then
  NAMES="A=signals B=reconcile C=overview D=digest"
  FAILED=""
  while read -r lbl prc reason; do
    [ -z "${lbl:-}" ] && continue
    [ "${prc:-0}" -eq 0 ] 2>/dev/null && continue
    for pair in $NAMES; do
      [ "${pair%%=*}" = "$lbl" ] && lbl="${pair#*=}" && break
    done
    FAILED="${FAILED:+$FAILED; }${lbl} — ${reason}"
  done < "$STATUS" 2>/dev/null

  if [ -z "$FAILED" ]; then
    FAILED="see logs/cron/${DATE_ISO}.run.log"
  fi

  # A partial failure still leaves real work on disk. Suppressing the digest
  # because one phase died threw away the proposals the surviving phases had
  # already produced and paid for, so lead with the warning and deliver anyway.
  if [ -f "$DIGEST" ]; then
    echo "⚠️ trading-review PARTIAL $DATE_ISO — ${FAILED}"
    echo "Digest below is built from the phases that did finish; anything owned by a failed phase is missing."
    echo
    cat "$DIGEST"
  else
    echo "⚠️ trading-review FAILED $DATE_ISO (rc=$RC) — ${FAILED}"
  fi
  exit 0
fi

# Deliver the digest (if the run produced one) — this is the only stdout on success.
[ -f "$DIGEST" ] && cat "$DIGEST"

exit 0
