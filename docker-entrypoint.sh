#!/bin/sh
# Applies pending Alembic migrations before the app starts, so a container
# restart (e.g. triggered by podman-auto-update pulling a new image) can
# never run new code against an unmigrated schema.
set -e

echo "Running database migrations..."
alembic upgrade head

exec "$@"
