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
AGENTIC="$REPO/logs/digest/${DATE_ISO}.agentic.md"
STATUS="$REPO/logs/cron/${DATE_ISO}.status"

# The Agentic proposals are the only part Jack can act on, so they go to Discord —
# interaction-required — while the read-only cross-account context stays on
# Telegram via this script's stdout. Hermes delivers stdout to one channel only,
# so the second destination is sent from here.
#
# Falls back to stdout when the profile has no Discord credentials: two clearly
# separated Telegram messages beat losing the proposals entirely, and the split
# starts working the moment the .env gains the keys — no redeploy.

# Hermes cron --script subprocesses do not inherit the profile .env, and the
# repo's _env.sh carries only the model pins and the Claude token. Pull the two
# Discord keys out by name rather than sourcing the whole file, which would also
# re-set TRADING_AGENT_REPO and PATH from a file this script has already resolved.
PROFILE_ENV="${TRADER_PROFILE_ENV:-$HOME/.hermes/profiles/trader/.env}"
if [ -r "$PROFILE_ENV" ]; then
  for _k in DISCORD_BOT_TOKEN DISCORD_HOME_CHANNEL; do
    _v="$(grep -E "^${_k}=" "$PROFILE_ENV" | tail -1 | cut -d= -f2-)"
    [ -n "$_v" ] && export "$_k=$_v"
  done
  unset _k _v
fi

# The Korean line used to be printed above this digest. It is gone: the morning
# briefing already sends that exact block on this same channel, so repeating it
# here made the afternoon message open with something Jack had read hours
# earlier. What this message is FOR is the cross-account US total, which the
# briefing does not carry — it is equity-only, priced at a different close, and
# says nothing about cash or crypto. The first line now says so itself.
#
# Kept as history rather than deleted quietly: the reason the line existed was
# to stop the US figure below being read as the whole portfolio, and that job is
# now done by the wording of the US line instead of by a duplicate.

deliver_agentic() {
  [ -f "$AGENTIC" ] || return 0
  local sender="$HOME/workspace/ai-assets/jackhpark-hermes-control-plane/gateway/scripts/send_discord.py"
  if [ -n "${DISCORD_BOT_TOKEN:-}" ] && [ -n "${DISCORD_HOME_CHANNEL:-}" ] && [ -f "$sender" ]; then
    if python3 "$sender" --bot-token "$DISCORD_BOT_TOKEN" --channel-id "$DISCORD_HOME_CHANNEL" \
         < "$AGENTIC" >/dev/null 2>&1; then
      return 0
    fi
    # A failed send must not swallow the proposals — fall through to stdout.
    echo "⚠️ Agentic proposals could not be sent to Discord; delivering here instead."
  fi
  cat "$AGENTIC"
  echo
  echo "———"
  echo
}

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
  if [ -f "$DIGEST" ] || [ -f "$AGENTIC" ]; then
    echo "⚠️ trading-review PARTIAL $DATE_ISO — ${FAILED}"
    echo "What follows is built from the phases that did finish; anything owned by a failed phase is missing."
    echo
    deliver_agentic
    [ -f "$DIGEST" ] && cat "$DIGEST"
  else
    echo "⚠️ trading-review FAILED $DATE_ISO (rc=$RC) — ${FAILED}"
  fi
  exit 0
fi

# Actionable proposals to Discord; the read-only cross-account digest is this
# script's stdout, which Hermes delivers to Telegram.
deliver_agentic
[ -f "$DIGEST" ] && cat "$DIGEST"

exit 0
