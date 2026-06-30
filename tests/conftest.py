import os
from unittest.mock import patch

os.environ["APP_ENVIRONMENT"] = "test"

import bcrypt
import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import storage as storage_module
from app.core.storage import StorageClient, get_storage
from app.db.base import Base
from app.db.database import get_db
from main import app

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _block_all_emails():
    with (
        patch("app.core.mailer.send_reset_email"),
        patch("app.core.mailer._send_to_multiple"),
    ):
        yield


_original_gensalt = bcrypt.gensalt


@pytest.fixture(scope="session", autouse=True)
def _fast_bcrypt():
    bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _original_gensalt(  # noqa: ARG005
        rounds=4, prefix=prefix
    )
    yield
    bcrypt.gensalt = _original_gensalt


@pytest.fixture(scope="session", autouse=True)
def _moto_env():
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        client.create_bucket(Bucket="test-bucket")
        yield


@pytest.fixture(autouse=True)
def mock_s3(_moto_env):
    old_singleton = storage_module._storage
    storage_module._storage = None

    storage = StorageClient(
        endpoint_url="https://s3.amazonaws.com",
        access_key="testing",
        secret_key="testing",
        bucket="test-bucket",
    )
    app.dependency_overrides[get_storage] = lambda: storage
    yield storage
    app.dependency_overrides.pop(get_storage, None)
    storage_module._storage = old_singleton


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="https://testserver") as c:
        yield c
