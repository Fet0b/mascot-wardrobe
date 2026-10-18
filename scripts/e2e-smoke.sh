#!/usr/bin/env bash
# End-to-end proof: REAL build of wheel and sdist, fresh venv, install the wheel, then run
# the offline flow with the installed CLI. It never passes --live-weather (never --live-weather).
#
# Outputs (dist, venv, working files) go to MASCOT_WARDROBE_EVIDENCE_DIR, default a new mktemp
# directory outside the repository. Needs: python3 with the "build" module, GNU find/stat.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
python="${PYTHON:-python3}"
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

step() { printf '\n== %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

step "build wheel and sdist"
"$python" -m build --outdir "$evidence/dist" "$root"
wheel="$(ls "$evidence"/dist/*.whl)"
sdist="$(ls "$evidence"/dist/*.tar.gz)"
echo "wheel: $(basename "$wheel")"
echo "sdist: $(basename "$sdist")"

step "fresh venv and wheel install"
"$python" -m venv "$evidence/venv"
"$evidence/venv/bin/python" -m pip install --quiet "$wheel"
cli="$evidence/venv/bin/mascot-wardrobe"
py="$evidence/venv/bin/python"

work="$evidence/work"
rm -rf "$work"
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
snapshot "$catalog" >"$evidence/gen1.txt"
sleep 1.1
"$cli" generate --mascot mascot.png --id sunny --country BR >/dev/null
snapshot "$catalog" >"$evidence/gen2.txt"
cmp "$evidence/gen1.txt" "$evidence/gen2.txt" || fail "second generate changed hashes or mtimes"
[ "$(ls "$catalog"/*.png "$catalog"/*.jpg | wc -l)" -eq 18 ] || fail "expected 18 outfit images"
echo "generate is idempotent: $(grep -c . "$evidence/gen1.txt") lines identical"

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
snapshot "$catalog" >"$evidence/gen3.txt"
cmp "$evidence/gen1.txt" "$evidence/gen3.txt" || fail "select modified the catalog"

step "demo --channel telegram twice"
exports="mascot-wardrobe-out/exports"
first="$("$cli" demo --manifest "$manifest" --country BR --temperature 28 --channel telegram)"
[ "$(printf '%s' "$first" | json_get sync.action)" = exported ] || fail "first demo should export"
[ "$(printf '%s' "$first" | json_get repeat_sync.action)" = unchanged ] || fail "repeat in first demo should be unchanged"
[ "$(printf '%s' "$first" | json_get sync.remote_sync)" = False ] || fail "remote_sync must be false"
snapshot "$exports" >"$evidence/exp1.txt"
sleep 1.1
second="$("$cli" demo --manifest "$manifest" --country BR --temperature 28 --channel telegram)"
[ "$(printf '%s' "$second" | json_get sync.action)" = unchanged ] || fail "second demo should be unchanged"
snapshot "$exports" >"$evidence/exp2.txt"
cmp "$evidence/exp1.txt" "$evidence/exp2.txt" || fail "second demo changed hashes or mtimes"
cmp "$catalog/sunny-br-warm.jpg" "$exports/telegram/sunny-br-warm/sunny-br-warm.jpg" || fail "export differs from catalog"
echo "demo: first exported, second unchanged, hashes and mtimes identical"

step "result"
echo "e2e smoke passed (no --live-weather, no network used)"
