FROM python:3.14-slim AS base

WORKDIR /app

# Shared runtime deps: WeasyPrint (PDF export) rendering libs + Postgres client
# (pg_dump/pg_restore, used by app/services/backup_service.py and dev scripts).
# postgresql-client is pinned via the official PGDG repo instead of Debian's
# bundled version, so pg_dump/pg_restore always match (or exceed) the Postgres
# server's major version - required for the client tools to safely dump from
# and restore into that server across a major-version upgrade.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 \
    curl ca-certificates gnupg postgresql-common \
    && sh /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*


FROM python:3.14-slim AS builder

WORKDIR /build

COPY requirements.lock .
RUN pip install --no-cache-dir --prefix=/install -r requirements.lock


FROM base AS dev

# Dev-only build headers: libpq-dev (compiling DB-related deps),
# libffi-dev (compiling cryptography/cffi-based deps from source).
RUN apt-get update && apt-get install -y \
    libpq-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--no-server-header"]


FROM base AS prod

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --no-create-home app

COPY --from=builder /install /usr/local
COPY --chown=app:app . .

RUN chmod +x docker-entrypoint.sh

# WeasyPrint (PDF export) triggers Fontconfig's cache init at import time
# for every worker; the app user has no writable $HOME (--no-create-home
# above), so point XDG's cache dir at /tmp instead of creating one.
# XDG_CACHE_HOME alone isn't enough: empirically, Fontconfig's own early
# init still probes a $HOME-based cache path (~/.fontconfig) before
# falling back to the XDG one, logging "No writable cache directories"
# even though the XDG path itself works fine. Redirecting HOME itself to
# /tmp (already world-writable, no real home directory needed) covers
# that path too, without creating one for the app user.
ENV HOME=/tmp
ENV XDG_CACHE_HOME=/tmp

# The container filesystem is mounted read-only in production/QA/test
# (only /tmp is a writable tmpfs, see the deploy repo's Quadlet files).
# Python silently skips writing __pycache__/*.pyc when it can't - not a
# crash risk - but disabling it outright avoids relying on that fallback
# and keeps every process attempt to touch the read-only source tree out
# of the picture entirely.
ENV PYTHONDONTWRITEBYTECODE=1

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/')"]

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--worker-tmp-dir", "/tmp", \
     "--access-logfile", "-", \
     "--no-control-socket"]
# --no-control-socket: this feature (gunicorn >= 25.1.0) is for gunicornc,
# a CLI tool for runtime worker management - unused here (Podman/systemd
# own the container lifecycle instead). Without this flag, gunicorn tries
# to create $HOME/.gunicorn/gunicorn.ctl by default, which fails with a
# permission error on every start: the app user above is created with
# --no-create-home, so its $HOME (/home/app) was never actually created.
# --graceful-timeout: gunicorn's own default (30s) made explicit - the
# time in-flight requests get to finish on shutdown before a worker is
# force-killed. Matched by the Quadlet's TimeoutStopSec so systemd never
# sends SIGKILL before gunicorn's own grace period has run out.
# --worker-tmp-dir: every gunicorn worker creates a small heartbeat file
# here on start (gunicorn.workers.base.Worker, inherited by
# UvicornWorker) - required for the worker to boot at all, not optional.
# Pointed explicitly at /tmp (the container's writable tmpfs mount in
# production/QA/test) instead of relying on the implicit system-temp-dir
# default; gunicorn's own docs recommend a memory-backed filesystem here
# to avoid os.fchmod blocking on a disk-backed one.
