import contextlib
import logging
import smtplib
from datetime import UTC, date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings, require_setting
from app.core.datetime_utils import get_app_timezone
from app.db.database import SessionLocal
from app.models.sent_email import SentEmail

logger = logging.getLogger(__name__)

_current_dir = Path(__file__).resolve().parent
_templates_dir = _current_dir.parent / "templates" / "email"
# Autoescaping is mandatory here: several templates render member-submitted
# free text (e.g. member_change_request_submitted.html renders self-service
# Stammdaten fields), and public_contact_form.html renders anonymous website
# visitor input. Without it, any of those fields could inject raw HTML into
# an email an admin opens in their mail client.
_jinja_env = Environment(
    loader=FileSystemLoader(str(_templates_dir)),
    autoescape=select_autoescape(["html"]),
)


def _build_from_header() -> tuple[str, str]:
    settings = get_settings()
    from_email = require_setting(settings.smtp_from_email, "SMTP_FROM_EMAIL")
    from_name = settings.smtp_from_name
    return from_email, f'"{from_name}" <{from_email}>'


def _send_message(msg: MIMEMultipart, recipients: str | list[str]) -> None:
    settings = get_settings()
    smtp_host = require_setting(settings.smtp_host, "SMTP_HOST")
    smtp_port = require_setting(settings.smtp_port, "SMTP_PORT")
    smtp_user = settings.smtp_user
    smtp_password = settings.smtp_password
    from_email = require_setting(settings.smtp_from_email, "SMTP_FROM_EMAIL")

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            if smtp_user and smtp_user.lower() != "null":
                server.login(smtp_user, smtp_password)
            server.sendmail(from_email, recipients, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            if server.has_extn("STARTTLS"):
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_user.lower() != "null":
                server.login(smtp_user, smtp_password)
            server.sendmail(from_email, recipients, msg.as_string())


def _log_sent_email(
    to_str: str,
    subject: str,
    html_body: str,
    template_key: str,
    from_addr: str | None = None,
    bcc_str: str | None = None,
) -> None:
    try:
        db = SessionLocal()
        try:
            now = datetime.now(UTC)
            entry = SentEmail(
                mail_from=from_addr
                or require_setting(get_settings().smtp_from_email, "SMTP_FROM_EMAIL"),
                to=to_str,
                bcc=bcc_str,
                subject=subject,
                body=html_body,
                headers=template_key,
                mailer="smtp",
                created_at=now,
                updated_at=now,
            )
            db.add(entry)
            db.commit()
            logger.info("Email logged: template=%s, to=%s", template_key, to_str)
        finally:
            db.close()
    except SQLAlchemyError:
        logger.exception("Failed to log sent email")


def render_template(
    template_name: str,
    **kwargs: object,
) -> str:
    template = _jinja_env.get_template(template_name)
    return template.render(**kwargs)


def send_to_recipients(
    to_emails: list[str],
    subject: str,
    html_content: str,
    template_key: str = "generic",
    from_addr: str | None = None,
    reply_to: str | None = None,
    bcc_emails: list[str] | None = None,
    from_name: str | None = None,
) -> None:
    if not to_emails and not bcc_emails:
        return

    settings = get_settings()
    from_email = from_addr or require_setting(
        settings.smtp_from_email, "SMTP_FROM_EMAIL"
    )
    resolved_from_name = from_name or settings.smtp_from_name
    from_header = f'"{resolved_from_name}" <{from_email}>'

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = ", ".join(to_emails) if to_emails else "Undisclosed-Recipients:;"
    if reply_to:
        msg["Reply-To"] = reply_to
    # Bcc is deliberately never attached as a header on the sent message —
    # bcc must stay envelope-only (SMTP RCPT TO via `all_recipients` below).
    # A "Bcc" header on the actual message would leak the full bcc list to
    # anyone who views raw message headers, defeating the point of bcc.
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    all_recipients = list(to_emails)
    if bcc_emails:
        all_recipients.extend(bcc_emails)

    _send_message(msg, all_recipients)
    _log_sent_email(
        ", ".join(to_emails) if to_emails else "(bcc-only)",
        subject,
        html_content,
        template_key,
        from_header,
        bcc_str=", ".join(bcc_emails) if bcc_emails else None,
    )


def send_reset_email(to_email: str, token: str) -> None:
    _from_email, from_header = _build_from_header()
    frontend_url = require_setting(
        get_settings().frontend_reset_url, "FRONTEND_RESET_URL"
    )
    reset_link = f"{frontend_url}?token={token}&email={to_email}"

    template = _jinja_env.get_template("password_reset.html")
    html_content = template.render(reset_link=reset_link)

    text_content = (
        f"Hallo!\n\n"
        f"Bitte nutze folgenden Link, um dein Passwort"
        f" zurueckzusetzen:\n{reset_link}\n\n"
        f"Dieser Link ist aus Sicherheitsgruenden"
        f" fuer 20 Minuten gueltig."
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Passwort zurücksetzen - Vindobona"
    msg["From"] = from_header
    msg["To"] = to_email
    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    _send_message(msg, to_email)
    _log_sent_email(
        to_email, msg["Subject"], html_content, "password-reset", from_header
    )


def _send_to_multiple(
    to_emails: list[str],
    subject: str,
    html_content: str,
    text_content: str,
    template_key: str = "generic",
) -> None:
    if not to_emails:
        return

    _, from_header = _build_from_header()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = ", ".join(to_emails)
    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    _send_message(msg, to_emails)
    _log_sent_email(
        ", ".join(to_emails), subject, html_content, template_key, from_header
    )


_MONTHS_DE = [
    "",
    "Jänner",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]


def _resolve_date_accuracy(key: str, diff: dict[str, dict[str, object]]) -> int:
    acc_key = key + "_accuracy"
    if acc_key not in diff:
        return 0
    acc_val = diff[acc_key]
    raw = acc_val.get("new", 0)
    return int(raw) if isinstance(raw, (int, float)) else 0


def _format_date_by_accuracy(value: date, accuracy: int) -> str:
    if accuracy == 0:
        return "-"
    if accuracy == 1:
        return str(value.year)
    if accuracy == 2:
        return f"{_MONTHS_DE[value.month]} {value.year}"
    return f"{value.day}. {_MONTHS_DE[value.month]} {value.year}"


def _format_diff_value(
    key: str, value: object, diff: dict[str, dict[str, object]]
) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "-"
    if isinstance(value, date) and key.endswith("datum"):
        accuracy = _resolve_date_accuracy(key, diff)
        return _format_date_by_accuracy(value, accuracy)
    return str(value)


def send_entry_changed_email(
    to_emails: list[str],
    entry_type: str,
    entry_cn: str,
    diff: dict[str, dict[str, object]],
    change_type: str,
    modifier_cn: str,
) -> None:
    if not to_emails or not diff:
        return

    now = datetime.now(get_app_timezone())
    subject = (
        f"Bearbeitung in der Verbindungsdatenbank ({now.strftime('%Y-%m-%d %H:%M')})"
    )

    template = _jinja_env.get_template("entry_changed.html")
    html_content = template.render(
        modifier_cn=modifier_cn,
        entry_type=entry_type,
        entry_cn=entry_cn,
        change_type=change_type,
        diff=diff,
        format_value=_format_diff_value,
    )

    text_lines = [
        f"{modifier_cn} hat eine Änderung in der Verbindungsdatenbank vorgenommen:",
        "",
        f"Datensatz: {'Mitglied' if entry_type == 'member' else 'Kontakt'}"
        f' "{entry_cn}"',
        f"Art: {'Neuanlage' if change_type == 'store' else 'Änderung'}",
        "",
    ]
    for key, values in diff.items():
        if key.endswith("_accuracy"):
            continue
        text_lines.append(f"{key}:")
        text_lines.append(f"  alt: {_format_diff_value(key, values.get('old'), diff)}")
        text_lines.append(f"  neu: {_format_diff_value(key, values.get('new'), diff)}")
        text_lines.append("")

    _send_to_multiple(
        to_emails,
        subject,
        html_content,
        "\n".join(text_lines),
        template_key="entry-changed",
    )


def _prepare_diff_for_display(
    diff: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    """proposed_data comes back from JSONB with geburtsdatum's value as a
    plain ISO string, not a date object - _format_diff_value only applies
    its fuzzy-date formatting to actual date instances. Converts just that
    one field back so the email renders "4. Mai 2001" instead of the raw
    "2001-05-04"."""
    prepared: dict[str, dict[str, object]] = {}
    for key, values in diff.items():
        new_values = dict(values)
        for side in ("old", "new"):
            val = new_values.get(side)
            if key == "geburtsdatum" and isinstance(val, str):
                with contextlib.suppress(ValueError):
                    new_values[side] = date.fromisoformat(val)
        prepared[key] = new_values
    return prepared


def send_member_change_request_submitted_email(
    to_emails: list[str],
    member_cn: str,
    diff: dict[str, dict[str, object]],
) -> None:
    """Notifies the submitting member's org admin(s) that a self-service
    Stammdaten change request is waiting for review."""
    if not to_emails or not diff:
        return

    subject = f"Neuer Änderungsantrag: {member_cn}"
    prepared_diff = _prepare_diff_for_display(diff)

    template = _jinja_env.get_template("member_change_request_submitted.html")
    html_content = template.render(
        member_cn=member_cn,
        diff=prepared_diff,
        format_value=_format_diff_value,
    )

    send_to_recipients(
        to_emails,
        subject,
        html_content,
        template_key="member-change-request-submitted",
    )


def send_member_change_request_resolved_email(
    to_email: str,
    diff: dict[str, dict[str, object]],
    field_decisions: dict[str, str],
) -> None:
    """Notifies the member of the outcome of their own change request,
    approved and rejected fields shown in clearly separate sections."""
    if not to_email or not diff:
        return

    prepared_diff = _prepare_diff_for_display(diff)
    approved = {
        key: values
        for key, values in prepared_diff.items()
        if field_decisions.get(key) == "approved"
    }
    rejected = {
        key: values
        for key, values in prepared_diff.items()
        if field_decisions.get(key) == "rejected"
    }

    template = _jinja_env.get_template("member_change_request_resolved.html")
    html_content = template.render(
        approved=approved,
        rejected=rejected,
        format_value=_format_diff_value,
    )

    send_to_recipients(
        [to_email],
        "Dein Änderungsantrag wurde bearbeitet",
        html_content,
        template_key="member-change-request-resolved",
    )


def send_own_image_changed_email(
    to_emails: list[str],
    member_cn: str,
    action: str,
) -> None:
    """Notifies a member's org standesdb admins that the member changed one
    of their own profile images via self-service (upload/update/delete).
    Purely informational - self-service image changes need no admin
    approval, this is not a gate."""
    if not to_emails:
        return

    action_label = {
        "upload": "ein neues Profilbild hochgeladen",
        "update": "ein Profilbild bearbeitet",
        "delete": "ein Profilbild gelöscht",
    }.get(action, "ein Profilbild geändert")

    subject = f"Profilbild geändert: {member_cn}"

    template = _jinja_env.get_template("own_image_changed.html")
    html_content = template.render(
        member_cn=member_cn,
        action_label=action_label,
        action=action,
    )

    send_to_recipients(
        to_emails,
        subject,
        html_content,
        template_key="own-image-changed",
    )
