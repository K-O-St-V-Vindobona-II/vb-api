"""HTTP-level coverage for p4x router endpoints that previously only had
service-layer tests, bypassing FastAPI's routing/response-building code
entirely: paginated warnings, import validation, transaction listings,
raw/attachment downloads, partner search validation, category filter CRUD,
filter2direct, category-direct assignment, fee config CRUD, fee member
lookups, SumUp balance, and the summary export.
"""

import asyncio
import base64
import io
import json
import uuid
import zipfile
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import bcrypt
import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

if TYPE_CHECKING:
    from decimal import Decimal

from app.api.router_includes import p4x as p4x_routes
from app.core.body_limit import MAX_UPLOAD_BODY_BYTES
from app.models.enums import SubjectMode
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_transaction import P4xTransaction
from app.models.role import Role
from app.models.state import State
from app.services import p4x_import_service, p4x_partner_service
from app.services.auth_service import create_user_session


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(db) -> None:
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            State(id="up", label="UP", order=1),
            Role(id="phil-xxxx", group="philchc", label="Phil-x", order=1),
        ]
    )
    db.commit()


def _create_admin(db, email: str = "p4x-http-admin@test.at") -> Member:
    pw = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    member = Member(
        vorname="Test",
        nachname="Admin",
        couleurname="Tester",
        email=email,
        auth_password=pw,
        org_id="vbw",
        state_id="up",
        auth_locked=False,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    db.add(
        MemberRole(
            member_id=member.id,
            role_id="phil-xxxx",
            startdate=date(2020, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    return member


def _login(db, member: Member) -> dict:
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


def _create_account(
    db,
    iban: str = "AT942011100005301947",
    bic: str = "GIBAATWWXXX",
    label: str = "Girokonto",
) -> P4xAccount:
    account = P4xAccount(
        iban=iban,
        bic=bic,
        label=label,
        init_date=date(2017, 1, 1),
        init_balance=100.0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _create_transaction(
    db,
    account: P4xAccount,
    *,
    sha256_hash: str = "http_cov_tx",
    booking: date = date(2026, 3, 15),
    amount: Decimal | float = 42.0,
    subject: str = "Test",
    iban: str = "AT001",
) -> P4xTransaction:
    tx = P4xTransaction(
        sha256_hash=sha256_hash,
        booking=booking,
        valuation=booking,
        iban=iban,
        amount=amount,
        subject=subject,
        p4x_account_id=account.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


def _create_category(
    db, name: str = "http_cov_cat", protected: bool = False
) -> P4xCategory:
    cat = P4xCategory(
        name=name,
        label="Cov",
        background_color="#000",
        text_color="#fff",
        protected=protected,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def _create_filter(
    db,
    account: P4xAccount,
    category: P4xCategory,
    *,
    name: str = "http_cov_filter",
    subject: str = "Filterhit",
) -> P4xCategoryFilter:
    f = P4xCategoryFilter(
        name=name,
        p4x_account_id=account.id,
        subject=subject,
        subject_mode=SubjectMode.CONTAINS,
        p4x_category_id=category.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


class TestWarningsEndpointsHttp:
    def test_partner_warnings_paginated(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        _create_transaction(db_session, account)

        resp = client.get("/api/p4x/warnings/partner", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert len(data["items"]) == 1

    def test_category_warnings_paginated(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        _create_transaction(db_session, account)

        resp = client.get("/api/p4x/warnings/category", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1


class TestImportEndpointHttp:
    def test_iban_mismatch_rejected(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)

        resp = client.post(
            f"/api/p4x/admin/accounts/{account.id}/import",
            files={"file": ("wrong_iban.json", b"[]", "application/json")},
            headers=headers,
        )
        assert resp.status_code == 422
        assert "IBAN" in resp.json()["detail"]

    def test_file_too_large_rejected(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        oversized = b"x" * (3 * 1024 * 1024 + 1)

        resp = client.post(
            f"/api/p4x/admin/accounts/{account.id}/import",
            files={
                "file": (
                    "AT942011100005301947.json",
                    oversized,
                    "application/json",
                )
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert "3 MB" in resp.json()["detail"]

    def test_unparseable_file_reports_failure_without_import(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session, bic="UNKNOWNBIC")

        resp = client.post(
            f"/api/p4x/admin/accounts/{account.id}/import",
            files={
                "file": (
                    "AT942011100005301947.json",
                    b"[]",
                    "application/json",
                )
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["given"]["parsed"] is False
        assert data["account"] is None

    def test_successful_import_returns_summary_and_account(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session, iban="AT94 2011 1000 0530 1947")

        entry = {
            "booking": "2026-03-20T00:00:00.000+0100",
            "valuation": "2026-03-20T00:00:00.000+0100",
            "partnerAccount": {"iban": "DE49100110012624770917"},
            "amount": {"value": 1500, "precision": 2},
            "reference": "monatlicher MB",
            "receiverReference": "",
        }
        raw_json = json.dumps([entry]).encode("utf-8")

        resp = client.post(
            f"/api/p4x/admin/accounts/{account.id}/import",
            files={
                "file": (
                    "AT942011100005301947.json",
                    raw_json,
                    "application/json",
                )
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["given"]["parsed"] is True
        assert data["summary"]["new"] == 1
        assert data["account"]["transactions_count"] == 1


def _spy_on_event_loop(monkeypatch, module, name: str) -> list[bool]:
    """Wrap `module.name` and record whether each call ran on the event loop.

    `asyncio.get_running_loop()` only succeeds in the thread that runs the loop,
    so a call from the thread pool (where blocking work belongs) raises.
    """
    real = getattr(module, name)
    on_loop: list[bool] = []

    def spy(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            on_loop.append(False)
        else:
            on_loop.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, spy)
    return on_loop


class TestImportRequestHandling:
    def test_non_utf8_file_is_a_422_not_a_500(self, db_session, client):
        _seed(db_session)
        headers = _login(db_session, _create_admin(db_session))
        account = _create_account(db_session)

        resp = client.post(
            f"/api/p4x/admin/accounts/{account.id}/import",
            files={
                "file": ("AT942011100005301947.json", b"\xff\xfe[]", "application/json")
            },
            headers=headers,
        )

        assert resp.status_code == 422
        assert "UTF-8" in resp.json()["detail"]

    def test_body_above_the_upload_limit_is_refused_before_the_handler(
        self, db_session, client, monkeypatch
    ):
        _seed(db_session)
        headers = _login(db_session, _create_admin(db_session))
        account = _create_account(db_session)
        reached: list[bool] = []
        monkeypatch.setattr(
            p4x_import_service,
            "parse_george_json",
            lambda *_args: reached.append(True),
        )

        resp = client.post(
            f"/api/p4x/admin/accounts/{account.id}/import",
            files={
                "file": (
                    "AT942011100005301947.json",
                    b"x" * (MAX_UPLOAD_BODY_BYTES + 1),
                    "text/plain",
                )
            },
            headers=headers,
        )

        assert resp.status_code == 413
        assert reached == []

    def test_the_import_does_not_run_on_the_event_loop(
        self, db_session, client, monkeypatch
    ):
        _seed(db_session)
        headers = _login(db_session, _create_admin(db_session))
        account = _create_account(db_session)
        on_loop = _spy_on_event_loop(
            monkeypatch, p4x_import_service, "parse_george_json"
        )

        resp = client.post(
            f"/api/p4x/admin/accounts/{account.id}/import",
            files={"file": ("AT942011100005301947.json", b"[]", "application/json")},
            headers=headers,
        )

        assert resp.status_code == 200
        assert on_loop == [False]


class _RecordingFile(io.BytesIO):
    """An upload body that remembers how much each read asked for."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.read_sizes: list[int | None] = []

    def read(self, size: int | None = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


class TestUploadReadIsBounded:
    """An oversized upload is refused after reading one byte more than the limit,
    not after buffering the whole file."""

    _LIMIT = 3 * 1024 * 1024

    def test_import_reads_at_most_one_byte_beyond_the_limit(self, db_session):
        _seed(db_session)
        admin = _create_admin(db_session)
        account = _create_account(db_session)
        upload = _RecordingFile(b"x" * (self._LIMIT * 2))

        with pytest.raises(HTTPException) as refused:
            p4x_routes.import_transactions(
                account.id,
                UploadFile(file=upload, filename="AT942011100005301947.json"),
                db_session,
                admin,
            )

        assert refused.value.status_code == 422
        assert upload.read_sizes == [self._LIMIT + 1]

    def test_attachment_reads_at_most_one_byte_beyond_the_limit(self, db_session):
        _seed(db_session)
        admin = _create_admin(db_session)
        tx = _create_transaction(db_session, _create_account(db_session))
        upload = _RecordingFile(b"x" * (self._LIMIT * 2))

        with pytest.raises(HTTPException) as refused:
            p4x_routes.update_transaction(
                tx.id,
                db_session,
                admin,
                comment="x",
                delete_attachment=False,
                file=UploadFile(
                    file=upload,
                    filename="beleg.pdf",
                    headers=Headers({"content-type": "application/pdf"}),
                ),
            )

        assert refused.value.status_code == 422
        assert upload.read_sizes == [self._LIMIT + 1]


class TestTransactionListingsHttp:
    def test_by_month_success_with_start_and_end_balance(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        _create_transaction(db_session, account, booking=date(2026, 3, 10))

        resp = client.get(
            f"/api/p4x/accounts/{account.id}/transactions/by-month/2026/3",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert "startbalance" in data
        assert "endbalance" in data

    def test_by_month_handles_december_year_rollover(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        _create_transaction(db_session, account, booking=date(2026, 12, 5))

        resp = client.get(
            f"/api/p4x/accounts/{account.id}/transactions/by-month/2026/12",
            headers=headers,
        )
        assert resp.status_code == 200

    def test_by_partner_success(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        _create_transaction(db_session, account)

        resp = client.get(
            f"/api/p4x/accounts/{account.id}/transactions/by-partner/member/{uuid.uuid4()}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_by_category_success(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        cat = _create_category(db_session)

        resp = client.get(
            f"/api/p4x/accounts/{account.id}/transactions/by-category/{cat.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_by_filter_success(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        cat = _create_category(db_session)
        f = _create_filter(db_session, account, cat)

        resp = client.get(
            f"/api/p4x/admin/accounts/{account.id}/transactions/by-filter/{f.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestTransactionRawAndAttachmentHttp:
    def test_raw_returns_original_payload(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        tx = _create_transaction(db_session, account)
        tx.raw = '{"original": true}'
        db_session.commit()

        resp = client.get(
            f"/api/p4x/accounts/{account.id}/transactions/raw/{tx.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["raw"] == '{"original": true}'

    def test_attachment_404_when_none_uploaded(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        tx = _create_transaction(db_session, account)

        resp = client.get(
            f"/api/p4x/accounts/{account.id}/transactions/attachment/{tx.id}",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_attachment_downloads_pdf_when_present(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        tx = _create_transaction(db_session, account)
        tx.attachment = base64.b64encode(b"%PDF-1.4 fake content").decode()
        db_session.commit()

        resp = client.get(
            f"/api/p4x/accounts/{account.id}/transactions/attachment/{tx.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content == b"%PDF-1.4 fake content"


class TestPartnerSearchValidationHttp:
    def test_search_below_minimum_length_rejected(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        resp = client.get("/api/p4x/partner/search?q=ab", headers=headers)
        assert resp.status_code == 400

    def test_search_returns_matches(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        resp = client.get("/api/p4x/partner/search?q=Admin", headers=headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestSetTransactionPartnerWithDataHttp:
    def test_set_partner_and_delegating_partner(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        tx = _create_transaction(db_session, account)
        delegate = _create_admin(db_session, email="p4x-delegate@test.at")

        resp = client.post(
            f"/api/p4x/admin/transactions/{tx.id}/set-partner",
            json={
                "partner": {
                    "type": "member",
                    "id": str(admin.id),
                    "cn": "Admin",
                },
                "hasDelegatingPartner": True,
                "delegatingPartner": {
                    "type": "member",
                    "id": str(delegate.id),
                    "cn": "Delegate",
                },
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["partner"]["id"] == str(admin.id)
        assert data["delegating_partner"]["id"] == str(delegate.id)


class TestUpdateTransactionValidationHttp:
    def test_non_pdf_content_type_rejected(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        tx = _create_transaction(db_session, account)

        resp = client.put(
            f"/api/p4x/admin/transactions/{tx.id}",
            data={"comment": "x", "delete_attachment": "false"},
            files={"file": ("note.txt", b"hello", "text/plain")},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_oversized_attachment_rejected(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        tx = _create_transaction(db_session, account)
        oversized = b"x" * (3 * 1024 * 1024 + 1)

        resp = client.put(
            f"/api/p4x/admin/transactions/{tx.id}",
            data={"comment": "x", "delete_attachment": "false"},
            files={"file": ("big.pdf", oversized, "application/pdf")},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_comment_and_pdf_attachment_saved(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        tx = _create_transaction(db_session, account)

        resp = client.put(
            f"/api/p4x/admin/transactions/{tx.id}",
            data={"comment": "Belegkopie", "delete_attachment": "false"},
            files={"file": ("beleg.pdf", b"%PDF-1.4 x", "application/pdf")},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["comment"] == "Belegkopie"
        assert data["has_attachment"] is True


class TestUpdateTransactionRequestHandling:
    def test_the_update_does_not_run_on_the_event_loop(
        self, db_session, client, monkeypatch
    ):
        _seed(db_session)
        headers = _login(db_session, _create_admin(db_session))
        tx = _create_transaction(db_session, _create_account(db_session))
        on_loop = _spy_on_event_loop(
            monkeypatch, p4x_partner_service, "update_transaction_meta"
        )

        resp = client.put(
            f"/api/p4x/admin/transactions/{tx.id}",
            data={"comment": "Beleg", "delete_attachment": "false"},
            headers=headers,
        )

        assert resp.status_code == 200
        assert on_loop == [False]


class TestCategoryFilterEndpointsHttp:
    def test_list_create_update_delete_via_http(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        cat = _create_category(db_session)

        resp = client.get("/api/p4x/admin/category-filters", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []

        resp = client.post(
            "/api/p4x/admin/category-filters",
            json={
                "name": "Mitgliedsbeitrag",
                "p4x_account_id": str(account.id),
                "subject": "MB",
                "subject_mode": "contains",
                "p4x_category_id": str(cat.id),
            },
            headers=headers,
        )
        assert resp.status_code == 201
        filter_id = resp.json()["id"]

        resp = client.put(
            f"/api/p4x/admin/category-filters/{filter_id}",
            json={
                "name": "Mitgliedsbeitrag geändert",
                "p4x_account_id": str(account.id),
                "subject": "MB",
                "subject_mode": "contains",
                "p4x_category_id": str(cat.id),
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Mitgliedsbeitrag geändert"

        resp = client.delete(
            f"/api/p4x/admin/category-filters/{filter_id}", headers=headers
        )
        assert resp.status_code == 204


class TestFilter2DirectHttp:
    def test_preview_and_process_without_matching_transactions(
        self, db_session, client
    ):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        cat = _create_category(db_session)
        f = _create_filter(db_session, account, cat)

        resp = client.get(
            f"/api/p4x/admin/category-filters/{f.id}/filter2direct",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hits"] == []
        assert data["category"]["id"] == str(cat.id)

        resp = client.post(
            f"/api/p4x/admin/category-filters/{f.id}/filter2direct",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["hits"] == []

    def test_process_rejected_while_warnings_are_open(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        cat = _create_category(db_session)
        f = _create_filter(db_session, account, cat)
        # An unassigned transaction is an open partner/category warning,
        # which filter_to_direct() refuses to run past.
        _create_transaction(db_session, account)

        resp = client.post(
            f"/api/p4x/admin/category-filters/{f.id}/filter2direct",
            headers=headers,
        )
        assert resp.status_code == 422


class TestCategoryDirectEndpointsHttp:
    def test_set_and_unset_category_direct(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        cat = _create_category(db_session)
        tx = _create_transaction(db_session, account, amount=100.0)

        resp = client.post(
            f"/api/p4x/admin/transactions/{tx.id}/set-category-direct",
            json=[{"p4x_category_id": str(cat.id), "amount": 100.0}],
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["p4x_category_directs"][0]["p4x_category_id"] == str(cat.id)

        resp = client.post(
            f"/api/p4x/admin/transactions/{tx.id}/set-category-direct",
            json=[{"p4x_category_id": str(cat.id), "amount": 1.0}],
            headers=headers,
        )
        assert resp.status_code == 422

        resp = client.delete(
            f"/api/p4x/admin/transactions/{tx.id}/unset-category-direct",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["p4x_category_directs"] == []


class TestFeeConfigEndpointsHttp:
    def test_list_create_delete_via_http(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        resp = client.get("/api/p4x/admin/fee-config", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []

        resp = client.post(
            "/api/p4x/admin/fee-config",
            json={"year": 2027, "month": 1, "fee": 40},
            headers=headers,
        )
        assert resp.status_code == 201
        assert len(resp.json()) == 1

        resp = client.post(
            "/api/p4x/admin/fee-config",
            json={"year": 2027, "month": 1, "fee": 45},
            headers=headers,
        )
        assert resp.status_code == 422

        resp = client.delete("/api/p4x/admin/fee-config/2027-01-01", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []

        resp = client.delete("/api/p4x/admin/fee-config/2027-01-01", headers=headers)
        assert resp.status_code == 422


class TestFeeMemberEndpointsHttp:
    def test_search_below_minimum_length_rejected(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        resp = client.get("/api/p4x/fee-members/search?q=ab", headers=headers)
        assert resp.status_code == 400

    def test_search_returns_matches(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        resp = client.get("/api/p4x/fee-members/search?q=Admin", headers=headers)
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_get_fee_member_success(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        # _create_admin already yields a fee-liable member (org_id=vbw,
        # state_id=up, entlassen=False, verstorben=False).
        target = _create_admin(db_session, email="fee-liable-target@test.at")

        resp = client.get(f"/api/p4x/fee-members/{target.id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == str(target.id)

    def test_get_fee_member_404_when_not_fee_liable(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        target = _create_admin(db_session, email="not-fee-member@test.at")
        # is_fee_member() checks org_id/state_id/entlassen/verstorben, not
        # roles - discharging the member is the actual way to fail it.
        target.entlassen = True
        db_session.commit()

        resp = client.get(f"/api/p4x/fee-members/{target.id}", headers=headers)
        assert resp.status_code == 404
        assert "Kein Beitragsmitglied" in resp.json()["detail"]


class TestSumUpBalanceHttp:
    def test_returns_empty_balance(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        resp = client.get("/api/p4x/sumup/balance", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["in_count"] == 0
        assert data["out_count"] == 0


class TestDownloadSummaryHttp:
    def test_end_before_start_rejected(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        resp = client.post(
            "/api/p4x/admin/summary",
            json={"start": "2026-03-01", "end": "2026-01-01"},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_generates_zip_with_xlsx(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        resp = client.post(
            "/api/p4x/admin/summary",
            json={"start": "2026-01-01", "end": "2026-01-31"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert any(name.endswith(".xlsx") for name in names)

    def test_generates_zip_with_bundled_pdf_attachment(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        account = _create_account(db_session)
        tx = _create_transaction(db_session, account, booking=date(2026, 1, 15))
        tx.attachment = base64.b64encode(b"%PDF-1.4 fake content").decode()
        db_session.commit()

        resp = client.post(
            "/api/p4x/admin/summary",
            json={"start": "2026-01-01", "end": "2026-01-31"},
            headers=headers,
        )
        assert resp.status_code == 200

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert any(name.endswith(".pdf") for name in names)
