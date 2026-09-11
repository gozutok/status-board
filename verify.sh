#!/bin/sh
# Everything the workflow will run, run here first — and once more under the
# conditions the runner has and this machine does not.
#
# test_leg failed in CI while passing here, because it copied a .cache that exists
# on this machine and not at that point on the runner. Passing locally is not the
# same as passing, so the second pass hides what the runner would not have.
set -e
cd "$(dirname "$0")"

echo "--- yerelde, olduğu gibi"
python3 check.py fetch.py render.py
for t in test_fr24 test_shapes test_leg test_edges; do
  printf "  %-12s " "$t"; python3 "$t.py" >/dev/null 2>&1 && echo PASS || { echo FAIL; python3 "$t.py"; exit 1; }
done

echo "--- runner gibi: cache yok, saat UTC"
hidden=""
if [ -d .cache ]; then hidden=".cache.verify-hidden"; mv .cache "$hidden"; fi
ok=0
TZ=UTC python3 check.py fetch.py render.py >/dev/null || ok=1
for t in test_fr24 test_shapes test_leg test_edges; do
  printf "  %-12s " "$t"
  if TZ=UTC python3 "$t.py" >/dev/null 2>&1; then echo PASS; else echo FAIL; ok=1; fi
done
[ -n "$hidden" ] && mv "$hidden" .cache
[ "$ok" = 0 ] || { echo "runner koşullarında düştü"; exit 1; }
echo "--- hepsi geçti"
