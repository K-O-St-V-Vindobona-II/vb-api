"""Rules that category and category-filter writes enforce before anything is
stored: unique names, at least one criterion, and existing references."""

import uuid

import pytest

from tests.p4x import test_p4x_router_http_coverage as p4x_http

CATEGORIES = "/api/p4x/admin/categories"
FILTERS = "/api/p4x/admin/category-filters"
NAME_TAKEN = "Name muss eindeutig sein."


@pytest.fixture
def admin_headers(db_session) -> dict:
    p4x_http._seed(db_session)
    return p4x_http._login(db_session, p4x_http._create_admin(db_session))


def _category_body(name: str) -> dict:
    return {
        "name": name,
        "label": "Label",
        "background_color": "#336600",
        "text_color": "#ffffff",
    }


def _filter_body(account_id, category_id, **overrides) -> dict:
    return {
        "name": "new_filter",
        "p4x_account_id": str(account_id),
        "subject": "MB",
        "subject_mode": "contains",
        "p4x_category_id": str(category_id),
        **overrides,
    }


class TestCategoryNames:
    def test_a_category_cannot_take_the_name_of_another(
        self, client, db_session, admin_headers
    ):
        p4x_http._create_category(db_session, name="first")
        second = p4x_http._create_category(db_session, name="second")

        resp = client.put(
            f"{CATEGORIES}/{second.id}",
            json=_category_body("first"),
            headers=admin_headers,
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == NAME_TAKEN

    def test_a_category_can_keep_its_own_name(self, client, db_session, admin_headers):
        category = p4x_http._create_category(db_session, name="keep")

        resp = client.put(
            f"{CATEGORIES}/{category.id}",
            json=_category_body("keep"),
            headers=admin_headers,
        )

        assert resp.status_code == 200
        db_session.refresh(category)
        assert (category.name, category.label) == ("keep", "Label")


class TestCategoryFilterInput:
    def test_a_filter_needs_at_least_one_criterion(
        self, client, db_session, admin_headers
    ):
        account = p4x_http._create_account(db_session)
        category = p4x_http._create_category(db_session)
        body = _filter_body(account.id, category.id, subject=None)

        resp = client.post(FILTERS, json=body, headers=admin_headers)

        assert resp.status_code == 422
        assert (
            resp.json()["detail"] == "Mindestens ein Filterkriterium muss gesetzt sein."
        )

    def test_a_filter_needs_an_existing_account(
        self, client, db_session, admin_headers
    ):
        category = p4x_http._create_category(db_session)
        body = _filter_body(uuid.uuid4(), category.id)

        resp = client.post(FILTERS, json=body, headers=admin_headers)

        assert resp.status_code == 422
        assert resp.json()["detail"] == "Konto existiert nicht."

    def test_a_filter_needs_an_existing_category(
        self, client, db_session, admin_headers
    ):
        account = p4x_http._create_account(db_session)
        body = _filter_body(account.id, uuid.uuid4())

        resp = client.post(FILTERS, json=body, headers=admin_headers)

        assert resp.status_code == 422
        assert resp.json()["detail"] == "Kategorie existiert nicht."


class TestCategoryFilterNames:
    def test_a_new_filter_cannot_reuse_a_name(self, client, db_session, admin_headers):
        account = p4x_http._create_account(db_session)
        category = p4x_http._create_category(db_session)
        p4x_http._create_filter(db_session, account, category, name="taken")

        resp = client.post(
            FILTERS,
            json=_filter_body(account.id, category.id, name="taken"),
            headers=admin_headers,
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == NAME_TAKEN

    def test_a_filter_cannot_take_the_name_of_another(
        self, client, db_session, admin_headers
    ):
        account = p4x_http._create_account(db_session)
        category = p4x_http._create_category(db_session)
        p4x_http._create_filter(db_session, account, category, name="taken")
        other = p4x_http._create_filter(db_session, account, category, name="other")

        resp = client.put(
            f"{FILTERS}/{other.id}",
            json=_filter_body(account.id, category.id, name="taken"),
            headers=admin_headers,
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == NAME_TAKEN
