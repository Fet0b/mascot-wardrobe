#!/usr/bin/env bash
# Fail if detect-secrets finds anything not in the audited .secrets.baseline.
#
# Gate: detect-secrets-hook (exits non-zero on new findings). The "detect-secrets scan"
# command is not used as a gate because it rewrites the baseline and exits 0.
#
# Usage: scripts/scan-secrets.sh [REPORT_PATH]
# The optional report holds only file names, line numbers and finding types; it never
# contains secret values.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"
report="${1:-}"

hook="detect-secrets-hook"
if [ -x "$root/.venv/bin/detect-secrets-hook" ]; then
  hook="$root/.venv/bin/detect-secrets-hook"
fi

# Tracked files plus untracked files that are not ignored; skip the baseline itself,
# deleted files and binary artwork.
files=()
while IFS= read -r -d '' name; do
  case "$name" in
    .secrets.baseline | *.png | *.jpg | *.jpeg) continue ;;
  esac
  [ -f "$name" ] && files+=("$name")
done < <(git ls-files -z --cached --others --exclude-standard)

status=0
output="$("$hook" --baseline .secrets.baseline "${files[@]}" 2>&1)" || status=$?

summary="detect-secrets-hook exit status ${status} over ${#files[@]} files"
if [ -n "$report" ]; then
  {
    echo "$summary"
    echo "$output" | sed -E 's/(secret|token|password)[^ ]*[=:] *[^ ]+/\1=<redacted>/Ig'
  } >"$report"
fi

echo "$summary"
if [ "$status" -ne 0 ]; then
  echo "$output" >&2
  echo "secret scan FAILED" >&2
  exit "$status"
fi
echo "secret scan passed"
