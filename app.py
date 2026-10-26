"""Seitentausch - Buchtausch-App.

Flask-Hauptanwendung. Struktur bewusst angelehnt an das bestehende
Buchhandlung-Projekt (app.py / models.py / extensions.py), damit Wartung
und Deployment-Workflow vertraut bleiben.

Module:
  geo.py            PLZ/Ort -> Koordinaten, Umkreis
  notifications.py  Zaehler, Live-Ereignisse, E-Mail-Benachrichtigungen
  mailer.py         SMTP-Versand
  images.py         Foto-Upload (verkleinern, in der Datenbank speichern)
  migrate.py        ergaenzt neue Spalten in bestehenden Tabellen
"""
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (
    Flask, Response, abort, flash, jsonify, redirect, render_template,
    request, url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

import geo
import mailer
import notifications
from extensions import csrf, db, login_manager
from images import ImageError, process_upload
from migrate import ensure_columns
from models import Block, Book, BookImage, Match, Message, Report, Swipe, User

log = logging.getLogger(__name__)

CONDITIONS = ["Neuwertig", "Sehr gut", "Gut", "Ordentlich"]
REPORT_REASONS = {
    "spam": "Spam oder Werbung",
    "belaestigung": "Belästigung oder unangemessenes Verhalten",
    "betrug": "Verdacht auf Betrug",
    "nicht_erschienen": "Nicht zum Treffen erschienen",
    "sonstiges": "Sonstiges",
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD = 8
RESET_MAX_AGE = 3600  # Passwort-Reset-Links gelten 1 Stunde

try:
    LOCAL_TZ = ZoneInfo("Europe/Berlin")
except ZoneInfoNotFoundError:  # tzdata fehlt -> UTC statt Absturz
    LOCAL_TZ = timezone.utc


# ---------------------------------------------------------------- Hilfsfunktionen

_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    """True, wenn das Limit fuer diesen Schluessel schon erreicht ist (zaehlt sonst mit)."""
    now = time.monotonic()
    with _hits_lock:
        dq = _hits[key]
        while dq and now - dq[0] > window_seconds:
            dq.popleft()
        if len(dq) >= limit:
            return True
        dq.append(now)
        if len(_hits) > 5000:  # Speicher begrenzen
            for k in [k for k, v in _hits.items() if not v]:
                del _hits[k]
        return False


def client_ip() -> str:
    return request.remote_addr or "unknown"


def safe_next(url):
    """Nur interne Pfade zulassen (Schutz vor Open-Redirect nach dem Login)."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return None


def wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "fetch"


def base_url() -> str:
    return os.environ.get("APP_BASE_URL") or request.url_root


def to_local(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)


def day_label(dt) -> str:
    local = to_local(dt)
    today = to_local(datetime.utcnow()).date()
    if local.date() == today:
        return "Heute"
    if local.date() == today - timedelta(days=1):
        return "Gestern"
    return local.strftime("%d.%m.%Y")


def short_when(dt) -> str:
    if dt is None:
        return ""
    local = to_local(dt)
    if local.date() == to_local(datetime.utcnow()).date():
        return local.strftime("%H:%M")
    return local.strftime("%d.%m.")


def book_img_url(book):
    if book is None:
        return None
    if book.image_id:
        return url_for("book_image", book_id=book.id, v=book.image_id)
    if book.image_filename:
        return url_for("static", filename="uploads/" + book.image_filename)
    return None


def serialize_book(book):
    return {
        "id": book.id, "title": book.title, "author": book.author,
        "condition": book.condition, "description": book.description or "",
        "img": book_img_url(book),
        "distance": getattr(book, "distance_text", ""),
        "owner": book.owner.name.split(" ")[0],
        "place": book.owner.city or "",
    }


def distance_between(a, b):
    if a is None or b is None or not (a.has_location and b.has_location):
        return None
    return geo.haversine_km(a.lat, a.lon, b.lat, b.lon)


def serialize_message(msg, uid):
    return {
        "id": msg.id,
        "mine": msg.sender_id == uid,
        "content": msg.content,
        "time": to_local(msg.created_at).strftime("%H:%M"),
        "day": day_label(msg.created_at),
        "read": msg.read_at is not None,
    }


def apply_location(user, text: str, country: str):
    """Setzt/loescht den Standort. Gibt (ok, fehlertext) zurueck."""
    country = country if country in geo.COUNTRIES else "DE"
    text = (text or "").strip()
    if not text:
        user.plz = user.city = user.lat = user.lon = None
        user.country = country
        return True, None
    result = geo.geocode(text, country)
    if not result:
        return False, (f"„{text}“ wurde nicht gefunden. Bitte gib eine gültige Postleitzahl "
                       f"oder einen Ortsnamen in {geo.COUNTRIES[country]} ein.")
    parsed = geo.parse_query(text, country)
    user.country = country
    user.lat, user.lon = result["lat"], result["lon"]
    user.plz = result.get("plz") or (parsed[1] if parsed and parsed[0] == "plz" else None)
    user.city = result.get("city") or (parsed[1] if parsed and parsed[0] == "city" else None)
    return True, None


def parse_radius(value, default):
    if value == "all":
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if v in geo.RADIUS_CHOICES else default


def reset_serializer():
    return URLSafeTimedSerializer(
        __import__("flask").current_app.config["SECRET_KEY"], salt="password-reset")


def make_reset_token(user) -> str:
    return reset_serializer().dumps({"u": user.id, "h": user.password_fingerprint()})


def load_reset_user(token):
    try:
        data = reset_serializer().loads(token, max_age=RESET_MAX_AGE)
    except (SignatureExpired, BadSignature):
        return None
    user = db.session.get(User, data.get("u"))
    if user is None or user.password_fingerprint() != data.get("h") or user.is_banned:
        return None  # Passwort wurde inzwischen geaendert -> Link ist verbraucht
    return user


# ---------------------------------------------------------------- Feed / Matching

def build_feed(user):
    """Alle fuer user sichtbaren, noch nicht bewerteten Buecher - nach Entfernung sortiert."""
    swiped = db.session.query(Swipe.book_id).filter(Swipe.user_id == user.id)
    q = (
        Book.query.join(User, Book.owner_id == User.id)
        .options(joinedload(Book.owner))
        .filter(
            Book.owner_id != user.id,
            Book.is_active.is_(True),
            Book.deleted_at.is_(None),
            ~Book.id.in_(swiped),
        )
    )
    hidden = notifications.blocked_ids(user.id)
    if hidden:
        q = q.filter(~Book.owner_id.in_(hidden))

    radius = user.radius_km if user.has_location else None
    query = q
    if radius:
        lat_min, lat_max, lon_min, lon_max = geo.bounding_box(user.lat, user.lon, radius)
        query = q.filter(User.lat.between(lat_min, lat_max), User.lon.between(lon_min, lon_max))

    books = query.order_by(Book.created_at.desc()).limit(500).all()

    feed = []
    for book in books:
        km = distance_between(user, book.owner)
        if radius and (km is None or km > radius):
            continue
        book.distance_km = km
        book.distance_text = geo.format_distance(km) if km is not None else ""
        feed.append(book)
    feed.sort(key=lambda b: (b.distance_km is None, b.distance_km or 0))  # stabil: neueste zuerst

    outside = 0
    if not feed and radius:
        outside = q.count()  # Buecher, die nur wegen des Radius ausgeblendet sind
    return feed, outside


def try_create_match(me, liked_book):
    """Legt ein Match an, wenn der Besitzer des gelikten Buchs ein Buch von mir geliked hat."""
    owner_id = liked_book.owner_id
    hit = (
        db.session.query(Swipe)
        .join(Book, Swipe.book_id == Book.id)
        .filter(
            Swipe.user_id == owner_id,
            Swipe.liked.is_(True),
            Book.owner_id == me.id,
            Book.is_active.is_(True),      # getauschte/pausierte Buecher zaehlen nicht
            Book.deleted_at.is_(None),
        )
        .order_by(Swipe.created_at.desc())
        .first()
    )
    if hit is None:
        return None

    u1, u2 = sorted([me.id, owner_id])
    if Match.query.filter_by(user1_id=u1, user2_id=u2, status="active").first():
        return None

    my_book = hit.book
    book1_id, book2_id = (my_book.id, liked_book.id) if u1 == me.id else (liked_book.id, my_book.id)
    match = Match(
        user1_id=u1, user2_id=u2, book1_id=book1_id, book2_id=book2_id,
        user1_seen=(u1 == me.id), user2_seen=(u2 == me.id),  # ich sehe es sofort, die andere Seite nicht
    )
    db.session.add(match)
    db.session.commit()

    try:
        notifications.email_new_match(
            match, db.session.get(User, owner_id), base_url(), os.environ.get("SITE_NAME", "Seitentausch"))
    except Exception:
        log.exception("Match-Mail fehlgeschlagen")
    return match


def purge_user(uid: int):
    """Loescht ein Konto und ALLE zugehoerigen Daten (DSGVO: Recht auf Loeschung)."""
    for m in Match.query.filter(or_(Match.user1_id == uid, Match.user2_id == uid)).all():
        db.session.delete(m)  # Nachrichten werden per Cascade mitgeloescht
    db.session.flush()

    books = Book.query.filter_by(owner_id=uid).all()
    book_ids = [b.id for b in books]
    image_ids = [b.image_id for b in books if b.image_id]
    legacy_files = [b.image_filename for b in books if b.image_filename]

    if book_ids:
        Swipe.query.filter(Swipe.book_id.in_(book_ids)).delete(synchronize_session=False)
    Swipe.query.filter_by(user_id=uid).delete(synchronize_session=False)
    Block.query.filter(or_(Block.blocker_id == uid, Block.blocked_id == uid)).delete(synchronize_session=False)
    Report.query.filter(or_(Report.reporter_id == uid, Report.reported_id == uid)).delete(synchronize_session=False)
    Book.query.filter_by(owner_id=uid).delete(synchronize_session=False)
    if image_ids:
        BookImage.query.filter(BookImage.id.in_(image_ids)).delete(synchronize_session=False)
    User.query.filter_by(id=uid).delete(synchronize_session=False)
    db.session.commit()

    upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
    for name in legacy_files:
        try:
            os.remove(os.path.join(upload_dir, os.path.basename(name)))
        except OSError:
            pass


# ---------------------------------------------------------------- App-Fabrik

def create_app(test_config=None):
    app = Flask(__name__)

    on_render = bool(os.environ.get("RENDER"))
    secret = os.environ.get("SECRET_KEY")
    if on_render and not secret:
        raise RuntimeError("SECRET_KEY fehlt - bitte in Render unter Environment setzen.")
    app.config["SECRET_KEY"] = secret or "dev-only-insecure-key"

    database_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
    # Render liefert "postgres://" oder "postgresql://" ohne Treiberangabe.
    # Wir erzwingen explizit psycopg2 (das installierte Paket), damit
    # SQLAlchemy nicht versucht, das nicht-installierte "psycopg" (v3) zu laden.
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif database_url.startswith("postgresql://") and "+psycopg" not in database_url:
        database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}  # tote DB-Verbindungen erkennen

    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # Handyfotos bis 8 MB
    app.config["WTF_CSRF_ENABLED"] = os.environ.get("WTF_CSRF_ENABLED", "1") != "0"
    app.config["WTF_CSRF_TIME_LIMIT"] = None  # Token gilt so lange wie die Sitzung
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = on_render
    app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_SECURE"] = on_render
    if test_config:
        app.config.update(test_config)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()          # neue Tabellen
        ensure_columns(db)       # neue Spalten in bestehenden Tabellen

    register_hooks(app)
    register_routes(app)
    return app


def register_hooks(app: Flask):

    @login_manager.user_loader
    def load_user(session_id):
        try:
            uid, fingerprint = str(session_id).split(":", 1)
            user = db.session.get(User, int(uid))
        except (ValueError, TypeError):
            return None
        if user is None or user.password_fingerprint() != fingerprint or user.is_banned:
            return None  # Passwort geaendert oder Konto gesperrt -> abmelden
        return user

    @app.before_request
    def touch_last_seen():
        if current_user.is_authenticated and request.endpoint != "static":
            now = datetime.utcnow()
            if current_user.last_seen is None or now - current_user.last_seen > timedelta(seconds=60):
                current_user.last_seen = now
                db.session.commit()

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.context_processor
    def inject_globals():
        ctx = {
            "SITE_NAME": os.environ.get("SITE_NAME", "Seitentausch"),
            "IMPRESSUM_URL": os.environ.get("IMPRESSUM_URL", ""),
            "PRIVACY_URL": os.environ.get("PRIVACY_URL", ""),
            "COUNTRIES": geo.COUNTRIES,
            "RADIUS_CHOICES": geo.RADIUS_CHOICES,
            "CONDITIONS": CONDITIONS,
            "book_img": book_img_url,
        }
        if current_user.is_authenticated:
            ctx["nav"] = {
                "counts": notifications.counts(current_user.id),
                "since": notifications.watermark(current_user.id),
            }
        return ctx

    app.add_template_filter(lambda dt: to_local(dt).strftime("%H:%M") if dt else "", "localtime")
    app.add_template_filter(short_when, "short_when")
    app.add_template_filter(day_label, "day_label")

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        if wants_json():
            return jsonify(error="Sitzung abgelaufen - bitte Seite neu laden."), 400
        flash("Die Sitzung ist abgelaufen. Bitte versuche es noch einmal.", "error")
        return redirect(request.referrer or url_for("index"))

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(e):
        flash("Die Datei ist zu groß (maximal 8 MB).", "error")
        return redirect(request.referrer or url_for("my_books"))

    @app.errorhandler(429)
    def too_many(e):
        if wants_json():
            return jsonify(error="Zu viele Anfragen - bitte kurz warten."), 429
        return render_template("404.html", message="Zu viele Anfragen. Bitte warte einen Moment."), 429


def register_routes(app: Flask):

    # ------------------------------------------------------------ Allgemein

    @app.route("/healthz")
    def healthz():
        return "ok", 200, {"Cache-Control": "no-store"}

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("discover"))
        return render_template("index.html")

    # ------------------------------------------------------------ Konto: Registrierung / Login

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("discover"))

        if request.method == "POST":
            if rate_limited(f"register:{client_ip()}", 10, 3600):
                flash("Zu viele Registrierungen von dieser Adresse. Bitte versuche es später erneut.", "error")
                return render_template("register.html"), 429

            name = request.form.get("name", "").strip()[:100]
            email = request.form.get("email", "").strip().lower()[:255]
            password = request.form.get("password", "")
            country = request.form.get("country", "DE")
            location = request.form.get("location", "").strip()
            form_data = {"name": name, "email": email, "location": location, "country": country}

            error = None
            if not name or not email or not password:
                error = "Bitte Name, E-Mail und Passwort ausfüllen."
            elif not EMAIL_RE.match(email):
                error = "Bitte gib eine gültige E-Mail-Adresse ein."
            elif len(password) < MIN_PASSWORD:
                error = f"Das Passwort muss mindestens {MIN_PASSWORD} Zeichen lang sein."
            elif request.form.get("accept_privacy") != "yes":
                error = "Bitte bestätige die Datenschutzerklärung."
            elif User.query.filter_by(email=email).first():
                error = "Für diese E-Mail existiert bereits ein Konto."
            if error:
                flash(error, "error")
                return render_template("register.html", form=form_data), 400

            user = User(name=name, email=email, terms_accepted_at=datetime.utcnow())
            user.set_password(password)
            ok, loc_error = apply_location(user, location, country) if location else (True, None)
            if not ok:
                apply_location(user, "", country)
            db.session.add(user)
            db.session.commit()

            login_user(user, remember=True)
            flash("Willkommen bei Seitentausch!", "success")
            if not ok:
                flash(loc_error + " Du kannst den Standort jederzeit im Profil nachtragen.", "info")
            return redirect(url_for("my_books" if ok else "discover"))

        return render_template("register.html", form={"country": "DE"})

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("discover"))

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if rate_limited(f"login:{client_ip()}:{email}", 10, 900):
                flash("Zu viele Anmeldeversuche. Bitte warte ein paar Minuten.", "error")
                return render_template("login.html"), 429

            user = User.query.filter_by(email=email).first()
            if user is None or not user.check_password(password):
                flash("E-Mail oder Passwort ist falsch.", "error")
                return render_template("login.html", email=email), 401
            if user.is_banned:
                flash("Dieses Konto wurde gesperrt.", "error")
                return render_template("login.html", email=email), 403

            login_user(user, remember=request.form.get("remember") == "yes")
            return redirect(safe_next(request.args.get("next")) or url_for("discover"))

        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("index"))

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            if rate_limited(f"forgot:{client_ip()}", 5, 3600):
                flash("Zu viele Anfragen. Bitte versuche es in einer Stunde erneut.", "error")
                return render_template("forgot_password.html"), 429
            email = request.form.get("email", "").strip().lower()
            user = User.query.filter_by(email=email).first() if email else None
            if user and not user.is_banned:
                link = base_url().rstrip("/") + url_for("reset_password", token=make_reset_token(user))
                site = os.environ.get("SITE_NAME", "Seitentausch")
                text = (
                    f"Hallo {user.name},\n\n"
                    f"für dein {site}-Konto wurde ein neues Passwort angefordert.\n"
                    f"Mit diesem Link kannst du es setzen (gültig für 1 Stunde, nur einmal nutzbar):\n\n"
                    f"{link}\n\n"
                    f"Wenn du das nicht warst, ignoriere diese E-Mail einfach - dein Passwort bleibt unverändert.\n"
                )
                html = mailer.button_email(
                    site, f"Hallo {user.name},",
                    ["für dein Konto wurde ein neues Passwort angefordert. "
                     "Klicke auf den Button, um ein neues Passwort zu setzen."],
                    "Neues Passwort festlegen", link,
                    ["Der Link ist 1 Stunde gültig und kann nur einmal benutzt werden.",
                     "Wenn du das nicht warst, ignoriere diese E-Mail einfach. Dein Passwort bleibt unverändert."])
                mailer.send_mail(user.email, f"Passwort zurücksetzen – {site}", text, html)
            if mailer.is_configured():
                flash("Falls ein Konto mit dieser E-Mail existiert, haben wir dir einen Link zum "
                      "Zurücksetzen geschickt. Bitte prüfe auch den Spam-Ordner.", "info")
            else:
                flash("Der E-Mail-Versand ist noch nicht eingerichtet. Bitte wende dich an den Betreiber.", "error")
            return redirect(url_for("login"))
        return render_template("forgot_password.html")

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        user = load_reset_user(token)
        if user is None:
            flash("Dieser Link ist ungültig oder abgelaufen. Bitte fordere einen neuen an.", "error")
            return redirect(url_for("forgot_password"))
        if request.method == "POST":
            password = request.form.get("password", "")
            if len(password) < MIN_PASSWORD:
                flash(f"Das Passwort muss mindestens {MIN_PASSWORD} Zeichen lang sein.", "error")
                return render_template("reset_password.html", token=token), 400
            if password != request.form.get("password2", ""):
                flash("Die beiden Passwörter stimmen nicht überein.", "error")
                return render_template("reset_password.html", token=token), 400
            user.set_password(password)  # entwertet auch den Link und alle alten Sitzungen
            db.session.commit()
            flash("Dein Passwort wurde geändert. Du kannst dich jetzt anmelden.", "success")
            return redirect(url_for("login"))
        return render_template("reset_password.html", token=token)

    # ------------------------------------------------------------ Entdecken / Swipe

    @app.route("/discover")
    @login_required
    def discover():
        feed, outside = build_feed(current_user)
        my_active = Book.query.filter_by(owner_id=current_user.id, is_active=True, deleted_at=None).count()
        next_radius = next((r for r in geo.RADIUS_CHOICES if current_user.radius_km and r > current_user.radius_km), None)
        batch = feed[:30]
        return render_template(
            "discover.html", books=[serialize_book(b) for b in batch], more=len(feed) > len(batch),
            outside=outside, my_active=my_active, next_radius=next_radius,
        )

    @app.route("/location", methods=["POST"])
    @login_required
    def save_location():
        country = request.form.get("country", current_user.country)
        ok, error = apply_location(current_user, request.form.get("location", ""), country)
        if not ok:
            db.session.rollback()
            flash(error, "error")
        else:
            if "radius" in request.form:
                current_user.radius_km = parse_radius(request.form.get("radius"), current_user.radius_km)
            db.session.commit()
            flash("Standort gespeichert." if current_user.has_location else "Standort entfernt.", "success")
        return redirect(safe_next(request.form.get("next")) or url_for("discover"))

    @app.route("/swipe/<int:book_id>/<decision>", methods=["POST"])
    @login_required
    def swipe(book_id, decision):
        if decision not in ("like", "skip"):
            abort(400)
        book = db.session.get(Book, book_id)
        if (book is None or book.owner_id == current_user.id or book.deleted_at or not book.is_active
                or book.owner.is_banned or book.owner_id in notifications.blocked_ids(current_user.id)):
            abort(404)
        if rate_limited(f"swipe:{current_user.id}", 120, 60):
            abort(429)

        existing = Swipe.query.filter_by(user_id=current_user.id, book_id=book_id).first()
        if existing is None:
            existing = Swipe(user_id=current_user.id, book_id=book_id)
            db.session.add(existing)
        existing.liked = decision == "like"
        existing.created_at = datetime.utcnow()
        db.session.commit()

        match = try_create_match(current_user, book) if existing.liked else None

        if wants_json():
            payload = {"matched": match is not None}
            if match:
                mine, theirs = match.my_book(current_user.id), match.their_book(current_user.id)
                payload.update({
                    "match_id": match.id,
                    "other_name": match.other_user(current_user.id).name,
                    "my_book": {"title": mine.title, "img": book_img_url(mine)},
                    "their_book": {"title": theirs.title, "img": book_img_url(theirs)},
                    "chat_url": url_for("chat", match_id=match.id),
                })
            return jsonify(payload)

        if match:
            flash("Es ist ein Match! Ihr könnt jetzt chatten.", "success")
            return redirect(url_for("chat", match_id=match.id))
        return redirect(url_for("discover"))

    # ------------------------------------------------------------ Meine Buecher

    @app.route("/books")
    @login_required
    def my_books():
        books = (Book.query.filter_by(owner_id=current_user.id, deleted_at=None)
                 .order_by(Book.created_at.desc()).all())
        likes = dict(
            db.session.query(Swipe.book_id, func.count(Swipe.id))
            .filter(Swipe.book_id.in_([b.id for b in books] or [0]), Swipe.liked.is_(True))
            .group_by(Swipe.book_id).all()
        )
        return render_template("my_books.html", books=books, likes=likes)

    def read_book_form():
        title = request.form.get("title", "").strip()[:255]
        author = request.form.get("author", "").strip()[:255]
        condition = request.form.get("condition", "Gut")
        description = request.form.get("description", "").strip()[:1000]
        if not title or not author:
            return None, "Titel und Autor sind Pflichtfelder."
        if condition not in CONDITIONS:
            condition = "Gut"
        return {"title": title, "author": author, "condition": condition,
                "description": description or None}, None

    @app.route("/books/add", methods=["POST"])
    @login_required
    def add_book():
        data, error = read_book_form()
        if error:
            flash(error, "error")
            return redirect(url_for("my_books"))

        image = None
        file = request.files.get("image")
        if file and file.filename:
            try:
                image = BookImage(data=process_upload(file), mimetype="image/jpeg")
            except ImageError as exc:
                flash(str(exc), "error")
                return redirect(url_for("my_books"))

        book = Book(owner_id=current_user.id, **data)
        if image:
            db.session.add(image)
            db.session.flush()
            book.image_id = image.id
        db.session.add(book)
        db.session.commit()
        flash(f"„{book.title}“ wurde hochgeladen.", "success")
        return redirect(url_for("my_books"))

    def own_book_or_404(book_id):
        book = db.session.get(Book, book_id)
        if book is None or book.owner_id != current_user.id or book.deleted_at:
            abort(404)
        return book

    @app.route("/books/<int:book_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_book(book_id):
        book = own_book_or_404(book_id)
        if request.method == "POST":
            data, error = read_book_form()
            if error:
                flash(error, "error")
                return render_template("edit_book.html", book=book), 400
            new_image = None
            file = request.files.get("image")
            if file and file.filename:
                try:
                    new_image = BookImage(data=process_upload(file), mimetype="image/jpeg")
                except ImageError as exc:
                    flash(str(exc), "error")
                    return render_template("edit_book.html", book=book), 400

            old_image_id = book.image_id
            for key, value in data.items():
                setattr(book, key, value)
            if new_image:
                db.session.add(new_image)
                db.session.flush()
                book.image_id, book.image_filename = new_image.id, None
            elif request.form.get("remove_image") == "yes":
                book.image_id = book.image_filename = None
            db.session.flush()
            if old_image_id and old_image_id != book.image_id:
                BookImage.query.filter_by(id=old_image_id).delete(synchronize_session=False)
            db.session.commit()
            flash("Änderungen gespeichert.", "success")
            return redirect(url_for("my_books"))
        return render_template("edit_book.html", book=book)

    @app.route("/books/<int:book_id>/toggle", methods=["POST"])
    @login_required
    def toggle_book(book_id):
        book = own_book_or_404(book_id)
        if book.swapped_at:                       # "Wieder anbieten"
            book.swapped_at, book.is_active = None, True
            flash(f"„{book.title}“ wird wieder angeboten.", "success")
        else:
            book.is_active = not book.is_active
            flash(f"„{book.title}“ " + ("wird wieder angeboten." if book.is_active else "ist pausiert und wird nicht mehr angezeigt."), "success")
        db.session.commit()
        return redirect(url_for("my_books"))

    @app.route("/books/<int:book_id>/delete", methods=["POST"])
    @login_required
    def delete_book(book_id):
        book = own_book_or_404(book_id)
        Swipe.query.filter_by(book_id=book.id).delete(synchronize_session=False)
        in_match = Match.query.filter(or_(Match.book1_id == book.id, Match.book2_id == book.id)).first()
        if in_match:
            # Teil eines Matches: nur ausblenden, damit Chats und Tauschhistorie intakt bleiben
            book.deleted_at, book.is_active = datetime.utcnow(), False
        else:
            image_id = book.image_id
            db.session.delete(book)
            db.session.flush()
            if image_id:
                BookImage.query.filter_by(id=image_id).delete(synchronize_session=False)
        db.session.commit()
        flash("Buch entfernt.", "success")
        return redirect(url_for("my_books"))

    @app.route("/media/book/<int:book_id>")
    @login_required
    def book_image(book_id):
        book = db.session.get(Book, book_id)
        if book is None or not book.image_id:
            abort(404)
        image = db.session.get(BookImage, book.image_id)
        if image is None:
            abort(404)
        return Response(image.data, mimetype=image.mimetype,
                        headers={"Cache-Control": "private, max-age=604800, immutable"})

    # ------------------------------------------------------------ Matches & Chat

    @app.route("/matches")
    @login_required
    def matches():
        uid = current_user.id
        items = (notifications.visible_matches_query(uid)
                 .options(joinedload(Match.book1), joinedload(Match.book2),
                          joinedload(Match.user1), joinedload(Match.user2)).all())
        ids = [m.id for m in items] or [0]

        unread = dict(
            db.session.query(Message.match_id, func.count(Message.id))
            .filter(Message.match_id.in_(ids), Message.sender_id != uid, Message.read_at.is_(None))
            .group_by(Message.match_id).all()
        )
        last_ids = [row[0] for row in db.session.query(func.max(Message.id))
                    .filter(Message.match_id.in_(ids)).group_by(Message.match_id).all()]
        last = {msg.match_id: msg for msg in Message.query.filter(Message.id.in_(last_ids or [0])).all()}

        rows = []
        for m in items:
            other = m.other_user(uid)
            km = distance_between(current_user, other)
            msg = last.get(m.id)
            rows.append({
                "match": m, "other": other, "mine": m.my_book(uid), "theirs": m.their_book(uid),
                "unread": unread.get(m.id, 0), "is_new": not m.seen_by(uid),
                "last": msg, "when": (msg.created_at if msg else m.created_at),
                "distance": geo.format_distance(km) if km is not None else "",
            })
        rows.sort(key=lambda r: r["when"], reverse=True)
        return render_template(
            "matches.html",
            active=[r for r in rows if r["match"].status == "active"],
            completed=[r for r in rows if r["match"].status != "active"],
        )

    def get_match_or_404(match_id):
        m = db.session.get(Match, match_id)
        if m is None or current_user.id not in (m.user1_id, m.user2_id):
            abort(404)
        if m.other_user(current_user.id).id in notifications.blocked_ids(current_user.id):
            abort(404)
        return m

    def mark_read(m):
        now = datetime.utcnow()
        Message.query.filter(
            Message.match_id == m.id, Message.sender_id != current_user.id, Message.read_at.is_(None)
        ).update({"read_at": now}, synchronize_session=False)
        m.mark_seen(current_user.id)
        db.session.commit()

    @app.route("/chat/<int:match_id>", methods=["GET", "POST"])
    @login_required
    def chat(match_id):
        m = get_match_or_404(match_id)
        uid = current_user.id

        if request.method == "POST":
            content = (request.form.get("content") or "").strip()
            if not content or len(content) > 2000:
                if wants_json():
                    return jsonify(error="Die Nachricht ist leer oder zu lang (max. 2000 Zeichen)."), 400
                return redirect(url_for("chat", match_id=m.id))
            if rate_limited(f"msg:{uid}", 40, 60):
                abort(429)
            msg = Message(match_id=m.id, sender_id=uid, content=content)
            m.mark_seen(uid)
            db.session.add(msg)
            db.session.commit()
            try:
                notifications.email_new_message(
                    m, msg, m.other_user(uid), base_url(), os.environ.get("SITE_NAME", "Seitentausch"))
            except Exception:
                log.exception("Nachrichten-Mail fehlgeschlagen")
            if wants_json():
                return jsonify(ok=True, message=serialize_message(msg, uid))
            return redirect(url_for("chat", match_id=m.id))

        mark_read(m)
        other = m.other_user(uid)
        km = distance_between(current_user, other)
        messages = [serialize_message(x, uid) for x in m.messages]
        return render_template(
            "chat.html", match=m, other=other, mine=m.my_book(uid), theirs=m.their_book(uid),
            messages=messages, distance=geo.format_distance(km) if km is not None else "",
            report_reasons=REPORT_REASONS,
        )

    @app.route("/chat/<int:match_id>/messages")
    @login_required
    def chat_messages(match_id):
        m = get_match_or_404(match_id)
        uid = current_user.id
        after = request.args.get("after", type=int, default=0)
        if request.args.get("mark") == "1":  # nur, wenn der Tab wirklich sichtbar ist
            mark_read(m)
        new = Message.query.filter(Message.match_id == m.id, Message.id > after).order_by(Message.id).all()
        my_read = db.session.query(func.max(Message.id)).filter(
            Message.match_id == m.id, Message.sender_id == uid, Message.read_at.isnot(None)).scalar() or 0
        return jsonify(messages=[serialize_message(x, uid) for x in new],
                       my_read_up_to=my_read, status=m.status)

    @app.route("/chat/<int:match_id>/complete", methods=["POST"])
    @login_required
    def complete_match(match_id):
        m = get_match_or_404(match_id)
        now = datetime.utcnow()
        m.status, m.completed_at = "completed", now
        for b in (m.book1, m.book2):
            b.is_active, b.swapped_at = False, now
        db.session.commit()
        flash("Super, der Tausch ist abgeschlossen! Beide Bücher werden nicht mehr angeboten.", "success")
        return redirect(url_for("chat", match_id=m.id))

    @app.route("/chat/<int:match_id>/reopen", methods=["POST"])
    @login_required
    def reopen_match(match_id):
        m = get_match_or_404(match_id)
        m.status, m.completed_at = "active", None
        for b in (m.book1, m.book2):
            if b.swapped_at and not b.deleted_at:
                b.is_active, b.swapped_at = True, None
        db.session.commit()
        flash("Der Tausch ist wieder offen.", "success")
        return redirect(url_for("chat", match_id=m.id))

    @app.route("/chat/<int:match_id>/block", methods=["POST"])
    @login_required
    def block_user(match_id):
        m = get_match_or_404(match_id)
        other = m.other_user(current_user.id)
        if not Block.query.filter_by(blocker_id=current_user.id, blocked_id=other.id).first():
            db.session.add(Block(blocker_id=current_user.id, blocked_id=other.id))
            db.session.commit()
        flash(f"{other.name} wurde blockiert. Ihr seht eure Bücher und Chats nicht mehr.", "success")
        return redirect(url_for("matches"))

    @app.route("/chat/<int:match_id>/report", methods=["POST"])
    @login_required
    def report_user(match_id):
        m = get_match_or_404(match_id)
        other = m.other_user(current_user.id)
        reason = request.form.get("reason", "sonstiges")
        if reason not in REPORT_REASONS:
            reason = "sonstiges"
        if rate_limited(f"report:{current_user.id}", 5, 3600):
            abort(429)
        details = request.form.get("details", "").strip()[:1000]
        db.session.add(Report(reporter_id=current_user.id, reported_id=other.id,
                              reason=reason, details=details or None))
        if request.form.get("also_block") == "yes" and not Block.query.filter_by(
                blocker_id=current_user.id, blocked_id=other.id).first():
            db.session.add(Block(blocker_id=current_user.id, blocked_id=other.id))
        db.session.commit()

        admin_mail = os.environ.get("ADMIN_EMAIL")
        if admin_mail:
            mailer.send_mail(
                admin_mail, f"Meldung: {other.name} (ID {other.id})",
                f"{current_user.name} (ID {current_user.id}) meldet {other.name} (ID {other.id}), {other.email}\n"
                f"Grund: {REPORT_REASONS[reason]}\n\n{details}\n")
        flash("Danke für deine Meldung. Wir sehen sie uns an.", "success")
        return redirect(url_for("matches") if request.form.get("also_block") == "yes"
                        else url_for("chat", match_id=m.id))

    # ------------------------------------------------------------ Live-Benachrichtigungen

    @app.route("/api/notifications")
    @login_required
    def api_notifications():
        uid = current_user.id
        since_match = request.args.get("since_match", type=int, default=0)
        since_message = request.args.get("since_message", type=int, default=0)
        current_match = request.args.get("current_match", type=int)
        wm = notifications.watermark(uid)  # zuerst: Ereignisse danach gehen nicht verloren
        events = notifications.events_since(
            uid, since_match, since_message, current_match, wm["match"], wm["message"])
        return jsonify(counts=notifications.counts(uid), events=events, since=wm)

    # ------------------------------------------------------------ Profil

    @app.route("/profile")
    @login_required
    def profile():
        blocked = (User.query.join(Block, Block.blocked_id == User.id)
                   .filter(Block.blocker_id == current_user.id).all())
        return render_template(
            "profile.html", mail_configured=mailer.is_configured(), blocked=blocked,
            books_count=Book.query.filter_by(owner_id=current_user.id, deleted_at=None).count(),
        )

    @app.route("/unblock/<int:user_id>", methods=["POST"])
    @login_required
    def unblock_user(user_id):
        Block.query.filter_by(blocker_id=current_user.id, blocked_id=user_id).delete()
        db.session.commit()
        flash("Blockierung aufgehoben.", "success")
        return redirect(url_for("profile"))

    @app.route("/profile/update", methods=["POST"])
    @login_required
    def update_profile():
        name = request.form.get("name", "").strip()[:100]
        if not name:
            flash("Der Name darf nicht leer sein.", "error")
            return redirect(url_for("profile"))
        current_user.name = name
        current_user.email_notifications = request.form.get("email_notifications") == "yes"
        db.session.commit()
        flash("Profil gespeichert.", "success")
        return redirect(url_for("profile"))

    @app.route("/profile/password", methods=["POST"])
    @login_required
    def change_password():
        if rate_limited(f"pwchange:{current_user.id}", 10, 3600):
            abort(429)
        if not current_user.check_password(request.form.get("current_password", "")):
            flash("Das aktuelle Passwort ist falsch.", "error")
            return redirect(url_for("profile"))
        new = request.form.get("new_password", "")
        if len(new) < MIN_PASSWORD:
            flash(f"Das neue Passwort muss mindestens {MIN_PASSWORD} Zeichen lang sein.", "error")
            return redirect(url_for("profile"))
        if new != request.form.get("new_password2", ""):
            flash("Die beiden neuen Passwörter stimmen nicht überein.", "error")
            return redirect(url_for("profile"))
        current_user.set_password(new)
        db.session.commit()
        login_user(current_user, remember=True)  # dieses Geraet bleibt angemeldet, alle anderen nicht
        flash("Passwort geändert. Auf allen anderen Geräten wurdest du abgemeldet.", "success")
        return redirect(url_for("profile"))

    @app.route("/profile/delete", methods=["POST"])
    @login_required
    def delete_account():
        if rate_limited(f"delete:{current_user.id}", 5, 3600):
            abort(429)
        if request.form.get("confirm") != "yes" or not current_user.check_password(request.form.get("password", "")):
            flash("Zum Löschen bitte das Passwort eingeben und die Bestätigung ankreuzen.", "error")
            return redirect(url_for("profile") + "#konto-loeschen")
        uid = current_user.id
        logout_user()
        purge_user(uid)
        flash("Dein Konto und alle zugehörigen Daten wurden gelöscht.", "success")
        return redirect(url_for("index"))


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
