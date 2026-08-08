"""Shared full-text-search query-construction helpers.

Used by every service that runs a Postgres tsvector/pg_trgm search
(archive_service, standesdb_service, p4x_fee_balance_service) - kept in
one place so the SQL-safety reasoning below is written, verified, and
maintained exactly once.
"""

from sqlalchemy import Text, cast, func, select
from sqlalchemy.orm import Session


def build_prefix_tsquery_text(db: Session, query: str) -> str:
    """Turns a raw, untrusted user search string into Postgres prefix-
    tsquery syntax, e.g. "Kassa Vorstand" -> "'kassa':* & 'vorstand':*".

    Plain word-form matching (websearch_to_tsquery() alone) would miss
    "Kassa" -> "Kassabericht": Postgres's 'german' text search config
    stems suffixes but never splits compound words, so a bare word never
    matches as a substring of a longer one the way a plain ILIKE would.
    Appending :* (prefix match) to every lexeme recovers exactly that
    case - and also short abbreviations/partially-typed words (e.g.
    "Schim" while typing "Schimpl", "BC"/"MC" committee abbreviations),
    verified empirically to tokenize and prefix-match fine even at 2
    characters.

    websearch_to_tsquery() does the actual parsing of the raw string - it
    never raises on malformed input (unlike to_tsquery()), so building the
    prefix query from its already-safely-parsed text representation stays
    safe against arbitrary user text (quotes, &/|/! operators, ...).
    Returns "" for input with no real lexemes (stop words only, or empty
    after Postgres's own tokenizing) - the caller treats that as "no
    results", matching plain ILIKE's behavior for a query that matches
    nothing.
    """
    safe_query_text = cast(func.websearch_to_tsquery("german", query), Text)
    prefixed_text = func.regexp_replace(safe_query_text, r"'(\w+)'", r"'\1':*", "g")
    return db.execute(select(cast(prefixed_text, Text))).scalar() or ""
