"""Seitentausch - Buchtausch-App.

Flask-Hauptanwendung. Struktur bewusst angelehnt an das bestehende
Buchhandlung-Projekt (app.py / models.py / extensions.py), damit Wartung
und Deployment-Workflow vertraut bleiben.
"""
import os
import uuid
from datetime import datetime

from flask import (
    Flask, abort, flash, redirect, render_template, request,
    url_for, jsonify,
)
from flask_login import (
    current_user, login_required, login_user, logout_user,
)
from werkzeug.security import generate_password_hash  # noqa: F401 (re-export Komfort)
from werkzeug.utils import secure_filename

from extensions import db, login_manager
from models import Book, Match, Message, Swipe, User

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
CONDITIONS = ["Neuwertig", "Sehr gut", "Gut", "Ordentlich"]


def create_app():
    app = Flask(__name__)

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-insecure-key")

    database_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
    # Render liefert "postgres://", SQLAlchemy 2.x will "postgresql://"
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    app.config["UPLOAD_FOLDER"] = os.path.join(app.static_folder, "uploads")
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB pro Upload

    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()

    register_routes(app)
    return app


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def register_routes(app: Flask):

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # ---------- Oeffentliche Seiten ----------

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("discover"))
        return render_template("index.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("discover"))

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            city = request.form.get("city", "").strip()

            if not name or not email or not password:
                flash("Bitte Name, E-Mail und Passwort ausfüllen.", "error")
                return render_template("register.html")

            if len(password) < 8:
                flash("Das Passwort muss mindestens 8 Zeichen lang sein.", "error")
                return render_template("register.html")

            if User.query.filter_by(email=email).first():
                flash("Für diese E-Mail existiert bereits ein Konto.", "error")
                return render_template("register.html")

            user = User(name=name, email=email, city=city or None)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            login_user(user)
            flash("Willkommen bei Seitentausch!", "success")
            return redirect(url_for("discover"))

        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("discover"))

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()

            if user is None or not user.check_password(password):
                flash("E-Mail oder Passwort ist falsch.", "error")
                return render_template("login.html")

            login_user(user)
            next_url = request.args.get("next")
            return redirect(next_url or url_for("discover"))

        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("index"))

    # ---------- Entdecken / Swipe ----------

    @app.route("/discover")
    @login_required
    def discover():
        already_swiped = db.session.query(Swipe.book_id).filter(
            Swipe.user_id == current_user.id
        )
        next_book = (
            Book.query.filter(
                Book.owner_id != current_user.id,
                Book.is_active.is_(True),
                ~Book.id.in_(already_swiped),
            )
            .order_by(Book.created_at.desc())
            .first()
        )
        remaining = Book.query.filter(
            Book.owner_id != current_user.id,
            Book.is_active.is_(True),
            ~Book.id.in_(already_swiped),
        ).count()
        return render_template("discover.html", book=next_book, remaining=remaining)

    @app.route("/swipe/<int:book_id>/<decision>", methods=["POST"])
    @login_required
    def swipe(book_id, decision):
        if decision not in ("like", "skip"):
            abort(400)

        book = db.session.get(Book, book_id)
        if book is None or book.owner_id == current_user.id:
            abort(404)

        existing = Swipe.query.filter_by(
            user_id=current_user.id, book_id=book_id
        ).first()
        if existing is None:
            existing = Swipe(user_id=current_user.id, book_id=book_id)
            db.session.add(existing)
        existing.liked = decision == "like"
        existing.created_at = datetime.utcnow()
        db.session.commit()

        matched = False
        if existing.liked:
            matched = try_create_match(current_user.id, book)

        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify({"matched": matched})

        if matched:
            flash("Es ist ein Match! Ihr könnt jetzt chatten.", "success")
            return redirect(url_for("matches"))
        return redirect(url_for("discover"))

    def try_create_match(liker_id: int, liked_book: Book) -> bool:
        """Prueft Reziprozitaet und legt bei Gegenseitigkeit ein Match an."""
        owner_id = liked_book.owner_id

        owner_likes_my_book = (
            db.session.query(Swipe)
            .join(Book, Swipe.book_id == Book.id)
            .filter(
                Swipe.user_id == owner_id,
                Swipe.liked.is_(True),
                Book.owner_id == liker_id,
            )
            .first()
        )
        if owner_likes_my_book is None:
            return False

        u1, u2 = sorted([liker_id, owner_id])
        already = Match.query.filter(
            Match.user1_id == u1, Match.user2_id == u2
        ).first()
        if already is not None:
            return False

        my_book_owner_liked = owner_likes_my_book.book

        if u1 == liker_id:
            book1_id, book2_id = my_book_owner_liked.id, liked_book.id
        else:
            book1_id, book2_id = liked_book.id, my_book_owner_liked.id

        match = Match(
            user1_id=u1, user2_id=u2, book1_id=book1_id, book2_id=book2_id
        )
        db.session.add(match)
        db.session.commit()
        return True

    # ---------- Meine Buecher ----------

    @app.route("/books")
    @login_required
    def my_books():
        books = (
            Book.query.filter_by(owner_id=current_user.id)
            .order_by(Book.created_at.desc())
            .all()
        )
        return render_template("my_books.html", books=books, conditions=CONDITIONS)

    @app.route("/books/add", methods=["POST"])
    @login_required
    def add_book():
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()
        condition = request.form.get("condition", CONDITIONS[2])
        description = request.form.get("description", "").strip()

        if not title or not author:
            flash("Titel und Autor sind Pflichtfelder.", "error")
            return redirect(url_for("my_books"))

        image_filename = None
        file = request.files.get("image")
        if file and file.filename and allowed_file(file.filename):
            ext = file.filename.rsplit(".", 1)[1].lower()
            image_filename = f"{uuid.uuid4().hex}.{ext}"
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], image_filename))

        book = Book(
            owner_id=current_user.id,
            title=title,
            author=author,
            condition=condition,
            description=description or None,
            image_filename=image_filename,
        )
        db.session.add(book)
        db.session.commit()
        flash(f"„{title}“ wurde hochgeladen.", "success")
        return redirect(url_for("my_books"))

    @app.route("/books/<int:book_id>/delete", methods=["POST"])
    @login_required
    def delete_book(book_id):
        book = db.session.get(Book, book_id)
        if book is None or book.owner_id != current_user.id:
            abort(404)
        db.session.delete(book)
        db.session.commit()
        flash("Buch entfernt.", "success")
        return redirect(url_for("my_books"))

    # ---------- Matches & Chat ----------

    @app.route("/matches")
    @login_required
    def matches():
        user_matches = (
            Match.query.filter(
                (Match.user1_id == current_user.id)
                | (Match.user2_id == current_user.id)
            )
            .order_by(Match.created_at.desc())
            .all()
        )
        return render_template("matches.html", matches=user_matches)

    @app.route("/chat/<int:match_id>", methods=["GET", "POST"])
    @login_required
    def chat(match_id):
        match = db.session.get(Match, match_id)
        if match is None or current_user.id not in (match.user1_id, match.user2_id):
            abort(404)

        if request.method == "POST":
            content = request.form.get("content", "").strip()
            if content:
                msg = Message(
                    match_id=match.id, sender_id=current_user.id, content=content
                )
                db.session.add(msg)
                db.session.commit()
            if request.headers.get("X-Requested-With") == "fetch":
                return jsonify({"ok": True})
            return redirect(url_for("chat", match_id=match.id))

        return render_template("chat.html", match=match)

    @app.route("/chat/<int:match_id>/messages")
    @login_required
    def chat_messages(match_id):
        match = db.session.get(Match, match_id)
        if match is None or current_user.id not in (match.user1_id, match.user2_id):
            abort(404)
        return jsonify(
            [
                {
                    "sender_id": m.sender_id,
                    "content": m.content,
                    "created_at": m.created_at.strftime("%H:%M"),
                }
                for m in match.messages
            ]
        )

    # ---------- Profil ----------

    @app.route("/profile")
    @login_required
    def profile():
        return render_template("profile.html")

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
