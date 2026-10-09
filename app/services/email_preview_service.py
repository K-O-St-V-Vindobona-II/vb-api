"""Dummy data for the live preview of the registered email templates."""

from fastapi import HTTPException

from app.core.mailer import render_template
from app.schemas.tracking import EmailTemplatePreview
from app.services import tracking_service


def _format_preview_value(
    _key: str,
    value: str | None,
    _diff: dict[str, dict[str, str | None]],
) -> str:
    return value if value is not None else "-"


TEMPLATE_PREVIEW_DATA: dict[str, dict[str, object]] = {
    "password-reset": {
        "reset_link": "https://intern.vindobona2.at/reset-password?token=abc123",
    },
    "entry-changed": {
        "modifier_cn": "Max Mustermann v/o Testikus",
        "entry_type": "member",
        "entry_cn": "Franz Beispiel v/o Musterknabe",
        "change_type": "update",
        "diff": {
            "nachname": {"old": "Beispiel", "new": "Beispiel-Neu"},
            "email": {"old": "alt@example.com", "new": "neu@example.com"},
        },
        "format_value": _format_preview_value,
    },
    "member-change-request-submitted": {
        "member_cn": "Franz Beispiel v/o Musterknabe",
        "diff": {
            "nachname": {"old": "Beispiel", "new": "Beispiel-Neu"},
            "email": {"old": "alt@example.com", "new": "neu@example.com"},
        },
        "format_value": _format_preview_value,
    },
    "member-change-request-resolved": {
        "approved": {
            "email": {"old": "alt@example.com", "new": "neu@example.com"},
        },
        "rejected": {
            "nachname": {"old": "Beispiel", "new": "Beispiel-Neu"},
        },
        "format_value": _format_preview_value,
    },
    "own-image-changed": {
        "member_cn": "Franz Beispiel v/o Musterknabe",
        "action_label": "ein neues Profilbild hochgeladen",
        "action": "upload",
    },
    "birthday": {
        "name": "Max Mustermann v/o Testikus",
        "age": 25,
    },
    "debtor_reminder": {
        "name": "Max Mustermann v/o Testikus",
        "fee": "15,00",
        "debt": "90,00",
        "target": "30. Juni 2026",
        "sender_name": "Franz Beispiel v/o Musterknabe",
    },
    "chronicles": {
        "start": "30. Juni 2026",
        "end": "6. Juli 2026",
        "anniversaries": {
            "vbw": {
                "lebend": {
                    "geburtsdatum": [
                        {
                            "cn": "Max Mustermann v/o Testikus",
                            "date": "1. Juli",
                            "years": 50,
                        },
                    ],
                    "aufnahmedatum": [
                        {
                            "cn": "Franz Beispiel v/o Musterknabe",
                            "date": "3. Juli",
                            "years": 10,
                        },
                    ],
                },
            },
        },
    },
    "archive_health_check": {
        "missing": ["archive/store/abc123"],
        "orphans": ["archive/store/def456"],
        "unsorted_count": 3,
    },
    "standesdb_health_check": {
        "missing": [],
        "orphans": ["standesdb/images/def456"],
    },
    "public-contact-form": {
        "name": "Max Mustermann",
        "email": "max@example.com",
        "message": "Hallo, ich interessiere mich für Vindobona II.",
    },
}


def build_preview(template_key: str) -> EmailTemplatePreview:
    """Render a registered template with its dummy data."""
    entry = next(
        (
            t
            for t in tracking_service.EMAIL_TEMPLATE_REGISTRY
            if t["key"] == template_key
        ),
        None,
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Template nicht gefunden")

    preview_data = TEMPLATE_PREVIEW_DATA.get(template_key)
    if not preview_data:
        raise HTTPException(
            status_code=404,
            detail="Keine Vorschaudaten verfügbar",
        )

    html = render_template(entry["file"], **preview_data)
    return EmailTemplatePreview(
        template_key=template_key,
        template_name=entry["name"],
        html=html,
    )
