"""The test session must not inherit an object store endpoint from the shell:
the mocked S3 lets requests to a custom host through to the real network."""

import os

from app.core.config import get_settings


def test_no_object_store_endpoint_leaks_into_the_session():
    assert "S3_ENDPOINT_URL" not in os.environ
    assert "S3_PUBLIC_ENDPOINT_URL" not in os.environ
    assert get_settings().s3_endpoint_url is None
