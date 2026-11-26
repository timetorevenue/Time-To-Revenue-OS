#!/usr/bin/env bash
# Runs the Core Python suite with an isolated HOME so no test can touch real user state.
# Usage: scripts/run_core_tests.sh [extra pytest args]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="$(mktemp -d)"
trap 'rm -rf "$HOME_DIR"' EXIT
cd "$ROOT"
if [ "$#" -gt 0 ] && { [ -e "$1" ] || [ "${1#-}" = "$1" ]; }; then TARGETS=(); else TARGETS=(tests dashboard/backend); fi
HOME="$HOME_DIR" PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider -n "${TTROS_TEST_WORKERS:-4}" "${TARGETS[@]}" "$@"
