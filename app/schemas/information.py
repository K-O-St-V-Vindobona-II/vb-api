from pydantic import BaseModel


class PaymentInfoEntry(BaseModel):
    """One of the two fixed payment-info entries (Aktivitas / Altherrenschaft)
    shown on the public /payment page - see get_payment_info() in
    app/services/information_service.py."""

    title: str
    name: str
    iban: str
    # Nullable on P4xAccount, so a value of None (vs. missing entirely) is a
    # legitimate, pre-existing possibility here.
    bic: str | None
    fee: str
