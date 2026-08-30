#!/bin/sh
# Applies pending Alembic migrations before the app starts, so a container
# restart (e.g. triggered by podman-auto-update pulling a new image) can
# never run new code against an unmigrated schema.
#
# SKIP_MIGRATIONS=true opts a container out of this (used by the ARQ
# worker container, which shares this image/entrypoint with the web
# container): Alembic has no built-in distributed lock, so two containers
# racing "alembic upgrade head" against real pending migrations is a
# genuine risk, not just harmless redundancy. Defaults to running
# migrations (fail-safe) for anything that doesn't opt out.
set -e

if [ "${SKIP_MIGRATIONS:-false}" != "true" ]; then
    echo "Running database migrations..."
    alembic upgrade head
fi

exec "$@"
