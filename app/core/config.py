import sys
from functools import lru_cache
from typing import NoReturn

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Kept in sync by convention with the equivalent frontend guards
# (vb-intern/vite.env-check.ts, vb-www/vite.env-check.ts) — not shared code,
# since backend and frontends deploy and release independently.
_VALID_ENVIRONMENTS = frozenset({"development", "test", "qa", "production"})


def _fail(message: str) -> NoReturn:
    sys.stderr.write(f"FATAL: {message} Aborting.\n")
    sys.stderr.flush()
    sys.exit(1)


class Settings(BaseSettings):
    """Typed application configuration, sourced from environment variables.

    Access exclusively via get_settings() (never instantiate directly) —
    it is the only path that keeps the lru_cache and its per-test reset
    fixture (see tests/conftest.py) in sync.
    """

    model_config = SettingsConfigDict(extra="ignore")

    app_environment: str | None = Field(
        default=None, validation_alias="APP_ENVIRONMENT"
    )
    cors_origins: str | None = Field(default=None, validation_alias="CORS_ORIGINS")
    secret_key: str | None = Field(default=None, validation_alias="SECRET_KEY")
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    # Required unconditionally: both the web container (enqueues ad-hoc
    # background tasks) and the ARQ worker container (runs cron jobs and
    # dequeues tasks) depend on Redis for their core functionality.
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")

    # Tier 3 (session/auth) — optional with defaults, no boot-time validation.
    session_lifetime_minutes: int = Field(
        default=15, validation_alias="SESSION_LIFETIME_MINUTES"
    )
    session_idle_timeout_minutes: int = Field(
        default=120, validation_alias="SESSION_IDLE_TIMEOUT_MINUTES"
    )
    refresh_token_lifetime_days: int = Field(
        default=7, validation_alias="REFRESH_TOKEN_LIFETIME_DAYS"
    )
    password_min_length: int = Field(default=8, validation_alias="PASSWORD_MIN_LENGTH")

    # Tier 2 (feature-required — checked at first use via require_setting(),
    # not at boot: a process that never sends email, offers Google Login,
    # or touches S3 storage (e.g. a bare migration run) must not be forced
    # to configure these; a real empty-string default for the S3 keys would
    # instead fail silently deep inside boto3 on first actual use).
    smtp_host: str | None = Field(default=None, validation_alias="SMTP_HOST")
    smtp_port: int | None = Field(default=None, validation_alias="SMTP_PORT")
    smtp_from_email: str | None = Field(
        default=None, validation_alias="SMTP_FROM_EMAIL"
    )
    frontend_reset_url: str | None = Field(
        default=None, validation_alias="FRONTEND_RESET_URL"
    )
    google_client_id: str | None = Field(
        default=None, validation_alias="GOOGLE_CLIENT_ID"
    )
    s3_access_key: str | None = Field(default=None, validation_alias="S3_ACCESS_KEY")
    s3_secret_key: str | None = Field(default=None, validation_alias="S3_SECRET_KEY")
    # Prod AWS S3 credentials for the non-prod downsync job/script
    # (app/services/downsync_service.py) — deliberately separate from the
    # s3_* fields above, which target this stage's own bucket/endpoint.
    # Expected to be a dedicated, read-only IAM user scoped to the prod
    # bucket only (s3:GetObject/s3:ListBucket) — the downsync path never
    # writes through this credential, only through the s3_* one above.
    aws_prod_access_key_id: str | None = Field(
        default=None, validation_alias="AWS_ACCESS_KEY_ID"
    )
    aws_prod_secret_access_key: str | None = Field(
        default=None, validation_alias="AWS_SECRET_ACCESS_KEY"
    )
    aws_prod_bucket: str | None = Field(default=None, validation_alias="AWS_BUCKET")
    # Tier 3: has a sensible default, unlike the three prod AWS fields above.
    aws_prod_region: str = Field(default="eu-central-1", validation_alias="AWS_REGION")

    # Tier 3 (SMTP) — optional with defaults, no boot-time validation.
    # "null" (not "") mirrors the historical env convention: an explicit
    # sentinel meaning "no SMTP auth", checked via `.lower() != "null"`.
    smtp_from_name: str = Field(default="Vindobona", validation_alias="SMTP_FROM_NAME")
    smtp_user: str = Field(default="null", validation_alias="SMTP_USER")
    smtp_password: str = Field(default="null", validation_alias="SMTP_PASSWORD")

    # Tier 3 (S3 path prefixes) — optional with defaults, no boot-time validation.
    s3_path_standesdb_images: str = Field(
        default="standesdb/images", validation_alias="S3_PATH_STANDESDB_IMAGES"
    )
    s3_path_standesdb_cache: str = Field(
        default="standesdb/cache", validation_alias="S3_PATH_STANDESDB_CACHE"
    )
    s3_path_archive_store: str = Field(
        default="archive/store", validation_alias="S3_PATH_ARCHIVE_STORE"
    )
    s3_path_archive_cache: str = Field(
        default="archive/cache", validation_alias="S3_PATH_ARCHIVE_CACHE"
    )
    s3_path_db_backups: str = Field(
        default="db-backups", validation_alias="S3_PATH_DB_BACKUPS"
    )
    s3_path_public_gallery: str = Field(
        default="public/gallery", validation_alias="S3_PATH_PUBLIC_GALLERY"
    )

    # Tier 3 (S3 connection) — optional with defaults, no boot-time
    # validation. s3_endpoint_url/s3_public_endpoint_url genuinely mean
    # something when unset (None = real AWS S3's default endpoint, or "no
    # separate public endpoint" — see StorageClient.__init__'s
    # public_endpoint_url fallback), unlike the credentials above, which
    # have no meaningful empty value.
    s3_endpoint_url: str | None = Field(
        default=None, validation_alias="S3_ENDPOINT_URL"
    )
    s3_bucket: str = Field(default="vindobona2-at", validation_alias="S3_BUCKET")
    s3_public_endpoint_url: str | None = Field(
        default=None, validation_alias="S3_PUBLIC_ENDPOINT_URL"
    )
    s3_region: str = Field(default="us-east-1", validation_alias="S3_REGION")
    s3_presigned_expiry: int = Field(
        default=900, validation_alias="S3_PRESIGNED_EXPIRY"
    )

    # Tier 3 (backup schedule / tracking / dev-only) — optional with
    # defaults, no boot-time validation.
    backup_enabled: bool = Field(default=True, validation_alias="BACKUP_ENABLED")
    backup_hour: int = Field(default=3, validation_alias="BACKUP_HOUR")
    backup_retention_days: int = Field(
        default=29, validation_alias="BACKUP_RETENTION_DAYS"
    )
    tracking_retention_months: int = Field(
        default=6, validation_alias="TRACKING_RETENTION_MONTHS"
    )
    dev_superuser_id: int = Field(default=0, validation_alias="DEV_SUPERUSER_ID")

    # Tier 3 (timezone) — optional with default, no boot-time validation.
    # Wall-clock timezone for human-facing output (cron schedule, email
    # timestamps) — not used for DB storage, which stays UTC (TIMESTAMPTZ)
    # regardless of this setting.
    app_timezone: str = Field(default="Europe/Vienna", validation_alias="APP_TIMEZONE")

    @field_validator("backup_enabled", mode="before")
    @classmethod
    def _parse_backup_enabled(cls, value: object) -> object:
        # Historical env convention: anything other than the literal string
        # "false" (case-insensitive) means enabled — not pydantic's default
        # strict bool parsing (which would reject e.g. "" or a typo and
        # crash boot for what should be a harmless Tier 3 default).
        if isinstance(value, str):
            return value.lower() != "false"
        return value

    @model_validator(mode="after")
    def _validate_tier1(self) -> "Settings":
        # Split into one helper per field (each with its own, smaller
        # branching) so this stays under the project's max-complexity
        # limit as the Tier 1 field list grows.
        self._validate_app_environment()
        self._validate_cors_origins()
        self._validate_secret_key()
        if not self.database_url:
            _fail("DATABASE_URL is not set.")
        if not self.redis_url:
            _fail("REDIS_URL is not set.")
        return self

    def _validate_app_environment(self) -> None:
        if not self.app_environment:
            _fail(
                "APP_ENVIRONMENT is not set. "
                f"Required values: {sorted(_VALID_ENVIRONMENTS)}."
            )
        if self.app_environment not in _VALID_ENVIRONMENTS:
            _fail(
                f"APP_ENVIRONMENT='{self.app_environment}' is invalid. "
                f"Valid values: {sorted(_VALID_ENVIRONMENTS)}."
            )

    def _validate_cors_origins(self) -> None:
        if not self.cors_origins:
            _fail(
                "CORS_ORIGINS is not set. "
                "Provide a comma-separated list of allowed frontend origins."
            )
        if not self.cors_origins_list:
            _fail("CORS_ORIGINS is set but contains no valid origins.")

    def _validate_secret_key(self) -> None:
        if not self.secret_key:
            _fail("SECRET_KEY is not set.")
        if len(self.secret_key) < 32:
            _fail("SECRET_KEY must be at least 32 characters long.")

    @property
    def cors_origins_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in (self.cors_origins or "").split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def require_setting[T](value: T | None, env_var: str) -> T:
    """Raise for a Tier 2 (feature-required) setting that is unset.

    Unlike Tier 1's _fail() (which aborts the process at boot), this
    raises a normal exception at first use — the process itself must
    stay up even if one feature (e.g. email) is unconfigured.
    """
    if value is None:
        msg = f"{env_var} is not set."
        raise RuntimeError(msg)
    return value
