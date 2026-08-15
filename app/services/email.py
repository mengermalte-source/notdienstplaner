import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date
from app.config import settings


def _send(to: str, subject: str, body_html: str):
    if not settings.smtp_host:
        print(f"[EMAIL] To: {to} | Subject: {subject}")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.attach(MIMEText(body_html, "html"))
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
        if settings.smtp_user:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


def send_coverage_request(target_email: str, target_name: str, absent_name: str,
                          shift_date: date, message: str = ""):
    date_str = shift_date.strftime("%d.%m.%Y (%A)")
    msg_block = f"<p><em>Nachricht: {message}</em></p>" if message else ""
    body = f"""
    <h2>Vertretungsanfrage</h2>
    <p>Hallo {target_name},</p>
    <p><strong>{absent_name}</strong> kann am <strong>{date_str}</strong> nicht Dienst machen.
    Sie werden als Vertretung angefragt.</p>
    {msg_block}
    <p><a href="{settings.app_base_url}/me/swaps">Zur Tauschbörse</a></p>
    """
    _send(target_email, f"Vertretungsanfrage für {shift_date.strftime('%d.%m.%Y')}", body)


def send_schedule_published(user_email: str, user_name: str, period_name: str,
                             my_dates: list[date]):
    dates_html = "".join(f"<li>{d.strftime('%d.%m.%Y (%A)')}</li>" for d in sorted(my_dates))
    body = f"""
    <h2>Notdienstplan veröffentlicht: {period_name}</h2>
    <p>Hallo {user_name},</p>
    <p>der Notdienstplan wurde freigegeben. Ihre Dienste:</p>
    <ul>{dates_html}</ul>
    <p><a href="{settings.app_base_url}/me">Zum Notdienstplaner</a></p>
    """
    _send(user_email, f"Notdienstplan veröffentlicht: {period_name}", body)
