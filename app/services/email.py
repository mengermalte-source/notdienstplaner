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


def send_wish_deadline_reminder(user_email: str, user_name: str, deadline: date, period_name: str):
    body = f"""
    <p>Hallo {user_name},</p>
    <p>Bitte geben Sie Ihre Wünsche für den <strong>{period_name}</strong>
    bis zum <strong>{deadline.strftime('%d.%m.%Y')}</strong> ein.</p>
    <p><a href="{settings.app_base_url}/me/wishes">Wünsche eingeben</a></p>
    """
    _send(user_email, f"Erinnerung: Wünsche bis {deadline.strftime('%d.%m.%Y')} einreichen", body)
