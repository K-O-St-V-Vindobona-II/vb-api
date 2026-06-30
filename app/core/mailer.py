import logging
import os
import smtplib
from datetime import UTC, date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

_current_dir = Path(__file__).resolve().parent
_templates_dir = _current_dir.parent / "templates" / "email"
_jinja_env = Environment(loader=FileSystemLoader(str(_templates_dir)))  # noqa: S701


def _build_from_header() -> tuple[str, str]:
    from_email = os.environ["SMTP_FROM_EMAIL"]
    from_name = os.environ.get("SMTP_FROM_NAME", "Vindobona NG")
    return from_email, f'"{from_name}" <{from_email}>'


def _send_message(msg: MIMEMultipart, recipients: str | list[str]) -> None:
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ["SMTP_PORT"])
    smtp_user = os.environ.get("SMTP_USER", "null")
    smtp_password = os.environ.get("SMTP_PASSWORD", "null")
    from_email = os.environ["SMTP_FROM_EMAIL"]

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
) -> None:
    try:
        from app.db.database import SessionLocal
        from app.models.sent_email import SentEmail

        db = SessionLocal()
        try:
            now = datetime.now(UTC)
            entry = SentEmail(
                mail_from=from_addr or os.environ.get("SMTP_FROM_EMAIL", ""),
                to=to_str,
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
    except Exception:
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
) -> None:
    if not to_emails:
        return

    from_email = from_addr or os.environ["SMTP_FROM_EMAIL"]
    from_name = os.environ.get("SMTP_FROM_NAME", "Vindobona")
    from_header = f'"{from_name}" <{from_email}>'

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = ", ".join(to_emails)
    if reply_to:
        msg["Reply-To"] = reply_to
    if bcc_emails:
        msg["Bcc"] = ", ".join(bcc_emails)
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    all_recipients = list(to_emails)
    if bcc_emails:
        all_recipients.extend(bcc_emails)

    _send_message(msg, all_recipients)
    _log_sent_email(
        ", ".join(to_emails),
        subject,
        html_content,
        template_key,
        from_header,
    )


def send_reset_email(to_email: str, token: str) -> None:
    _from_email, from_header = _build_from_header()
    frontend_url = os.environ["FRONTEND_RESET_URL"]
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
    msg["Subject"] = "Passwort zurücksetzen - Vindobona NG"
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

    now = datetime.now(UTC)
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
