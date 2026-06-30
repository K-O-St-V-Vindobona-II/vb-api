import datetime

from sqlalchemy import String, TypeDecorator
from sqlalchemy.engine.interfaces import Dialect


class FlexibleDate(TypeDecorator[datetime.date]):
    """SQLite stores dates as text. The legacy DB has both '2006-01-09'
    and '2006-01-09 00:00:00' formats. This type handles both."""

    impl = String
    cache_ok = True

    def process_result_value(
        self,
        value: str | datetime.date | None,
        dialect: Dialect,  # noqa: ARG002
    ) -> datetime.date | None:
        if value is None:
            return None
        if isinstance(value, datetime.date):
            return value
        return datetime.date.fromisoformat(value.split(" ")[0])

    def process_bind_param(
        self,
        value: str | datetime.date | None,
        dialect: Dialect,  # noqa: ARG002
    ) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime.date):
            return value.isoformat()
        return value.split(" ")[0]
