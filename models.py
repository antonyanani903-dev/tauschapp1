"""Datenmodelle fuer die Buchtausch-App.

Kernidee des Matchings:
- Ein User "swiped" auf Buecher anderer User (liked=True oder liked=False).
- Ein Match entsteht erst, wenn BEIDE Seiten sich gegenseitig ein Buch
  des jeweils anderen geliked haben (echte Tausch-Reziprozitaet, nicht
  nur "ich mag dein Buch").
"""
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    city = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    books = db.relationship(
        "Book", backref="owner", lazy=True, cascade="all, delete-orphan"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.email}>"


class Book(db.Model):
    __tablename__ = "book"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    author = db.Column(db.String(255), nullable=False)
    condition = db.Column(db.String(50), nullable=False)  # Neuwertig/Sehr gut/Gut/Ordentlich
    description = db.Column(db.Text)
    image_filename = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

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


class Message(db.Model):
    __tablename__ = "message"

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("match.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    sender = db.relationship("User", foreign_keys=[sender_id])
