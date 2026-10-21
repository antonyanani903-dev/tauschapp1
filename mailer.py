"""E-Mail-Versand per SMTP (z. B. IONOS) - ohne zusaetzliche Abhaengigkeiten.

Konfiguration ueber Umgebungsvariablen (Render -> Environment):
  MAIL_SERVER     z. B. smtp.ionos.de
  MAIL_PORT       587 (STARTTLS, Standard) oder 465 (SSL)
  MAIL_USERNAME   vollstaendige Absenderadresse, z. B. tausch@deine-domain.de
  MAIL_PASSWORD   Passwort dieses Postfachs
  MAIL_FROM       optional, Standard = MAIL_USERNAME
  MAIL_FROM_NAME  optional, Standard = Seitentausch

Ist nichts konfiguriert, wird die Mail nur ins Server-Log geschrieben.
Dann funktioniert die App weiter, aber "Passwort vergessen" und
E-Mail-Benachrichtigungen erreichen niemanden.
"""
import logging
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(os.environ.get("MAIL_SERVER") and os.environ.get("MAIL_USERNAME")
                and os.environ.get("MAIL_PASSWORD"))


def _send_now(to: str, subject: str, text: str, html: str | None) -> bool:
    server = os.environ["MAIL_SERVER"]
    port = int(os.environ.get("MAIL_PORT", "587"))
    user = os.environ["MAIL_USERNAME"]
    password = os.environ["MAIL_PASSWORD"]
    sender = os.environ.get("MAIL_FROM") or user
    name = os.environ.get("MAIL_FROM_NAME", "Seitentausch")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((name, sender))
    msg["To"] = to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    try:
        context = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(server, port, context=context, timeout=15) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(server, port, timeout=15) as smtp:
                smtp.starttls(context=context)
                smtp.login(user, password)
                smtp.send_message(msg)
        return True
    except Exception as exc:
        log.error("Mailversand an %s fehlgeschlagen: %s", to, exc)
        return False


def send_mail(to: str, subject: str, text: str, html: str | None = None,
              background: bool = True) -> bool:
    """Verschickt eine Mail. Gibt False zurueck, wenn kein SMTP konfiguriert ist."""
    if not is_configured():
        log.warning("MAIL nicht konfiguriert - Mail an %s NICHT versendet.\nBetreff: %s\n%s",
                    to, subject, text)
        return False
    if background:
        threading.Thread(target=_send_now, args=(to, subject, text, html), daemon=True).start()
        return True
    return _send_now(to, subject, text, html)
