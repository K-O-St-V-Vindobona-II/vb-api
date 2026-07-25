python scripts/downsync_prod.py
podman exec -it vb-api bash
exit
alembic Upgrade head
alembic upgrade head
exit
