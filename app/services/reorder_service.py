from typing import Literal

from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, Session


def find_reorder_neighbor[ModelT: DeclarativeBase](
    db: Session,
    model_cls: type[ModelT],
    order_column: InstrumentedAttribute[int],
    current_sort_order: int,
    direction: Literal["up", "down"],
) -> ModelT | None:
    """Find the row immediately above/below `current_sort_order` in a
    sort_order-ordered admin list, or None if `current_sort_order` is
    already at that end.

    Extracted from public_gallery_service.move_image()'s original
    neighbor-lookup logic so every reorderable admin list (programm
    hints, quotes, social links, gallery images) shares one
    implementation instead of near-identical copies (CLAUDE.md strict
    DRY). The actual sort_order swap + commit stays with each caller
    (a couple of concretely-typed lines) rather than living here too -
    trying to also generify the attribute read/write would require a
    structural Protocol over SQLAlchemy's `Mapped[int]` descriptor,
    which doesn't type-check cleanly across unrelated model classes.
    """
    comparator = order_column < current_sort_order
    ordering = order_column.desc()
    if direction == "down":
        comparator = order_column > current_sort_order
        ordering = order_column.asc()

    return db.query(model_cls).filter(comparator).order_by(ordering).first()
