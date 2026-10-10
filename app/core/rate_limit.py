from slowapi import Limiter
from slowapi.util import get_remote_address

# The key is the client address the ASGI server resolves from X-Forwarded-For.
# The start commands (see the Dockerfile) trust that header from any peer,
# which is only sound because the container port is published on the host's
# loopback interface alone and the reverse proxy replaces the header. The
# counters live in this process's memory, so with N gunicorn workers a limit
# is effectively up to N times the configured value and starts from zero after
# a restart.
limiter = Limiter(key_func=get_remote_address)
