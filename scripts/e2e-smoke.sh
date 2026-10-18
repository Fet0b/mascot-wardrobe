#!/usr/bin/env bash
# End-to-end proof: REAL build of wheel and sdist, fresh venv, install the wheel, then run
# the offline flow with the installed CLI. It never passes --live-weather (never --live-weather).
#
# Interpreter: $PYTHON if set, else <repo>/.venv/bin/python if executable, else python3. It needs
# the "build" module (pip install build). Also needs GNU find/stat.
#
# Outputs (dist, venv, working files) go into a fresh run directory created with mktemp inside
# MASCOT_WARDROBE_EVIDENCE_DIR (default: a new mktemp directory outside the repository). Existing
# content of the evidence directory is never modified or deleted, and the run directory is kept.
#
# Test hooks: E2E_RESOLVE_ONLY=1 prints the chosen interpreter and exits;
# E2E_ALLOCATE_ONLY=1 stops right after the run directory is allocated.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
if [ -n "${PYTHON:-}" ]; then
  python="$PYTHON"
elif [ -x "$root/.venv/bin/python" ]; then
  python="$root/.venv/bin/python"
else
  python="python3"
fi
echo "python: $python"
[ "${E2E_RESOLVE_ONLY:-}" = 1 ] && exit 0

if ! "$python" -c 'import build' >/dev/null 2>&1; then
  echo "prerequisite missing: the 'build' module is not available for $python (pip install build)" >&2
  exit 1
fi

evidence="${MASCOT_WARDROBE_EVIDENCE_DIR:-$(mktemp -d)}"
mkdir -p "$evidence"
evidence="$(cd "$evidence" && pwd)"
case "$evidence" in
  "$root" | "$root"/*)
    echo "evidence dir must be outside the repository" >&2
    exit 1
    ;;
esac
echo "evidence dir: $evidence"
run="$(mktemp -d "$evidence/run.XXXXXX")"
echo "run dir: $run"
[ "${E2E_ALLOCATE_ONLY:-}" = 1 ] && exit 0

step() { printf '\n== %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

step "build wheel and sdist"
"$python" -m build --outdir "$run/dist" "$root"
wheel="$(ls "$run"/dist/*.whl)"
sdist="$(ls "$run"/dist/*.tar.gz)"
echo "wheel: $(basename "$wheel")"
echo "sdist: $(basename "$sdist")"

step "fresh venv and wheel install"
"$python" -m venv "$run/venv"
"$run/venv/bin/python" -m pip install --quiet "$wheel"
cli="$run/venv/bin/mascot-wardrobe"
py="$run/venv/bin/python"

work="$run/work"
mkdir -p "$work"
cd "$work"
cp "$root/examples/mascot.png" mascot.png

# Any accidental network use from the CLI would fail against this dead loopback proxy.
export HTTP_PROXY="http://127.0.0.1:9" HTTPS_PROXY="http://127.0.0.1:9" NO_PROXY=""

snapshot() { # hashes and mtimes of every file below $1
  (
    cd "$1"
    find . -type f -print0 | sort -z | xargs -0 sha256sum
    find . -type f -printf '%p %T+\n' | sort
  )
}
json_get() { "$py" -c 'import json,sys; d=json.load(sys.stdin)
for part in sys.argv[1].split("."): d=d[part]
print(d)' "$1"; }

step "mascot-wardrobe --help"
"$cli" --help >/dev/null
"$cli" --help | head -3

step "generate twice, compare hashes and mtimes"
"$cli" generate --mascot mascot.png --id sunny --country BR >/dev/null
catalog="mascot-wardrobe-out/catalog"
manifest="$catalog/manifest.json"
snapshot "$catalog" >"$run/gen1.txt"
sleep 1.1
"$cli" generate --mascot mascot.png --id sunny --country BR >/dev/null
snapshot "$catalog" >"$run/gen2.txt"
cmp "$run/gen1.txt" "$run/gen2.txt" || fail "second generate changed hashes or mtimes"
[ "$(ls "$catalog"/*.png "$catalog"/*.jpg | wc -l)" -eq 18 ] || fail "expected 18 outfit images"
echo "generate is idempotent: $(grep -c . "$run/gen1.txt") lines identical"

step "select one outfit per category, plus Fahrenheit"
check_select() { # temperature unit expected-category
  local out category
  out="$("$cli" select --manifest "$manifest" --country BR --temperature "$1" --unit "$2")"
  category="$(printf '%s' "$out" | json_get category)"
  [ "$category" = "$3" ] || fail "$1 $2 expected $3 got $category"
  printf '%s %s -> %s\n' "$1" "$2" "$category"
}
check_select 3 C very_cold
check_select 10 C cold
check_select 17 C mild
check_select 21 C ambient
check_select 28 C warm
check_select 35 C very_hot
check_select 77 F warm
snapshot "$catalog" >"$run/gen3.txt"
cmp "$run/gen1.txt" "$run/gen3.txt" || fail "select modified the catalog"

step "demo --channel telegram twice"
exports="mascot-wardrobe-out/exports"
first="$("$cli" demo --manifest "$manifest" --country BR --temperature 28 --channel telegram)"
[ "$(printf '%s' "$first" | json_get sync.action)" = exported ] || fail "first demo should export"
[ "$(printf '%s' "$first" | json_get repeat_sync.action)" = unchanged ] || fail "repeat in first demo should be unchanged"
[ "$(printf '%s' "$first" | json_get sync.remote_sync)" = False ] || fail "remote_sync must be false"
snapshot "$exports" >"$run/exp1.txt"
sleep 1.1
second="$("$cli" demo --manifest "$manifest" --country BR --temperature 28 --channel telegram)"
[ "$(printf '%s' "$second" | json_get sync.action)" = unchanged ] || fail "second demo should be unchanged"
snapshot "$exports" >"$run/exp2.txt"
cmp "$run/exp1.txt" "$run/exp2.txt" || fail "second demo changed hashes or mtimes"
cmp "$catalog/sunny-br-warm.jpg" "$exports/telegram/sunny-br-warm/sunny-br-warm.jpg" || fail "export differs from catalog"
echo "demo: first exported, second unchanged, hashes and mtimes identical"

step "result"
echo "e2e smoke passed (no --live-weather, no network used)"
