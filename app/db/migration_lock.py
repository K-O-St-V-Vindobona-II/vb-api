"""Serialises schema migrations across processes.

The web container migrates at start, and the downsync job (or a person) can run
`alembic upgrade head` while another migration is still running. Alembic takes
no lock of its own, so two runs would both read the same current revision and
apply the same steps. Every run holds this PostgreSQL advisory lock for the
length of its migration transaction; a second run waits, then sees the first
run's result.
"""

MIGRATION_LOCK_KEY = 4_182_734_069
