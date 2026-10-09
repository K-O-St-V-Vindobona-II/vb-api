"""An oversized image upload is refused after reading at most limit + 1 bytes."""

import io
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.services import image_service


def _upload(raw: io.BytesIO) -> tuple[UploadFile, MagicMock]:
    spy = MagicMock(wraps=raw)
    return UploadFile(file=spy, filename="big.jpg", headers=Headers()), spy


def test_reads_at_most_one_byte_past_the_limit() -> None:
    upload, spy = _upload(io.BytesIO(b"x" * (image_service.MAX_FILE_SIZE + 10)))

    with pytest.raises(HTTPException) as exc:
        image_service.upload_image(
            MagicMock(),
            "member",
            uuid.uuid4(),
            upload,
            description=None,
            created_by=None,
            storage=MagicMock(),
        )

    assert exc.value.status_code == 422
    assert "5 MB" in exc.value.detail
    spy.read.assert_called_once_with(image_service.MAX_FILE_SIZE + 1)


def test_a_file_of_exactly_the_limit_is_not_refused_for_its_size() -> None:
    upload, spy = _upload(io.BytesIO(b"x" * image_service.MAX_FILE_SIZE))

    with pytest.raises(HTTPException) as exc:
        image_service.upload_image(
            MagicMock(),
            "member",
            uuid.uuid4(),
            upload,
            description=None,
            created_by=None,
            storage=MagicMock(),
        )

    # The size check passes; the missing content type is what stops it.
    assert "JPEG" in exc.value.detail
    spy.read.assert_called_once_with(image_service.MAX_FILE_SIZE + 1)
