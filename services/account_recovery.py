"""Expiring, password-bound recovery links, delivered only through configured TLS SMTP."""
import hashlib
import hmac
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlparse

from flask import current_app
from itsdangerous import URLSafeTimedSerializer


def credential_version(user):
    return hmac.new(str(current_app.config["SECRET_KEY"]).encode(),
                    user.password_hash.encode(), hashlib.sha256).hexdigest()


def serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="aistora-password-reset-v1")


def recovery_available():
    config = current_app.config
    url = urlparse(config.get("PUBLIC_APP_URL", ""))
    return bool(config.get("SMTP_HOST") and config.get("SMTP_FROM")
                and url.scheme == "https" and url.netloc and not url.username
                and not url.password and not url.query and not url.fragment
                and url.path in ("", "/"))


def make_reset_token(user):
    return serializer().dumps({"user_id": user.id, "version": credential_version(user)})


def send_reset_email(user):
    config = current_app.config
    token = make_reset_token(user)
    url = config["PUBLIC_APP_URL"].rstrip("/") + "/app#reset=" + token
    message = EmailMessage()
    message["Subject"] = "Reset your AIStora password"
    message["From"] = config["SMTP_FROM"]
    message["To"] = user.email
    message.set_content(
        "Use this link to choose a new AIStora password. It expires in 30 minutes "
        "and stops working after your password changes.\n\n" + url +
        "\n\nIf you didn't request this, you can ignore this email."
    )
    context = ssl.create_default_context()
    port = int(config.get("SMTP_PORT", 587))
    if port == 465:
        connection = smtplib.SMTP_SSL(config["SMTP_HOST"], port, timeout=10, context=context)
    else:
        connection = smtplib.SMTP(config["SMTP_HOST"], port, timeout=10)
    with connection as smtp:
        if port != 465:
            smtp.starttls(context=context)
        if config.get("SMTP_USERNAME"):
            smtp.login(config["SMTP_USERNAME"], config.get("SMTP_PASSWORD", ""))
        smtp.send_message(message)
