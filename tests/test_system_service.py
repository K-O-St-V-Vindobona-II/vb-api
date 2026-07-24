"""Direct service-layer tests for app.services.system_service.

Permission-gating and the HTTP contract are already covered end-to-end
via tests/test_system.py — these tests exercise the service functions
directly against the real (Alembic-migrated) schema.
"""

import pytest
from fastapi import HTTPException

from app.services import system_service


class TestGetValidTables:
    def test_returns_sorted_known_table(self, db_session) -> None:
        tables = system_service.get_valid_tables(db_session)
        assert tables == sorted(tables)
        assert "members" in tables


class TestGetTableData:
    def test_unknown_table_raises_404(self, db_session) -> None:
        with pytest.raises(HTTPException) as exc_info:
            system_service.get_table_data(db_session, "not_a_real_table", 1, 25)
        assert exc_info.value.status_code == 404

    def test_returns_columns_and_rows_for_known_table(self, db_session) -> None:
        data = system_service.get_table_data(db_session, "orgs", 1, 25)
        assert data["table_name"] == "orgs"
        assert isinstance(data["columns"], list)
        assert data["columns"]
        assert {c["name"] for c in data["columns"]} >= {"id", "label"}
        assert data["page"] == 1
        assert data["page_size"] == 25
