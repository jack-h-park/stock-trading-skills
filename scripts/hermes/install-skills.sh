#!/usr/bin/env bash
# install-skills.sh — copy this repo's skills into the Hermes trader profile, under
# the `trading` category: skills/<name>/ -> $HERMES_HOME_TRADER/skills/trading/<name>/.
#
# Until this existed no trading skill was installed at all: the cron prompts read
# skills/*/SKILL.md from the repo, and an interactive session had only the Hermes
# bundled skills. This installs all of them — trade, portfolio-review, reconcile,
# log, tax-desk, analyst-facts — and the category's DESCRIPTION.md.
#
# Nothing else is installed. Skills cite their scripts by repo path
# ($HOME/workspace/ai-assets/jackhpark-stock-trading-skills/scripts/...), resolved
# when the agent runs them, so there is no second copy of any script to drift. A
# skill that needed a script at a fixed path would have to be added here, no later
# than the skill itself.
#
# Idempotent: a file whose bytes already match is left alone, and every file that
# is added or changed is printed. Additive like the control plane's deploys: a file
# in the profile with no counterpart here (a note the agent wrote, a .bak) is
# reported as residue and NOT deleted — whether it carries knowledge is a
# judgement, not a copy step.
#
# A skill is bound when a session starts, so verify a change in a NEW session.
#
# Usage:
#   scripts/hermes/install-skills.sh --dry-run    # print what would change; writes nothing
#   scripts/hermes/install-skills.sh              # ops host only (~/.hermes/role = ops)
# Env: HERMES_HOME_TRADER (default $HOME/.hermes/profiles/trader).
set -euo pipefail

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SRC="$REPO/skills"
DEST_ROOT="${HERMES_HOME_TRADER:-$HOME/.hermes/profiles/trader}"
DEST="$DEST_ROOT/skills/trading"

# Writing into a live profile is the ops host's job; a dry run is safe anywhere.
if [[ "$DRY_RUN" -eq 0 ]]; then
  ROLE_FILE="${HERMES_REAL_HOME:-$HOME}/.hermes/role"
  ROLE=""
  [[ -r "$ROLE_FILE" ]] && ROLE="$(tr -d '[:space:]' < "$ROLE_FILE")"
  if [[ "$ROLE" != "ops" ]]; then
    echo "install-skills.sh writes a live profile and is ops-host-only: $ROLE_FILE must contain 'ops' (found '${ROLE:-<missing>}')." >&2
    echo "  Use --dry-run to see what it would change." >&2
    exit 1
  fi
  [[ -d "$DEST_ROOT" ]] || { echo "trader profile not found at $DEST_ROOT" >&2; exit 1; }
fi

[[ "$DRY_RUN" -eq 1 ]] && echo "(dry run — nothing is written)"
echo "source: $SRC"
echo "target: $DEST"

changed=0
same=0
residue=0

install_file() { # <src> <dst>
  local src="$1" dst="$2" verb
  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
    same=$((same + 1))
    return
  fi
  verb="[update]"; [[ -f "$dst" ]] || verb="[add]"
  echo "$verb ${dst#"$DEST"/}"
  changed=$((changed + 1))
  if [[ "$DRY_RUN" -eq 0 ]]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst.tmp-$$" && mv -f "$dst.tmp-$$" "$dst"
  fi
}

[[ -f "$SRC/DESCRIPTION.md" ]] && install_file "$SRC/DESCRIPTION.md" "$DEST/DESCRIPTION.md"

for skill_dir in "$SRC"/*/; do
  skill_dir="${skill_dir%/}"
  name="$(basename "$skill_dir")"
  [[ -f "$skill_dir/SKILL.md" ]] || { echo "[skip] $name: no SKILL.md"; continue; }
  while IFS= read -r -d '' f; do
    install_file "$f" "$DEST/$name/${f#"$skill_dir"/}"
  done < <(find "$skill_dir" -type f ! -name '.DS_Store' ! -name '*.pyc' -print0 | sort -z)
  if [[ -d "$DEST/$name" ]]; then
    while IFS= read -r -d '' f; do
      rel="${f#"$DEST/$name"/}"
      if [[ ! -f "$skill_dir/$rel" ]]; then
        echo "[residue] $name/$rel (in the profile, not in the repo; left in place)"
        residue=$((residue + 1))
      fi
    done < <(find "$DEST/$name" -type f ! -name '.DS_Store' -print0 | sort -z)
  fi
done

echo "$changed file(s) $([[ "$DRY_RUN" -eq 1 ]] && echo "would change" || echo "changed"), $same unchanged, $residue residue."
[[ "$changed" -gt 0 && "$DRY_RUN" -eq 0 ]] && echo "Skills bind at session start: verify in a NEW trader session."
exit 0
