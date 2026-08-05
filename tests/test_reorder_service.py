"""Unit tests for the shared sort_order-swap helper (extracted from
public_gallery_service.move_image(), now used by every reorderable admin
list - see app/services/reorder_service.py)."""

from app.models.public_site_quote import PublicSiteQuote
from app.services.reorder_service import find_reorder_neighbor


def _make_quotes(db, count: int) -> list[PublicSiteQuote]:
    rows = [
        PublicSiteQuote(quote=f"Zitat {i}", author=f"Autor {i}", sort_order=i)
        for i in range(1, count + 1)
    ]
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


class TestFindReorderNeighbor:
    def test_up_finds_previous_row(self, db_session):
        rows = _make_quotes(db_session, 3)

        neighbor = find_reorder_neighbor(
            db_session, PublicSiteQuote, PublicSiteQuote.sort_order, 2, "up"
        )

        assert neighbor is not None
        assert neighbor.id == rows[0].id

    def test_down_finds_next_row(self, db_session):
        rows = _make_quotes(db_session, 3)

        neighbor = find_reorder_neighbor(
            db_session, PublicSiteQuote, PublicSiteQuote.sort_order, 2, "down"
        )

        assert neighbor is not None
        assert neighbor.id == rows[2].id

    def test_up_at_start_returns_none(self, db_session):
        _make_quotes(db_session, 3)

        neighbor = find_reorder_neighbor(
            db_session, PublicSiteQuote, PublicSiteQuote.sort_order, 1, "up"
        )

        assert neighbor is None

    def test_down_at_end_returns_none(self, db_session):
        _make_quotes(db_session, 3)

        neighbor = find_reorder_neighbor(
            db_session, PublicSiteQuote, PublicSiteQuote.sort_order, 3, "down"
        )

        assert neighbor is None

    def test_finds_nearest_neighbor_with_gaps(self, db_session):
        # sort_order values aren't necessarily contiguous (e.g. after a
        # delete) - the nearest existing row must still be found.
        db_session.add_all(
            [
                PublicSiteQuote(quote="A", author="A", sort_order=1),
                PublicSiteQuote(quote="C", author="C", sort_order=5),
                PublicSiteQuote(quote="D", author="D", sort_order=9),
            ]
        )
        db_session.commit()

        neighbor = find_reorder_neighbor(
            db_session, PublicSiteQuote, PublicSiteQuote.sort_order, 5, "down"
        )

        assert neighbor is not None
        assert neighbor.sort_order == 9
