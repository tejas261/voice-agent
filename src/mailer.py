"""Transactional account emails through a configured TLS SMTP relay."""

import smtplib
import ssl
from email.message import EmailMessage

from src.config import Settings


def configured(settings: Settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_from and settings.public_origin)


def send_account_email(settings: Settings, recipient: str, purpose: str, token: str):
    if not configured(settings):
        raise RuntimeError("Email delivery is not configured")
    path = "/verify" if purpose == "verify" else "/reset"
    url = f"{settings.public_origin}{path}#{token}"
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = "Verify your Listen account" if purpose == "verify" else "Reset your Listen password"
    action = "verify your email address" if purpose == "verify" else "reset your password"
    message.set_content(f"Hello,\n\nOpen this link to {action}:\n{url}\n\nThe link expires in one hour. If you didn't request this, you can ignore this email.\n")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)
