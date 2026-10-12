#!/bin/sh
# Dev-tooling helper for the local pre-commit hook (.pre-commit-config.yaml).
# Derives TEST_DATABASE_URL from the container's own DATABASE_URL (same
# credentials, different db name) so pytest never runs against the real
# dev database — see "Tests" in README.md. After the run it checks
# the separate statement and branch minimums (scripts/check_coverage_gate.py).
set -e
export TEST_DATABASE_URL="$(echo "$DATABASE_URL" | sed 's#/[^/]*$#/vb_test#')"
python -m pytest "$@"
# Statements and branches are held to separate minimums; the report to read
# is the JSON file the run above has just written.
exec python scripts/check_coverage_gate.py
