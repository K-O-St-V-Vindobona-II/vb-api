"""Bounds of the filters of the sent-mail list."""

import pytest

from tests.test_activity_log import _login_admin, _seed


class TestTrackingFilters:
    @pytest.mark.parametrize(
        "query",
        [
            "year=99999",
            "year=1999",
            "year=2101",
            "month=13",
            "month=0",
            "search=" + "x" * 201,
        ],
    )
    def test_out_of_range_filter_is_a_validation_error(self, client, db_session, query):
        _seed(db_session)
        headers, _ = _login_admin(db_session)

        resp = client.get(f"/api/tracking/sent-emails?{query}", headers=headers)

        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "query",
        [
            "year=2000&month=1",
            "year=2100&month=12",
            "year=2026&month=3&search=abc",
            "search=" + "x" * 200,
        ],
    )
    def test_filters_inside_the_bounds_are_accepted(self, client, db_session, query):
        _seed(db_session)
        headers, _ = _login_admin(db_session)

        resp = client.get(f"/api/tracking/sent-emails?{query}", headers=headers)

        assert resp.status_code == 200
