#!/usr/bin/env bash
# install-cron.sh — declare the trading-review Hermes cron job under the `trader`
# profile (iMac only). Versioned, reproducible source for the job; the job itself
# lives in the trader profile's runtime cron store once created.
#
# Mirrors hermes-control-plane's gateway/scripts/cron-install-ops.sh pattern:
#   - installs the --no-agent wrapper into ~/.hermes/scripts/
#   - creates the cron job PAUSED (installing != enabling)
#
# Delivery: --deliver telegram → the trader profile must have TELEGRAM_BOT_TOKEN +
# TELEGRAM_CHAT_ID configured (gateway/env/trader.env on the iMac). Until then,
# create with --deliver local to dry-run into the cron log.
#
# Usage (on iMac, as hermes-runner), after the trader profile is bootstrapped:
#   TRADING_AGENT_REPO=~/workspace/ai-assets/jackhpark-trading-agent \
#     scripts/hermes/install-cron.sh [telegram|local]
# Then enable in the maintenance window:
#   <hermes> --profile trader cron resume trading-review
set -euo pipefail

if [[ "$(whoami)" != "hermes-runner" ]]; then
  echo "install-cron.sh is iMac-only (expects user hermes-runner); got $(whoami)." >&2
  exit 1
fi

DELIVER="${1:-telegram}"
PROFILE="trader"
JOB_NAME="trading-review"
SCHEDULE="30 13 * * 1-5"   # weekdays 13:30 local (= 16:30 ET, 30 min after US close)

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${TRADING_AGENT_REPO:-$(cd "$HERE/../.." && pwd)}"
SCRIPTS_DIR="$HOME/.hermes/scripts"
WRAPPER_SRC="$HERE/trading-review-cron.sh"
WRAPPER_DST="$SCRIPTS_DIR/trading-review-cron.sh"

PY="$HOME/.hermes/hermes-agent/venv/bin/python"
hermes() { "$PY" -m hermes_cli.main "$@"; }

[ -f "$WRAPPER_SRC" ] || { echo "wrapper not found: $WRAPPER_SRC" >&2; exit 1; }

# 1. Install the wrapper into ~/.hermes/scripts/ (Hermes --script resolves here).
mkdir -p "$SCRIPTS_DIR"
cp "$WRAPPER_SRC" "$WRAPPER_DST"
chmod +x "$WRAPPER_DST"
echo "installed wrapper -> $WRAPPER_DST"

# 2. Create the cron job PAUSED, unless it already exists (idempotent).
EXISTING="$(hermes --profile "$PROFILE" cron list 2>/dev/null || true)"
if grep -qF "$JOB_NAME" <<<"$EXISTING"; then
  echo "[skip] cron job '$JOB_NAME' already exists — leaving it untouched."
else
  echo "[create] $JOB_NAME ($SCHEDULE, deliver=$DELIVER, --no-agent)"
  hermes --profile "$PROFILE" cron create "$SCHEDULE" \
    --name "$JOB_NAME" \
    --script "trading-review-cron.sh" \
    --no-agent \
    --deliver "$DELIVER"
  hermes --profile "$PROFILE" cron pause "$JOB_NAME"
  echo "created PAUSED. Resume when ready:"
  echo "  $PY -m hermes_cli.main --profile $PROFILE cron resume $JOB_NAME"
fi

echo
echo "Verify:  $PY -m hermes_cli.main --profile $PROFILE cron list"
