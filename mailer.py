"""E-Mail-Versand - ohne zusaetzliche Abhaengigkeiten.

WICHTIG: Kostenlose Render-Web-Services blockieren ausgehende SMTP-Verbindungen
(Ports 25/465/587). Dort funktioniert nur der Versand ueber eine HTTPS-API:

  Variante A2: SendGrid
    SENDGRID_API_KEY  API-Key (Berechtigung "Mail Send")
    MAIL_FROM         bei SendGrid bestaetigte Absenderadresse (Single Sender)

  Variante A (empfohlen auf Render Free): Brevo
    BREVO_API_KEY   API-Key (Brevo -> SMTP & API -> API Keys)
    MAIL_FROM       bei Brevo bestaetigte Absenderadresse
    MAIL_FROM_NAME  optional, Standard = Seitentausch

  Variante B (nur wo SMTP erlaubt ist, z. B. bezahlter Plan / eigener Server):
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
import html as _html
import json
import smtplib
import ssl
import threading
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger(__name__)


def _brevo_configured() -> bool:
    return bool(os.environ.get("BREVO_API_KEY") and os.environ.get("MAIL_FROM"))


def _sendgrid_configured() -> bool:
    return bool(os.environ.get("SENDGRID_API_KEY") and os.environ.get("MAIL_FROM"))


def _post_json(url: str, headers: dict, payload: dict, label: str, to: str) -> bool:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={**headers, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        log.error("%s-Versand an %s fehlgeschlagen: HTTP %s %s", label, to, exc.code, detail)
    except Exception as exc:
        log.error("%s-Versand an %s fehlgeschlagen: %s", label, to, exc)
    return False


def _send_sendgrid(to: str, subject: str, text: str, html: str | None) -> bool:
    content = [{"type": "text/plain", "value": text}]
    if html:
        content.append({"type": "text/html", "value": html})
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": os.environ["MAIL_FROM"], "name": os.environ.get("MAIL_FROM_NAME", "Seitentausch")},
        "subject": subject, "content": content,
        # keine Link-Umschreibung/Tracking: sonst werden Links ellenlang und wirken auf Yahoo/Gmail verdaechtig
        "tracking_settings": {"click_tracking": {"enable": False, "enable_text": False},
                              "open_tracking": {"enable": False}},
    }
    return _post_json("https://api.sendgrid.com/v3/mail/send",
                      {"Authorization": "Bearer " + os.environ["SENDGRID_API_KEY"]}, payload, "SendGrid", to)


def is_configured() -> bool:
    smtp = bool(os.environ.get("MAIL_SERVER") and os.environ.get("MAIL_USERNAME")
                and os.environ.get("MAIL_PASSWORD"))
    return _brevo_configured() or _sendgrid_configured() or smtp


def _send_brevo(to: str, subject: str, text: str, html: str | None) -> bool:
    payload = {
        "sender": {"name": os.environ.get("MAIL_FROM_NAME", "Seitentausch"), "email": os.environ["MAIL_FROM"]},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": text,
    }
    if html:
        payload["htmlContent"] = html
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email", data=json.dumps(payload).encode(), method="POST",
        headers={"api-key": os.environ["BREVO_API_KEY"], "content-type": "application/json",
                 "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        log.error("Brevo-Versand an %s fehlgeschlagen: HTTP %s %s", to, exc.code, detail)
    except Exception as exc:
        log.error("Brevo-Versand an %s fehlgeschlagen: %s", to, exc)
    return False


def _send_now(to: str, subject: str, text: str, html: str | None) -> bool:
    if _sendgrid_configured():
        return _send_sendgrid(to, subject, text, html)
    if _brevo_configured():
        return _send_brevo(to, subject, text, html)
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


def button_email(site_name: str, greeting: str, paragraphs: list[str], button_label: str,
                 url: str, footnotes: list[str] | None = None) -> str:
    """HTML-Mail im Look der App: Text, ein Button, kleines Kleingedrucktes.

    Mit Inline-Styles und Tabellen, weil Mailprogramme (Outlook, Yahoo, Gmail) kein
    modernes CSS verstehen. Der Link steckt nur im Button, nicht als lange Zeile im Text.
    """
    e = _html.escape
    paras = "".join(f'<p style="margin:0 0 14px;font-size:15px;line-height:1.55;color:#333333;">{e(t)}</p>'
                    for t in paragraphs)
    notes = "".join(f'<p style="margin:0 0 6px;font-size:12px;line-height:1.5;color:#6b6b6b;">{e(t)}</p>'
                    for t in (footnotes or []))
    brand_a, brand_b = e(site_name[:6]), e(site_name[6:])
    return f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f8f8f8;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8f8f8;padding:24px 12px;">
<tr><td align="center">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:10px;">
    <tr><td style="padding:22px 28px 6px;font-family:Georgia,'Times New Roman',serif;font-size:26px;font-weight:600;color:#0c4d77;">{brand_a}<span style="color:#ff7f50;">{brand_b}</span></td></tr>
    <tr><td style="padding:12px 28px 4px;font-family:'Segoe UI',Arial,sans-serif;">
      <p style="margin:0 0 14px;font-size:15px;color:#333333;">{e(greeting)}</p>
      {paras}
    </td></tr>
    <tr><td align="center" style="padding:10px 28px 22px;">
      <a href="{e(url, quote=True)}" style="display:inline-block;background:#ff7f50;color:#ffffff;text-decoration:none;font-family:'Segoe UI',Arial,sans-serif;font-size:15px;font-weight:600;padding:13px 30px;border-radius:5px;">{e(button_label)}</a>
    </td></tr>
    <tr><td style="padding:4px 28px 24px;font-family:'Segoe UI',Arial,sans-serif;border-top:1px solid #e0e0e0;">
      <div style="padding-top:14px;">{notes}</div>
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""
