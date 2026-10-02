"""The generic table browser must not show credentials or session secrets."""

import pytest
from sqlalchemy import event, text

from app.services import system_service
from tests.test_activity_log import _login_admin, _seed

SECRET_NAME_HINTS = ("password", "token", "secret", "jti")


class TestMaskedColumns:
    def test_password_hash_is_masked_in_the_members_table(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        assert admin.auth_password

        resp = client.get("/api/system/tables/members", headers=headers)

        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert rows
        assert {row["auth_password"] for row in rows} == {system_service.MASKED_VALUE}

    def test_session_secrets_are_masked(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)

        resp = client.get("/api/system/tables/sessions", headers=headers)

        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert rows
        for row in rows:
            assert row["jti"] == system_service.MASKED_VALUE
            assert row["refresh_token_hash"] == system_service.MASKED_VALUE

    def test_other_columns_stay_readable(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)

        resp = client.get("/api/system/tables/members", headers=headers)

        assert resp.json()["rows"][0]["email"] == admin.email

    def test_every_secret_looking_column_is_on_the_mask_list(self, db_session):
        """A new credential column must be added to the mask list on purpose."""
        rows = db_session.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        ).all()
        unmasked = [
            (table, column)
            for table, column in rows
            if any(hint in column for hint in SECRET_NAME_HINTS)
            and (table, column) not in system_service.MASKED_COLUMNS
        ]

        assert unmasked == []


class TestStableOrder:
    @pytest.mark.parametrize(
        ("table", "order_column"),
        [("members", "id"), ("alembic_version", "version_num")],
    )
    def test_rows_are_read_in_primary_key_order(self, db_session, table, order_column):
        statements: list[str] = []

        def capture(_conn, _cursor, statement, *_args):
            statements.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            system_service.get_table_data(db_session, table, 1, 25)
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        selects = [s for s in statements if s.startswith("SELECT *")]
        assert len(selects) == 1
        assert f"ORDER BY {order_column} LIMIT" in selects[0]
