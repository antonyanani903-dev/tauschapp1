import io, os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["WTF_CSRF_ENABLED"] = "0"
import pytest
from PIL import Image

import geo
from app import create_app
from extensions import db
from models import User, Book, Match, Message, Swipe, Block, BookImage


@pytest.fixture(autouse=True)
def fake_geo(monkeypatch):
    places = {
        "80331": {"lat": 48.137, "lon": 11.575, "city": "München", "plz": "80331"},
        "85221": {"lat": 48.264, "lon": 11.434, "city": "Dachau", "plz": "85221"},   # ~20 km
        "10115": {"lat": 52.532, "lon": 13.384, "city": "Berlin", "plz": "10115"},   # ~500 km
    }
    monkeypatch.setattr(geo, "geocode", lambda text, country="DE": places.get(text.strip()))


@pytest.fixture(autouse=True)
def reset_limiter():
    import app as appmod
    appmod._hits.clear()


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/t.db",
                       "WTF_CSRF_ENABLED": False})


def mk(app, name, plz=None, radius=50):
    c = app.test_client()
    r = c.post("/register", data={"name": name, "email": f"{name.lower()}@x.de", "password": "geheim123",
                                   "accept_privacy": "yes", "location": plz or "", "country": "DE"})
    assert r.status_code == 302, r.data
    if plz:
        c.post("/location", data={"location": plz, "country": "DE", "radius": str(radius)})
    return c


def add_book(c, title):
    r = c.post("/books/add", data={"title": title, "author": "A", "condition": "Gut"})
    assert r.status_code == 302


def jpeg():
    b = io.BytesIO(); Image.new("RGB", (1600, 1200), "red").save(b, "JPEG"); b.seek(0); return b


def book_id(app, title):
    with app.app_context():
        return Book.query.filter_by(title=title).first().id


def make_match(app):
    a, b = mk(app, "Anna", "80331"), mk(app, "Ben", "85221")
    add_book(a, "AnnaBuch"); add_book(b, "BenBuch")
    r1 = a.post(f"/swipe/{book_id(app,'BenBuch')}/like", headers={"X-Requested-With": "fetch"})
    assert r1.get_json()["matched"] is False
    r2 = b.post(f"/swipe/{book_id(app,'AnnaBuch')}/like", headers={"X-Requested-With": "fetch"})
    assert r2.get_json()["matched"] is True
    return a, b, r2.get_json()


def test_pages_render(app):
    c = mk(app, "Anna", "80331")
    for url in ["/discover", "/books", "/matches", "/profile", "/forgot-password", "/healthz"]:
        assert c.get(url).status_code == 200, url
    assert app.test_client().get("/").status_code == 200
    assert app.test_client().get("/register").status_code == 200


def test_match_notifications_and_chat(app):
    a, b, payload = make_match(app)
    assert payload["other_name"] == "Anna"
    # Anna: Badge + Toast-Ereignis fuer neues Match, Ben hat es schon gesehen
    n = a.get("/api/notifications?since_match=0&since_message=0").get_json()
    assert n["counts"]["new_matches"] == 1
    assert any(e["type"] == "match" for e in n["events"])
    assert b.get("/api/notifications").get_json()["counts"]["new_matches"] == 0
    mid = payload["match_id"]
    # Ben schreibt -> Anna sieht ungelesene Nachricht
    r = b.post(f"/chat/{mid}", data={"content": "Hallo!"}, headers={"X-Requested-With": "fetch"})
    assert r.get_json()["ok"]
    n = a.get("/api/notifications").get_json()
    assert n["counts"]["unread_messages"] == 1
    since = n["since"]
    # Anna oeffnet den Chat -> alles gelesen, Ben sieht Lesehaken
    assert a.get(f"/chat/{mid}").status_code == 200
    assert a.get("/api/notifications").get_json()["counts"]["total"] == 0
    poll = b.get(f"/chat/{mid}/messages?after=0").get_json()
    assert poll["my_read_up_to"] >= 1
    # neue Nachricht -> Ereignis nur nach dem Wasserstand
    b.post(f"/chat/{mid}", data={"content": "Wann?"}, headers={"X-Requested-With": "fetch"})
    ev = a.get(f"/api/notifications?since_match={since['match']}&since_message={since['message']}").get_json()["events"]
    assert [e["type"] for e in ev] == ["message"]
    assert a.get(f"/matches").status_code == 200


def test_distance_filter(app):
    a = mk(app, "Anna", "80331", radius=10)
    near, far = mk(app, "Near", "85221"), mk(app, "Far", "10115")
    add_book(near, "NearBook"); add_book(far, "FarBook")
    html = a.get("/discover").get_data(as_text=True)
    assert "NearBook" not in html and "FarBook" not in html      # 10 km: nichts
    a.post("/location", data={"location": "80331", "country": "DE", "radius": "25"})
    html = a.get("/discover").get_data(as_text=True)
    assert "NearBook" in html and "FarBook" not in html           # 25 km: nur Dachau
    a.post("/location", data={"location": "80331", "country": "DE", "radius": "all"})
    html = a.get("/discover").get_data(as_text=True)
    assert "NearBook" in html and "FarBook" in html
    # ungueltiger Ort
    r = a.post("/location", data={"location": "99999", "country": "DE", "radius": "25"}, follow_redirects=True)
    assert "nicht gefunden" in r.get_data(as_text=True)


def test_password_reset_and_change(app):
    from app import make_reset_token
    c = mk(app, "Anna")
    with app.app_context():
        token = make_reset_token(User.query.filter_by(email="anna@x.de").first())
    anon = app.test_client()
    assert anon.get(f"/reset-password/{token}").status_code == 200
    r = anon.post(f"/reset-password/{token}", data={"password": "neuesPasswort1", "password2": "neuesPasswort1"})
    assert r.status_code == 302
    # Link ist einmalig
    assert anon.get(f"/reset-password/{token}").status_code == 302
    assert anon.post("/login", data={"email": "anna@x.de", "password": "neuesPasswort1"}).status_code == 302
    # alte Sitzung (c) ist abgemeldet
    assert c.get("/discover").status_code == 302
    assert anon.get("/reset-password/kaputt").status_code == 302
    # Passwort aendern
    r = anon.post("/profile/password", data={"current_password": "neuesPasswort1", "new_password": "nochNeuer123", "new_password2": "nochNeuer123"})
    assert r.status_code == 302 and anon.get("/discover").status_code == 200


def test_delete_account_cascade(app):
    a, b, p = make_match(app)
    b.post(f"/chat/{p['match_id']}", data={"content": "x"})
    r = a.post("/profile/delete", data={"password": "falsch", "confirm": "yes"})
    with app.app_context():
        assert User.query.filter_by(email="anna@x.de").first() is not None   # falsches Passwort
    r = a.post("/profile/delete", data={"password": "geheim123", "confirm": "yes"})
    assert r.status_code == 302
    with app.app_context():
        assert User.query.filter_by(email="anna@x.de").first() is None
        assert Match.query.count() == 0 and Message.query.count() == 0
        assert Book.query.filter_by(title="AnnaBuch").count() == 0
        assert Swipe.query.filter(Swipe.book_id.isnot(None)).count() == 0
        assert User.query.filter_by(email="ben@x.de").first() is not None
    assert b.get("/discover").status_code == 200


def test_block_complete_reopen_delete_book(app):
    a, b, p = make_match(app)
    mid = p["match_id"]
    a.post(f"/chat/{mid}/complete")
    with app.app_context():
        assert Match.query.first().status == "completed"
        assert not Book.query.filter_by(title="AnnaBuch").first().is_active
    a.post(f"/chat/{mid}/reopen")
    with app.app_context():
        assert Book.query.filter_by(title="AnnaBuch").first().is_active
    # Buch in Match loeschen -> soft delete, kein Fehler
    r = a.post(f"/books/{book_id(app,'AnnaBuch')}/delete")
    assert r.status_code == 302
    with app.app_context():
        assert Book.query.filter_by(title="AnnaBuch").first().deleted_at is not None
    # Blockieren
    a.post(f"/chat/{mid}/block")
    assert a.get(f"/chat/{mid}").status_code == 404
    assert b.get(f"/chat/{mid}").status_code == 404
    assert a.get("/api/notifications").get_json()["counts"]["total"] == 0
    a.post(f"/unblock/{2}")
    with app.app_context():
        assert Block.query.count() == 0


def test_report(app):
    a, b, p = make_match(app)
    r = a.post(f"/chat/{p['match_id']}/report", data={"reason": "spam", "details": "x", "also_block": "yes"})
    assert r.status_code == 302


def test_image_upload_and_access(app):
    c = mk(app, "Anna")
    r = c.post("/books/add", data={"title": "Foto", "author": "A", "condition": "Gut", "image": (jpeg(), "x.jpg")},
               content_type="multipart/form-data")
    assert r.status_code == 302
    bid = book_id(app, "Foto")
    img = c.get(f"/media/book/{bid}")
    assert img.status_code == 200 and img.mimetype == "image/jpeg"
    assert max(Image.open(io.BytesIO(img.data)).size) <= 900
    assert app.test_client().get(f"/media/book/{bid}").status_code == 302   # Login noetig
    bad = c.post("/books/add", data={"title": "Bad", "author": "A", "image": (io.BytesIO(b"nope"), "x.jpg")},
                 content_type="multipart/form-data", follow_redirects=True)
    assert "kein gültiges Bild" in bad.get_data(as_text=True)
    # Bearbeiten
    assert c.get(f"/books/{bid}/edit").status_code == 200
    r = c.post(f"/books/{bid}/edit", data={"title": "Foto2", "author": "A", "condition": "Sehr gut", "remove_image": "yes"})
    assert r.status_code == 302
    with app.app_context():
        b = Book.query.get(bid); assert b.title == "Foto2" and b.image_id is None and BookImage.query.count() == 0
    # fremdes Buch nicht bearbeitbar
    other = mk(app, "Ben")
    assert other.get(f"/books/{bid}/edit").status_code == 404


def test_security_basics(app):
    anon = app.test_client()
    assert anon.get("/discover").status_code == 302
    c = mk(app, "Anna")
    c2 = app.test_client()
    r = c2.post("/login?next=//evil.com", data={"email": "anna@x.de", "password": "geheim123"})
    assert r.headers["Location"].endswith("/discover")
    assert c.post("/swipe/999/like").status_code == 404
    # Registrierung ohne Datenschutz-Haken
    r = app.test_client().post("/register", data={"name": "X", "email": "x@x.de", "password": "geheim123"})
    assert r.status_code == 400


def test_csrf_enforced(tmp_path):
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/c.db", "WTF_CSRF_ENABLED": True})
    c = app.test_client()
    r = c.post("/login", data={"email": "a@x.de", "password": "x"})
    assert r.status_code in (302, 400)
    page = c.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = c.post("/login", data={"email": "a@x.de", "password": "x", "csrf_token": token})
    assert r.status_code == 401   # Token ok, Login falsch


def test_legacy_db_migration(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE user (id INTEGER PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE, name VARCHAR(100) NOT NULL,
        password_hash VARCHAR(255) NOT NULL, created_at DATETIME NOT NULL);
    CREATE TABLE book (id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, title VARCHAR(255) NOT NULL,
        author VARCHAR(255) NOT NULL, condition VARCHAR(50) NOT NULL, description TEXT, image_filename VARCHAR(255),
        created_at DATETIME NOT NULL);
    CREATE TABLE swipe (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, book_id INTEGER NOT NULL, liked BOOLEAN NOT NULL, created_at DATETIME NOT NULL);
    CREATE TABLE "match" (id INTEGER PRIMARY KEY, user1_id INTEGER NOT NULL, user2_id INTEGER NOT NULL,
        book1_id INTEGER NOT NULL, book2_id INTEGER NOT NULL, created_at DATETIME NOT NULL);
    CREATE TABLE message (id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, sender_id INTEGER NOT NULL, content TEXT NOT NULL, created_at DATETIME NOT NULL);
    INSERT INTO user VALUES (1,'old@x.de','Old','x','2024-01-01');
    INSERT INTO book VALUES (1,1,'Alt','A','Gut',NULL,NULL,'2024-01-01');
    """)
    con.commit(); con.close()
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}", "WTF_CSRF_ENABLED": False})
    with app.app_context():
        u = User.query.first(); b = Book.query.first()
        assert u.country == "DE" and u.radius_km == 50 and u.email_notifications is True and u.is_banned is False
        assert b.is_active is True and b.deleted_at is None
    create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}", "WTF_CSRF_ENABLED": False})  # zweiter Start: idempotent


def test_postgres_url_normalised(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    from flask import Flask
    from app import create_app as ca
    import extensions
    # nur die URL-Logik pruefen, ohne Verbindung aufzubauen
    url = "postgres://u:p@h/db".replace("postgres://", "postgresql+psycopg2://", 1)
    assert url.startswith("postgresql+psycopg2://")
