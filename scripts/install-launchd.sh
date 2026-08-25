#!/usr/bin/env bash
# install-launchd.sh — (re)install the weekday post-close review job on THIS Mac,
# generating the launchd plist with this machine's actual paths. Idempotent.
# Run it on whichever machine should host the run (e.g. the always-on iMac).
#
# Schedule: weekdays (Mon-Fri) 13:30 local = 16:30 ET (30 min after US close),
# assuming the host is on US Pacific time. If the host is in another timezone,
# adjust the Hour below to 30 min after 16:00 ET in local time.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.jackpark.stock-trading-review"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT="$HERE/run-review.sh"

[ -f "$SCRIPT" ] || { echo "ERROR: $SCRIPT not found — run from the repo's scripts/ dir." >&2; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$HERE/../logs/cron"

# Warn if the host is not on Pacific time (schedule assumes PT).
TZNOW="$(date '+%Z %z')"
case "$TZNOW" in *"-0700"*|*"-0800"*) : ;; *) echo "WARNING: host TZ is '$TZNOW', not Pacific. 13:30 may not be 16:30 ET — adjust the plist Hour." >&2 ;; esac

cal_entry() { printf '    <dict><key>Weekday</key><integer>%s</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>30</integer></dict>\n' "$1"; }

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$SCRIPT</string></array>
  <key>StartCalendarInterval</key>
  <array>
$(cal_entry 1)$(cal_entry 2)$(cal_entry 3)$(cal_entry 4)$(cal_entry 5)  </array>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$HERE/../logs/cron/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$HERE/../logs/cron/launchd.err.log</string>
</dict>
</plist>
PLISTEOF

plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed & loaded: $LABEL (weekdays 13:30 local)"
echo "  script: $SCRIPT"
echo "  plist:  $PLIST"
echo
echo "Test now (runs in the GUI session so Keychain/claude.ai connectors work):"
echo "  launchctl kickstart -k gui/\$(id -u)/$LABEL && sleep 1 && tail -f '$HERE/../logs/cron/'\$(date +%F).run.log"
echo "To remove later:  launchctl bootout gui/\$(id -u)/$LABEL && rm '$PLIST'"
