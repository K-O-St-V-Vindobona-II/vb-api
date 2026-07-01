import os
from io import BytesIO
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from PIL import Image as PILImage

PILImage.MAX_IMAGE_PIXELS = 100_000_000

MAX_PRESIGNED_EXPIRY = 86400
MIN_PRESIGNED_EXPIRY = 60

S3_PATH_STANDESDB_IMAGES: str = os.environ.get(
    "S3_PATH_STANDESDB_IMAGES", "standesdb/images"
)
S3_PATH_STANDESDB_CACHE: str = os.environ.get(
    "S3_PATH_STANDESDB_CACHE", "standesdb/cache"
)
S3_PATH_ARCHIVE_STORE: str = os.environ.get("S3_PATH_ARCHIVE_STORE", "archive/store")
S3_PATH_ARCHIVE_CACHE: str = os.environ.get("S3_PATH_ARCHIVE_CACHE", "archive/cache")
S3_PATH_DB_BACKUPS: str = os.environ.get("S3_PATH_DB_BACKUPS", "db-backups")


class StorageClient:
    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        public_endpoint_url: str | None = None,
        region: str = "us-east-1",
        presigned_expiry: int = 900,
    ) -> None:
        self._bucket = bucket
        self._presigned_expiry = presigned_expiry

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

        public_url = public_endpoint_url or endpoint_url
        if public_url != endpoint_url:
            self._public_client = boto3.client(
                "s3",
                endpoint_url=public_url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=Config(signature_version="s3v4"),
            )
        else:
            self._public_client = self._client

    def upload(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except ClientError as exc:
            msg = f"S3 upload failed for key '{key}'"
            raise RuntimeError(msg) from exc

    def download(self, key: str) -> bytes:
        response = self._client.get_object(
            Bucket=self._bucket,
            Key=key,
        )
        return response["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(
                Bucket=self._bucket,
                Key=key,
            )
        except ClientError:
            return False
        return True

    def generate_presigned_url(
        self,
        key: str,
        expires_in: int | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        ttl = expires_in or self._presigned_expiry
        ttl = max(MIN_PRESIGNED_EXPIRY, min(ttl, MAX_PRESIGNED_EXPIRY))

        params: dict[str, str] = {
            "Bucket": self._bucket,
            "Key": key,
        }
        if filename:
            safe = quote(filename, safe=".-_")
            params["ResponseContentDisposition"] = (
                f"attachment; filename*=UTF-8''{safe}"
            )
        if content_type:
            params["ResponseContentType"] = content_type
        return self._public_client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=ttl,
        )

    def delete(self, key: str) -> None:
        # Reserved for explicit, deliberate operations only (e.g. backup retention
        # cleanup in backup_service.py). Application code must NEVER call this to
        # remove archive/standesdb objects — see archive_service.py and
        # image_service.py for the intentional S3 retention policy.
        self._client.delete_object(
            Bucket=self._bucket,
            Key=key,
        )

    def list_keys(self, prefix: str) -> list[str]:
        """Return all object keys under prefix (handles S3 pagination)."""
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys


def generate_thumbnail(
    data: bytes,
    max_dimension: int,
    *,
    preserve_png: bool = False,
    source_mime: str | None = None,
) -> tuple[bytes, str]:
    """Resize image to fit bounding box, return (bytes, content_type)."""
    img = PILImage.open(BytesIO(data))
    if img.width > img.height:
        ratio = max_dimension / img.width
    else:
        ratio = max_dimension / img.height
    new_size = (
        max(1, int(img.width * ratio)),
        max(1, int(img.height * ratio)),
    )
    img = img.resize(new_size, PILImage.Resampling.LANCZOS)

    use_png = preserve_png and source_mime and "png" in source_mime
    if not use_png and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    fmt = "PNG" if use_png else "JPEG"
    buf = BytesIO()
    if fmt == "JPEG":
        img.save(buf, format=fmt, quality=70)
    else:
        img.save(buf, format=fmt)

    ct = "image/png" if use_png else "image/jpeg"
    return buf.getvalue(), ct


_storage: StorageClient | None = None


def _get_storage_singleton() -> StorageClient:
    global _storage
    if _storage is None:
        _storage = StorageClient(
            endpoint_url=os.environ.get(
                "S3_ENDPOINT_URL",
                "http://localhost:9000",
            ),
            access_key=os.environ.get("S3_ACCESS_KEY", ""),
            secret_key=os.environ.get("S3_SECRET_KEY", ""),
            bucket=os.environ.get("S3_BUCKET", "vindobona2-at"),
            public_endpoint_url=os.environ.get(
                "S3_PUBLIC_ENDPOINT_URL",
            ),
            region=os.environ.get("S3_REGION", "us-east-1"),
            presigned_expiry=int(
                os.environ.get("S3_PRESIGNED_EXPIRY", "900"),
            ),
        )
    return _storage


def get_storage() -> StorageClient:
    return _get_storage_singleton()
