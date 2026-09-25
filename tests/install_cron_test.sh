#!/usr/bin/env bash
# Behaviour test for scripts/hermes/install-cron.sh.
#
# Runs the real installer against a throwaway $HOME whose Hermes interpreter is a
# stub that records every call and keeps a tiny job table, so nothing touches a real
# profile. It pins the four things the installer must do on any ops host:
#   1. gate on the ~/.hermes/role marker, never on the account name;
#   2. refuse to guess a delivery target;
#   3. create every job PAUSED, and notice when a pause did not take;
#   4. carry no private chat id in the tree.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$HERE/../scripts/hermes/install-cron.sh"
JOBS=(trading-review)
FAIL=0
ACCOUNT="$(id -un)"   # expanded up front: a substitution inside a check's arguments would clobber $?

check() { # <description> <condition-exit-status>
  if [[ "$2" -eq 0 ]]; then echo "ok   - $1"; else echo "FAIL - $1"; FAIL=1; fi
}

new_home() {
  TEST_HOME="$(mktemp -d)"
  mkdir -p "$TEST_HOME/.hermes/hermes-agent/venv/bin"
  export FAKE_LOG="$TEST_HOME/calls.log" FAKE_STATE="$TEST_HOME/jobs.txt"
  : > "$FAKE_LOG"; : > "$FAKE_STATE"
  cat > "$TEST_HOME/.hermes/hermes-agent/venv/bin/python" <<'STUB'
#!/usr/bin/env bash
# stand-in for `python -m hermes_cli.main [--profile P] <args>`
shift 2
[[ "${1:-}" == "--profile" ]] && shift 2
echo "$*" >> "$FAKE_LOG"
case "${1:-} ${2:-}" in
  "cron list")
    while read -r name state; do
      printf '  0123abcd%s [%s]\n    Name:      %s\n    Schedule:  x\n\n' "${#name}" "$state" "$name"
    done < "$FAKE_STATE" ;;
  "cron create")
    name=""; prev=""
    for a in "$@"; do [[ "$prev" == "--name" ]] && name="$a"; prev="$a"; done
    echo "$name active" >> "$FAKE_STATE" ;;
  "cron pause")
    [[ -n "${FAKE_PAUSE_NOOP:-}" ]] || sed -i.bak "s/^$3 active/$3 paused/" "$FAKE_STATE" ;;
esac
exit 0
STUB
  chmod +x "$TEST_HOME/.hermes/hermes-agent/venv/bin/python"
}

run_installer() { HOME="$TEST_HOME" HERMES_REAL_HOME="" "$@" "$INSTALLER" ${ARGS[@]+"${ARGS[@]}"} >"$TEST_HOME/out.log" 2>&1; }

set_role() { mkdir -p "$TEST_HOME/.hermes"; printf '%s\n' "$1" > "$TEST_HOME/.hermes/role"; }
created() { grep -c '^cron create' "$FAKE_LOG"; }

unset TRADER_CRON_DELIVER

# 1. the role marker gates the host, the account name does not
new_home; ARGS=(local)
run_installer env; rc=$?
[[ $rc -ne 0 && "$(created)" -eq 0 ]]; check "no role marker: refuses, creates nothing" $?
grep -q "ops-host-only" "$TEST_HOME/out.log"; check "no role marker: message names the marker" $?

new_home; ARGS=(local); set_role imac
run_installer env; rc=$?
[[ $rc -ne 0 && "$(created)" -eq 0 ]]; check "role other than ops: refuses" $?

new_home; ARGS=(local); set_role ops
run_installer env; rc=$?
[[ $rc -eq 0 && "$(created)" -eq "${#JOBS[@]}" ]]; check "role ops under account '$ACCOUNT': installs" $?

# 2. the delivery target is required, from an argument or the environment
new_home; ARGS=(); set_role ops
run_installer env; rc=$?
[[ $rc -ne 0 && "$(created)" -eq 0 ]]; check "no target: refuses, creates nothing" $?

new_home; ARGS=(); set_role ops
run_installer env TRADER_CRON_DELIVER=telegram:1:2; rc=$?
[[ $rc -eq 0 ]] && ! grep '^cron create' "$FAKE_LOG" | grep -Evq -- '--deliver telegram:1:2( |$)'
check "TRADER_CRON_DELIVER supplies the target" $?

new_home; ARGS=(local); set_role ops
run_installer env TRADER_CRON_DELIVER=telegram:1:2; rc=$?
[[ $rc -eq 0 ]] && ! grep '^cron create' "$FAKE_LOG" | grep -Evq -- '--deliver local( |$)'
check "an argument wins over TRADER_CRON_DELIVER" $?

# 3. every job ends paused; a pause that did not take is an error
new_home; ARGS=(local); set_role ops
run_installer env; rc=$?
[[ $rc -eq 0 ]] && ! grep -q ' active$' "$FAKE_STATE" && [[ "$(wc -l < "$FAKE_STATE")" -eq "${#JOBS[@]}" ]]
check "every job is left paused" $?

run_installer env; rc=$?
[[ $rc -eq 0 && "$(created)" -eq "${#JOBS[@]}" ]]; check "second run: paused jobs are found, not created again" $?

new_home; ARGS=(local); set_role ops
run_installer env FAKE_PAUSE_NOOP=1; rc=$?
[[ $rc -ne 0 ]] && grep -q "not paused" "$TEST_HOME/out.log"; check "a pause that exits 0 without effect fails the run" $?

# 4. no chat id in the tree
! grep -rEq 'telegram:-?[0-9]{6,}' "$INSTALLER"; check "installer carries no chat id" $?

exit "$FAIL"
