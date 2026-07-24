from fastapi import HTTPException, status
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session


def get_valid_tables(db: Session) -> list[str]:
    bind = db.get_bind()
    inspector = inspect(bind)
    return sorted(inspector.get_table_names())


def get_table_data(
    db: Session,
    table_name: str,
    page: int,
    page_size: int,
) -> dict[str, object]:
    valid_tables = get_valid_tables(db)
    if table_name not in valid_tables:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tabelle nicht gefunden.",
        )

    bind = db.get_bind()
    inspector = inspect(bind)
    raw_columns = inspector.get_columns(table_name)
    pk_constraint = inspector.get_pk_constraint(table_name)
    pk_cols = set(pk_constraint.get("constrained_columns", []))

    columns = [
        {
            "name": c["name"],
            "type": str(c["type"]),
            "nullable": c.get("nullable", True),
            "primary_key": c["name"] in pk_cols,
        }
        for c in raw_columns
    ]

    quoted = f'"{table_name}"'
    total = db.execute(text(f"SELECT COUNT(*) FROM {quoted}")).scalar()  # noqa: S608

    offset = (page - 1) * page_size
    rows_raw = (
        db.execute(
            text(f"SELECT * FROM {quoted} LIMIT :limit OFFSET :offset"),  # noqa: S608
            {"limit": page_size, "offset": offset},
        )
        .mappings()
        .all()
    )

    rows = [
        {k: str(v) if v is not None else None for k, v in row.items()}
        for row in rows_raw
    ]

    return {
        "table_name": table_name,
        "columns": columns,
        "rows": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
