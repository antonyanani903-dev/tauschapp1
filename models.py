"""Datenmodelle fuer die Buchtausch-App.

Kernidee des Matchings:
- Ein User "swiped" auf Buecher anderer User (liked=True oder liked=False).
- Ein Match entsteht erst, wenn BEIDE Seiten sich gegenseitig ein Buch
  des jeweils anderen geliked haben (echte Tausch-Reziprozitaet).

Wichtig fuer bestehende Datenbanken: db.create_all() legt nur NEUE Tabellen an.
Neue Spalten in bestehenden Tabellen ergaenzt migrate.ensure_columns() beim Start
automatisch - deshalb haben alle neuen Spalten einen server_default.
"""
import hashlib
from datetime import datetime

import sqlalchemy as sa
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Standort (nur grob: PLZ/Ort + daraus berechnete Koordinaten, keine Strasse)
    city = db.Column(db.String(100))
    plz = db.Column(db.String(10))
    country = db.Column(db.String(2), nullable=False, server_default="DE", default="DE")
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    # Suchradius im Entdecken-Feed; NULL = ueberall
    radius_km = db.Column(db.Integer, server_default=sa.text("50"), default=50)

    # Benachrichtigungen / Aktivitaet
    email_notifications = db.Column(
        db.Boolean, nullable=False, server_default=sa.true(), default=True
    )
    last_seen = db.Column(db.DateTime)

    # Moderation / Einwilligung
    is_banned = db.Column(
        db.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    terms_accepted_at = db.Column(db.DateTime)

    books = db.relationship(
        "Book", backref="owner", lazy=True, cascade="all, delete-orphan"
    )

    def get_id(self) -> str:
        # Session-ID ist an das Passwort gekoppelt: Nach einer Passwortaenderung
        # oder einem Reset werden alle anderen angemeldeten Geraete abgemeldet.
        return f"{self.id}:{self.password_fingerprint()}"

    def password_fingerprint(self) -> str:
        return hashlib.sha256((self.password_hash or "").encode()).hexdigest()[:12]

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def location_label(self) -> str:
        parts = [p for p in (self.plz, self.city) if p]
        return " ".join(parts)

    def __repr__(self):
        return f"<User {self.email}>"


class BookImage(db.Model):
    """Buchfoto direkt in der Datenbank (komprimiert).

    Render-Webservices haben ein fluechtiges Dateisystem - Dateien in
    static/uploads gehen bei jedem Deploy verloren. In der Datenbank bleiben
    die Fotos dauerhaft erhalten.
    """

    __tablename__ = "book_image"

    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.LargeBinary, nullable=False)
    mimetype = db.Column(db.String(50), nullable=False, default="image/jpeg")
    # Zugehoeriges Buch und Reihenfolge (0 = Titelbild). Absichtlich kein Fremdschluessel:
    # Book.image_id verweist schon auf diese Tabelle, ein zweiter Verweis waere ein Zyklus.
    book_id = db.Column(db.Integer, index=True)
    position = db.Column(db.Integer, nullable=False, server_default=sa.text("0"), default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Book(db.Model):
    __tablename__ = "book"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    author = db.Column(db.String(255), nullable=False)
    condition = db.Column(db.String(50), nullable=False)  # Neuwertig/Sehr gut/Gut/Ordentlich
    description = db.Column(db.Text)
    image_filename = db.Column(db.String(255))  # Altbestand (Datei auf Platte)
    image_id = db.Column(db.Integer, db.ForeignKey("book_image.id"))
    is_active = db.Column(db.Boolean, nullable=False, server_default=sa.true(), default=True)
    swapped_at = db.Column(db.DateTime)  # gesetzt, wenn das Buch getauscht wurde
    # "geloescht": nicht mehr sichtbar, bleibt aber erhalten, wenn es Teil eines Matches ist
    deleted_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    image = db.relationship("BookImage", foreign_keys=[image_id])

    @property
    def has_image(self) -> bool:
        return bool(self.image_id or self.image_filename)

    def __repr__(self):
        return f"<Book {self.title!r} von User {self.owner_id}>"


class Swipe(db.Model):
    """Entscheidung eines Users zu einem fremden Buch (liken oder ueberspringen)."""

    __tablename__ = "swipe"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False)
    liked = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", foreign_keys=[user_id])
    book = db.relationship("Book", foreign_keys=[book_id])

    __table_args__ = (
        db.UniqueConstraint("user_id", "book_id", name="uq_swipe_user_book"),
    )


class Match(db.Model):
    """Gegenseitiges Match zwischen zwei Usern inkl. der beiden beteiligten Buecher."""

    __tablename__ = "match"

    id = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    # book1 gehoert user1 und wurde von user2 geliked, und umgekehrt
    book1_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False)
    book2_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # "Neues Match"-Hinweis: pro Seite, bis der Chat geoeffnet wurde
    user1_seen = db.Column(db.Boolean, nullable=False, server_default=sa.false(), default=False)
    user2_seen = db.Column(db.Boolean, nullable=False, server_default=sa.false(), default=False)

    # active | completed (Tausch abgeschlossen)
    status = db.Column(db.String(20), nullable=False, server_default="active", default="active")
    completed_at = db.Column(db.DateTime)

    # Zeitpunkt der letzten Mail-Benachrichtigung (Drosselung bei Nachrichten)
    user1_notified_at = db.Column(db.DateTime)
    user2_notified_at = db.Column(db.DateTime)

    user1 = db.relationship("User", foreign_keys=[user1_id])
    user2 = db.relationship("User", foreign_keys=[user2_id])
    book1 = db.relationship("Book", foreign_keys=[book1_id])
    book2 = db.relationship("Book", foreign_keys=[book2_id])
    messages = db.relationship(
        "Message", backref="match", lazy=True,
        cascade="all, delete-orphan", order_by="Message.created_at",
    )

    def other_user(self, current_user_id):
        return self.user2 if self.user1_id == current_user_id else self.user1

    def my_book(self, current_user_id):
        """Das eigene Buch, das in diesem Match getauscht wird."""
        return self.book1 if self.user1_id == current_user_id else self.book2

    def their_book(self, current_user_id):
        """Das Buch der Gegenseite, das in diesem Match getauscht wird."""
        return self.book2 if self.user1_id == current_user_id else self.book1

    def seen_by(self, uid) -> bool:
        return self.user1_seen if self.user1_id == uid else self.user2_seen

    def mark_seen(self, uid) -> None:
        if self.user1_id == uid:
            self.user1_seen = True
        else:
            self.user2_seen = True

    def notified_at(self, uid):
        return self.user1_notified_at if self.user1_id == uid else self.user2_notified_at

    def set_notified(self, uid, when) -> None:
        if self.user1_id == uid:
            self.user1_notified_at = when
        else:
            self.user2_notified_at = when


class Message(db.Model):
    __tablename__ = "message"

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("match.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    read_at = db.Column(db.DateTime)  # NULL = vom Empfaenger noch nicht gelesen

    sender = db.relationship("User", foreign_keys=[sender_id])


class Block(db.Model):
    """blocker_id will blocked_id nicht mehr sehen (und umgekehrt)."""

    __tablename__ = "block"

    id = db.Column(db.Integer, primary_key=True)
    blocker_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    blocked_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("blocker_id", "blocked_id", name="uq_block_pair"),
    )


class Report(db.Model):
    """Meldung eines Users (z. B. aus dem Chat) fuer die Moderation."""

    __tablename__ = "report"

    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reported_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reason = db.Column(db.String(50), nullable=False)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    handled = db.Column(db.Boolean, nullable=False, server_default=sa.false(), default=False)

    reporter = db.relationship("User", foreign_keys=[reporter_id])
    reported = db.relationship("User", foreign_keys=[reported_id])


class GeoCache(db.Model):
    """Zwischenspeicher fuer Geocoding-Ergebnisse (PLZ/Ort -> Koordinaten)."""

    __tablename__ = "geo_cache"

    id = db.Column(db.Integer, primary_key=True)
    query_key = db.Column(db.String(160), unique=True, nullable=False, index=True)
    label = db.Column(db.String(160))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

