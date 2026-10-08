"""Visitors behind the reverse proxy are told apart by the rate limiter.

The API container is reached from the host (Caddy proxies to the published
loopback port). Under rootless networking the container sees such a
connection as coming from the host's own public address, not from 127.0.0.1,
so an allow-list of loopback addresses never matches and every visitor shares
the one address of the host. The start commands therefore trust the forwarded
address of any peer; that is safe only while the port is published on the
loopback interface of the host alone and the proxy overwrites the header."""

import json
import re
from functools import cache
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.rate_limit import limiter as application_limiter

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
FLAG = "--forwarded-allow-ips"
# The address under which the host's connections reach the container.
HOST_ADDRESS = ("188.68.41.169", 50000)
LIMIT = 2


def _start_commands() -> dict[str, list[str]]:
    """The CMD of the dev and of the prod stage, as argument lists."""
    text = DOCKERFILE.read_text()
    stages = re.split(r"^FROM .* AS (\w+)$", text, flags=re.MULTILINE)
    commands: dict[str, list[str]] = {}
    for stage, body in zip(stages[1::2], stages[2::2], strict=True):
        match = re.search(r"^CMD (\[.*?\])\s*$", body, flags=re.MULTILINE | re.DOTALL)
        if match:
            commands[stage] = json.loads(match.group(1).replace("\\\n", ""))
    return commands


def _allowed_peers(command: list[str]) -> str:
    return command[command.index(FLAG) + 1]


_LIMITERS: list[Limiter] = []


@cache
def _app_behind_the_proxy_setting(trusted_hosts: str) -> FastAPI:
    # A limiter of its own per app (a decorator registers its limit at the
    # limiter every time, and slowapi warns about it each time), with the key
    # function of the application's limiter. Built once per setting.
    limiter = Limiter(key_func=application_limiter._key_func)
    _LIMITERS.append(limiter)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.get("/limited")
    @limiter.limit(f"{LIMIT}/minute")
    def limited(request: Request) -> dict[str, str]:
        return {"client": request.client.host if request.client else ""}

    return ProxyHeadersMiddleware(app, trusted_hosts=trusted_hosts)  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _reset_the_counters():
    for limiter in _LIMITERS:
        limiter._storage.reset()


class TestStartCommands:
    @pytest.mark.parametrize("stage", ["dev", "prod"])
    def test_every_start_command_names_the_peers_it_trusts(self, stage):
        assert FLAG in _start_commands()[stage]

    def test_both_stages_found(self):
        assert set(_start_commands()) == {"dev", "prod"}

    def test_both_stages_trust_the_same_peers(self):
        commands = _start_commands()

        assert _allowed_peers(commands["dev"]) == _allowed_peers(commands["prod"])


@pytest.mark.parametrize("stage", ["dev", "prod"])
class TestVisitorsGetTheirOwnLimit:
    def _client(self, stage: str) -> TestClient:
        trusted = _allowed_peers(_start_commands()[stage])
        return TestClient(_app_behind_the_proxy_setting(trusted), client=HOST_ADDRESS)

    @staticmethod
    def _visit(client: TestClient, visitor: str) -> int:
        return client.get("/limited", headers={"X-Forwarded-For": visitor}).status_code

    def test_one_visitor_reaching_the_limit_does_not_block_another(self, stage):
        client = self._client(stage)

        first = [self._visit(client, "203.0.113.9") for _ in range(LIMIT + 1)]
        other = self._visit(client, "198.51.100.7")

        assert first == [200] * LIMIT + [429]
        assert other == 200

    def test_the_application_sees_the_forwarded_address(self, stage):
        client = self._client(stage)

        response = client.get("/limited", headers={"X-Forwarded-For": "203.0.113.9"})

        assert response.json() == {"client": "203.0.113.9"}


class TestWhatTheLoopbackListDoes:
    """Documents the failure the start commands avoid: a list of loopback
    addresses ignores the header, so all visitors share the host's address."""

    def test_a_loopback_only_list_puts_everybody_in_one_bucket(self):
        client = TestClient(
            _app_behind_the_proxy_setting("127.0.0.1"), client=HOST_ADDRESS
        )

        statuses = [
            client.get("/limited", headers={"X-Forwarded-For": visitor}).status_code
            for visitor in ("203.0.113.1", "203.0.113.2", "203.0.113.3")
        ]

        assert statuses == [200, 200, 429]
