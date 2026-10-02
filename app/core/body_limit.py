"""Request body limits, enforced before anything is parsed or buffered.

Multipart parsing spools every uploaded file to disk and the handlers then
read it into memory, so a size check inside a handler comes after the cost has
been paid. The limits below are the largest that any route accepts: the
biggest upload (an archive file, 10 MiB) plus the multipart framing, and a
small bound for everything else (JSON and forms).
"""

from typing import TYPE_CHECKING

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_UPLOAD_BODY_BYTES = 11 * 1024 * 1024
MAX_JSON_BODY_BYTES = 1024 * 1024
_BODYLESS_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "DELETE"})
_TOO_LARGE_DETAIL = "Die Anfrage ist zu groß."


class BodyTooLargeError(Exception):
    """Raised inside the receive channel when a streamed body passes the limit."""


def _counting_receive(receive: Receive, limit: int) -> Receive:
    """Wrap `receive` so that reading more than `limit` body bytes raises."""
    received = 0

    async def counting_receive() -> Message:
        nonlocal received
        message = await receive()
        if message["type"] != "http.request":
            return message
        received += len(message.get("body", b""))
        if received > limit:
            raise BodyTooLargeError
        return message

    return counting_receive


class MaxBodySizeMiddleware:
    """Answers 413 for a request body above the limit of its content type.

    A declared Content-Length is checked before the application runs; a body
    without one (chunked) is counted while the application reads it.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_upload_bytes: int = MAX_UPLOAD_BODY_BYTES,
        max_json_bytes: int = MAX_JSON_BODY_BYTES,
    ) -> None:
        self.app = app
        self.max_upload_bytes = max_upload_bytes
        self.max_json_bytes = max_json_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in _BODYLESS_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        limit = self._limit_for(headers)
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            await self._reject(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            response_started = response_started or (
                message["type"] == "http.response.start"
            )
            await send(message)

        try:
            await self.app(scope, _counting_receive(receive, limit), tracking_send)
        except BodyTooLargeError:
            if response_started:
                raise
            await self._reject(scope, receive, send)

    def _limit_for(self, headers: Headers) -> int:
        content_type = headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            return self.max_upload_bytes
        return self.max_json_bytes

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"detail": _TOO_LARGE_DETAIL}, status_code=413)
        await response(scope, receive, send)
