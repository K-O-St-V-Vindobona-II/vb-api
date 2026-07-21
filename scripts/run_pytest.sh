#!/bin/sh
# Dev-tooling helper for the local pre-commit hook (.pre-commit-config.yaml).
# Derives TEST_DATABASE_URL from the container's own DATABASE_URL (same
# credentials, different db name) so pytest never runs against the real
# dev database — see "Running tests" in README.md.
set -e
export TEST_DATABASE_URL="$(echo "$DATABASE_URL" | sed 's#/[^/]*$#/vb_test#')"
exec python -m pytest "$@"
