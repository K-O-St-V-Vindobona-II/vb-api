FROM python:3.12-slim AS base

WORKDIR /app

# Shared runtime deps: WeasyPrint (PDF export) rendering libs + Postgres client
# (pg_dump/pg_restore, used by app/services/backup_service.py and dev scripts).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*


FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.lock .
RUN pip install --no-cache-dir --prefix=/install -r requirements.lock


FROM base AS dev

# Dev-only build headers: libpq-dev (compiling DB-related deps),
# libsqlite3-dev/sqlite3 (scripts/sqlite2pg.py, local SQLite inspection),
# libffi-dev (compiling cryptography/cffi-based deps from source).
RUN apt-get update && apt-get install -y \
    sqlite3 libsqlite3-dev \
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

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/')"]

CMD ["gunicorn", "main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "120", \
     "--access-logfile", "-"]
