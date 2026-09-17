#!/usr/bin/env bash
# Local verification — everything that can be checked without glasses, a Jetson
# or CI quota. Wire it to git with:  git config core.hooksPath .githooks
#
#   ./scripts/check.sh          # full run
#   ./scripts/check.sh --fast   # skip the slower ZMQ round-trip tests
set -uo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"
FAILED=0
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$1"; FAILED=1; }

step "shell scripts (bash -n)"
for f in scripts/*.sh .githooks/*; do
    [ -f "$f" ] || continue
    bash -n "$f" && echo "  ok  $f" || fail "$f"
done

step "python syntax"
$PY -m compileall -q src examples tests > /dev/null && echo "  ok  src, examples, tests" || fail "compileall"

step "package imports"
$PY -c "
import aria_arm64_bridge as a
from aria_arm64_bridge import protocol, observer, bridge, mock_receiver
print('  ok  aria-arm64-bridge', a.__version__, '->', ', '.join(a.__all__))
" || fail "package import"

step "C++ (only if already configured)"
if [ -x build/cpp/ariabridge_tests ]; then
    cmake --build build/cpp -j"$(nproc)" > /dev/null 2>&1 \
        && ./build/cpp/ariabridge_tests --unit \
        || fail "libariabridge unit tests"
else
    echo "  skip  not configured — ./scripts/test_cpp_consumer.sh builds it"
fi

step "tests"
PYTEST_ARGS=(-q -m "not hardware")
[ "${1:-}" = "--fast" ] && PYTEST_ARGS+=(-k "not zmq and not observer")
$PY -m pytest "${PYTEST_ARGS[@]}" || fail "pytest"

if [ "$FAILED" -eq 0 ]; then
    printf '\n\033[32mALL CHECKS PASSED\033[0m\n'
else
    printf '\n\033[31mCHECKS FAILED\033[0m\n'
fi
exit $FAILED
