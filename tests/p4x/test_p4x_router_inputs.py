"""Inputs of the p4x routes that must be refused with a message instead of
producing a wrong page or a server error, and download names that hold any
character of a member's name."""

import base64
import uuid

import pytest

from tests.p4x.test_p4x_router_http_coverage import (
    _create_account,
    _create_admin,
    _create_transaction,
    _login,
    _seed,
)


@pytest.fixture
def admin_headers(db_session):
    _seed(db_session)
    return _login(db_session, _create_admin(db_session))


@pytest.fixture
def account(db_session):
    return _create_account(db_session)


def _list_routes(account_id):
    any_id = uuid.uuid4()
    transactions = f"/api/p4x/accounts/{account_id}/transactions"
    return {
        "warnings-partner": "/api/p4x/warnings/partner",
        "warnings-category": "/api/p4x/warnings/category",
        "by-month": f"{transactions}/by-month/2026/3",
        "by-partner": f"{transactions}/by-partner/member/{any_id}",
        "by-category": f"{transactions}/by-category/{any_id}",
        "by-filter": (
            f"/api/p4x/admin/accounts/{account_id}/transactions/by-filter/{any_id}"
        ),
    }


ROUTE_NAMES = [
    "warnings-partner",
    "warnings-category",
    "by-month",
    "by-partner",
    "by-category",
    "by-filter",
]


class TestPageParameter:
    @pytest.mark.parametrize("route", ROUTE_NAMES)
    @pytest.mark.parametrize("page", ["0", "-1", "-100"])
    def test_a_page_below_one_is_a_validation_error(
        self, client, account, admin_headers, route, page
    ):
        url = _list_routes(account.id)[route]

        response = client.get(f"{url}?page={page}", headers=admin_headers)

        assert response.status_code == 422
        assert "page" in str(response.json()["detail"])

    @pytest.mark.parametrize(
        "route", ["warnings-partner", "warnings-category", "by-month"]
    )
    def test_page_one_is_accepted(self, client, account, admin_headers, route):
        url = _list_routes(account.id)[route]

        response = client.get(f"{url}?page=1", headers=admin_headers)

        assert response.status_code == 200
        assert response.json()["page"] == 1

    @pytest.mark.parametrize(
        "route", ["warnings-partner", "warnings-category", "by-month"]
    )
    def test_a_page_beyond_the_data_is_an_empty_page(
        self, client, account, admin_headers, route
    ):
        url = _list_routes(account.id)[route]

        response = client.get(f"{url}?page=50", headers=admin_headers)

        assert response.status_code == 200
        assert response.json()["items"] == []


class TestMonthPath:
    @pytest.mark.parametrize(
        ("year", "month"),
        [(2026, 0), (2026, 13), (2026, -1), (99999, 3), (1999, 3), (2101, 3), (0, 1)],
    )
    def test_an_impossible_month_or_year_is_a_validation_error(
        self, client, account, admin_headers, year, month
    ):
        response = client.get(
            f"/api/p4x/accounts/{account.id}/transactions/by-month/{year}/{month}",
            headers=admin_headers,
        )

        assert response.status_code == 422

    @pytest.mark.parametrize(("year", "month"), [(2000, 1), (2026, 12), (2100, 12)])
    def test_the_edges_of_the_range_are_accepted(
        self, client, account, admin_headers, year, month
    ):
        response = client.get(
            f"/api/p4x/accounts/{account.id}/transactions/by-month/{year}/{month}",
            headers=admin_headers,
        )

        assert response.status_code == 200


class TestDownloadFilenames:
    def test_the_summary_download_has_a_percent_encoded_exact_name(
        self, client, admin_headers
    ):
        response = client.post(
            "/api/p4x/admin/summary",
            json={"start": "2026-01-01", "end": "2026-01-31"},
            headers=admin_headers,
        )

        disposition = response.headers["content-disposition"]
        name = "Abrechnung_2026-01-01_bis_2026-01-31.zip"
        assert response.status_code == 200
        assert f"filename*=UTF-8''{name}" in disposition
        assert f'filename="{name}"' in disposition

    def test_the_attachment_download_has_a_percent_encoded_name(
        self, client, db_session, account, admin_headers
    ):
        tx = _create_transaction(db_session, account)
        tx.attachment = base64.b64encode(b"%PDF-1.4 x").decode()
        db_session.commit()

        response = client.get(
            f"/api/p4x/accounts/{account.id}/transactions/attachment/{tx.id}",
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert (
            f"filename*=UTF-8''Beilage_{tx.id}.pdf"
            in (response.headers["content-disposition"])
        )
