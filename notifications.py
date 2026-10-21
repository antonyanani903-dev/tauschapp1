"""Benachrichtigungen: Zaehler, Ereignisse fuer die Live-Abfrage und E-Mails.

Bewusst ohne WebSockets/Push-Dienst: Die Seite fragt alle paar Sekunden
/api/notifications ab (billig und auf dem kostenlosen Render-Plan zuverlaessig).
Wer die App gerade nicht offen hat, wird per E-Mail benachrichtigt (siehe mailer.py).
"""
from datetime import datetime, timedelta

from sqlalchemy import and_, or_

import mailer
from extensions import db
from models import Block, Match, Message, User

ONLINE_WINDOW = timedelta(minutes=5)      # so lange gilt jemand als "gerade aktiv"
MAIL_COOLDOWN = timedelta(minutes=60)     # max. 1 Nachrichten-Mail pro Chat und Stunde


def blocked_ids(uid: int) -> set:
    """Unsichtbare User: von uid blockiert, uid blockiert hat, oder vom Betreiber gesperrt."""
    rows = Block.query.filter(or_(Block.blocker_id == uid, Block.blocked_id == uid)).all()
    hidden = {b.blocked_id if b.blocker_id == uid else b.blocker_id for b in rows}
    banned = {row[0] for row in db.session.query(User.id).filter(User.is_banned.is_(True)).all()}
    return hidden | banned  # gesperrte Konten sind fuer alle unsichtbar


def visible_matches_query(uid: int):
    """Matches von uid ohne Chats mit blockierten Personen."""
    q = Match.query.filter(or_(Match.user1_id == uid, Match.user2_id == uid))
    hidden = blocked_ids(uid)
    if hidden:
        q = q.filter(~Match.user1_id.in_(hidden), ~Match.user2_id.in_(hidden))
    return q


def _my_match_ids(uid: int):
    return [m.id for m in visible_matches_query(uid).with_entities(Match.id).all()]


def new_matches_query(uid: int):
    return visible_matches_query(uid).filter(
        Match.status == "active",
        or_(
            and_(Match.user1_id == uid, Match.user1_seen.is_(False)),
            and_(Match.user2_id == uid, Match.user2_seen.is_(False)),
        ),
    )


def unread_messages_query(uid: int):
    ids = _my_match_ids(uid)
    if not ids:
        return Message.query.filter(db.false())
    return Message.query.filter(
        Message.match_id.in_(ids), Message.sender_id != uid, Message.read_at.is_(None)
    )


def counts(uid: int) -> dict:
    new_matches = new_matches_query(uid).count()
    unread = unread_messages_query(uid).count()
    return {"new_matches": new_matches, "unread_messages": unread, "total": new_matches + unread}


def watermark(uid: int) -> dict:
    """Hoechste IDs, die der Nutzer schon kennt - Ausgangspunkt fuer 'neue' Ereignisse."""
    last_match = db.session.query(db.func.max(Match.id)).filter(
        or_(Match.user1_id == uid, Match.user2_id == uid)).scalar() or 0
    ids = _my_match_ids(uid)
    last_msg = 0
    if ids:
        last_msg = db.session.query(db.func.max(Message.id)).filter(
            Message.match_id.in_(ids)).scalar() or 0
    return {"match": last_match, "message": last_msg}


def events_since(uid: int, since_match: int, since_message: int, current_match: int | None = None,
                 upto_match: int | None = None, upto_message: int | None = None):
    """Neue Ereignisse fuer Toasts: neue Matches und neue ungelesene Nachrichten.

    upto_*: obere Grenze (= Wasserstand zu Beginn der Abfrage), damit zwischen
    Abfrage und Wasserstand eintreffende Ereignisse nicht verloren gehen.
    """
    events = []

    match_q = new_matches_query(uid).filter(Match.id > since_match)
    if upto_match is not None:
        match_q = match_q.filter(Match.id <= upto_match)
    for m in match_q.order_by(Match.id).all():
        other = m.other_user(uid)
        events.append({
            "type": "match", "id": m.id, "match_id": m.id,
            "title": "Neues Match! 🎉",
            "text": f"{other.name} möchte „{m.my_book(uid).title}“ gegen „{m.their_book(uid).title}“ tauschen.",
            "url": f"/chat/{m.id}",
        })

    msgs = unread_messages_query(uid).filter(Message.id > since_message)
    if upto_message is not None:
        msgs = msgs.filter(Message.id <= upto_message)
    if current_match:
        msgs = msgs.filter(Message.match_id != current_match)
    latest_per_match = {}
    for msg in msgs.order_by(Message.id).all():
        latest_per_match[msg.match_id] = msg  # nur die jeweils neueste je Chat
    for msg in latest_per_match.values():
        preview = msg.content if len(msg.content) <= 80 else msg.content[:77] + "…"
        events.append({
            "type": "message", "id": msg.id, "match_id": msg.match_id,
            "title": f"Neue Nachricht von {msg.sender.name}",
            "text": preview, "url": f"/chat/{msg.match_id}",
        })
    return events


# ---------- E-Mail ----------

def _link(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def email_new_match(match: Match, recipient: User, base_url: str, site_name: str = "Seitentausch"):
    """Mail an die Person, die NICHT selbst das Match ausgeloest hat."""
    if not recipient.email_notifications:
        return False
    other = match.other_user(recipient.id)
    text = (
        f"Hallo {recipient.name},\n\n"
        f"gute Nachrichten: Du hast ein Match auf {site_name}!\n\n"
        f"{other.name} und du interessiert euch jeweils für ein Buch des anderen:\n"
        f"  Du gibst:     {match.my_book(recipient.id).title}\n"
        f"  Du bekommst:  {match.their_book(recipient.id).title}\n\n"
        f"Schreibt euch jetzt und vereinbart ein Treffen:\n"
        f"{_link(base_url, f'/chat/{match.id}')}\n\n"
        f"Tipp: Triff dich für den Tausch an einem öffentlichen Ort.\n\n"
        f"Du möchtest keine E-Mails mehr? Das kannst du in deinem Profil ausschalten:\n"
        f"{_link(base_url, '/profile')}\n"
    )
    return mailer.send_mail(recipient.email, f"Neues Match auf {site_name} 🎉", text)


def email_new_message(match: Match, message: Message, recipient: User, base_url: str,
                      site_name: str = "Seitentausch"):
    """Mail bei neuer Nachricht - nur wenn der Empfaenger gerade nicht aktiv ist
    und nicht schon kuerzlich eine Mail zu diesem Chat bekam. Enthaelt NICHT den Inhalt."""
    if not recipient.email_notifications:
        return False
    now = datetime.utcnow()
    if recipient.last_seen and now - recipient.last_seen < ONLINE_WINDOW:
        return False  # sieht es ohnehin live auf der Seite

    earlier_unread = Message.query.filter(
        Message.match_id == match.id, Message.sender_id == message.sender_id,
        Message.read_at.is_(None), Message.id != message.id,
    ).count()
    last_mail = match.notified_at(recipient.id)
    if earlier_unread and last_mail and now - last_mail < MAIL_COOLDOWN:
        return False  # es gibt schon eine offene Benachrichtigung zu diesem Chat

    sender = message.sender
    text = (
        f"Hallo {recipient.name},\n\n"
        f"{sender.name} hat dir auf {site_name} geschrieben "
        f"(Tausch: „{match.my_book(recipient.id).title}“ gegen „{match.their_book(recipient.id).title}“).\n\n"
        f"Nachricht lesen und antworten:\n{_link(base_url, f'/chat/{match.id}')}\n\n"
        f"E-Mail-Benachrichtigungen kannst du in deinem Profil ausschalten:\n"
        f"{_link(base_url, '/profile')}\n"
    )
    sent = mailer.send_mail(recipient.email, f"Neue Nachricht von {sender.name} auf {site_name}", text)
    if sent:
        match.set_notified(recipient.id, now)
        db.session.commit()
    return sent
