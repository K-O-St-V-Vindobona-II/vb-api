FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (SQLite for migration tool, PostgreSQL client, WeasyPrint rendering)
RUN apt-get update && apt-get install -y \
    sqlite3 libsqlite3-dev \
    libpq-dev \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev libcairo2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

# start command for dev-env (with Live-Reloading)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--no-server-header"]
