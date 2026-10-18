#!/usr/bin/env bash
# Fail if detect-secrets finds anything not in the audited .secrets.baseline.
#
# Gate: detect-secrets-hook (exits non-zero on new findings). The "detect-secrets scan"
# command is not used as a gate because it rewrites the baseline and exits 0.
#
# Usage: scripts/scan-secrets.sh [REPORT_PATH]
# The optional report holds only the status, exit code, file and finding counts, and one
# "type at file:line" entry per finding. It never contains raw hook output or context lines;
# the full hook output goes to the terminal (stderr) only.
# SCAN_ROOT overrides the repository to scan (used by the tests).
set -euo pipefail

root="${SCAN_ROOT:-$(git rev-parse --show-toplevel)}"
cd "$root"
report="${1:-}"

hook="detect-secrets-hook"
tools="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -x "$tools/.venv/bin/detect-secrets-hook" ]; then
  hook="$tools/.venv/bin/detect-secrets-hook"
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
  # Keep only the hook's "Secret Type:" and "Location:" values; everything else is dropped.
  findings="$(printf '%s\n' "$output" | awk '
    /^Secret Type:/ { sub(/^Secret Type: */, ""); type = $0 }
    /^Location:/ { sub(/^Location: */, ""); print type " at " $0 }')"
  count=0
  if [ -n "$findings" ]; then count="$(printf '%s\n' "$findings" | wc -l)"; fi
  {
    if [ "$status" -eq 0 ]; then echo "status: passed"; else echo "status: failed"; fi
    echo "exit code: ${status}"
    echo "files: ${#files[@]}"
    echo "findings: ${count}"
    if [ -n "$findings" ]; then printf '%s\n' "$findings"; fi
  } >"$report"
fi

echo "$summary"
if [ "$status" -ne 0 ]; then
  echo "$output" >&2
  echo "secret scan FAILED" >&2
  exit "$status"
fi
echo "secret scan passed"
