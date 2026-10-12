# Seitentausch

Buchtausch-App für gebrauchte Illustrationsbücher: Bücher hochladen, Bücher
anderer liken, bei Gegenseitigkeit entsteht ein Match, dann chatten und
treffen.

Technisch bewusst im selben Stil wie das bestehende Buchhandlungs-Projekt
gebaut: Flask + SQLAlchemy + PostgreSQL, Deployment über Render mit
`render.yaml`.

## Funktionsumfang (v1)

- Registrierung & Login (E-Mail + Passwort, mit Flask-Login)
- Bücher hochladen (Titel, Autor, Zustand, Beschreibung, optional Foto)
- Entdecken-Feed mit Like/Skip
- Matching: Ein Match entsteht erst, wenn **beide Seiten** sich gegenseitig
  ein Buch der anderen Person geliked haben (echte Tausch-Reziprozität)
- Chat pro Match (aktualisiert sich automatisch alle paar Sekunden)
- Profilseite

## Projektstruktur

```
buchtausch-app/
├── app.py              # Flask-App, alle Routen
├── models.py            # User, Book, Swipe, Match, Message
├── extensions.py         # db, login_manager
├── init_db.py            # Tabellen manuell anlegen
├── requirements.txt
├── runtime.txt            # Python-Version für Render
├── render.yaml            # Render-Deployment-Konfiguration
├── static/
│   ├── css/style.css
│   ├── js/script.js
│   ├── images/
│   └── uploads/           # hochgeladene Buchfotos (siehe Hinweis unten)
└── templates/
    ├── base.html, index.html, login.html, register.html
    ├── discover.html, my_books.html, matches.html, chat.html, profile.html
    └── 404.html
```

## Lokal starten

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # und SECRET_KEY anpassen
export $(cat .env | xargs)      # oder: pip install python-dotenv nutzen

python app.py
```

Ohne gesetzte `DATABASE_URL` läuft die App automatisch gegen eine lokale
SQLite-Datei (`local.db`) — ideal zum Entwickeln, ohne Postgres lokal
installieren zu müssen.

Die App ist dann unter `http://localhost:5000` erreichbar.

> **Hinweis für dieses Sandbox-Environment:** Falls `pip install` beim
> Paket `psycopg2-binary` einen Kompilierfehler wirft, liegt das meist an
> einer Python-Version, für die es kein fertiges Wheel gibt (z. B. sehr
> neue Python-Versionen). Für die reine SQLite-Entwicklung kannst du
> `psycopg2-binary` aus `requirements.txt` vorübergehend weglassen — auf
> Render selbst (Python-Version aus `runtime.txt`) ist das kein Problem.

## Deployment: GitHub + Render

### 1. Code auf GitHub bringen

```bash
cd buchtausch-app
git init
git add .
git commit -m "Erste Version der Seitentausch-App"
```

Dann auf [github.com](https://github.com) ein neues, leeres Repository
anlegen (z. B. `seitentausch-app`) und:

```bash
git branch -M main
git remote add origin https://github.com/DEIN-USERNAME/seitentausch-app.git
git push -u origin main
```

### 2. Render-Workspace anlegen

Damit diese App ihr eigenes 750-Stunden-Freikontingent bekommt und nicht
mit deiner Buchhandlung konkurriert: im Render-Dashboard oben links auf
den Workspace-Namen klicken → **New Workspace** → z. B. „Seitentausch"
nennen.

### 3. Mit Render verbinden

Im neuen Workspace: **New → Blueprint** → GitHub-Repo auswählen. Render
liest automatisch die `render.yaml` ein und legt sowohl den Web-Service
als auch die PostgreSQL-Datenbank an — inklusive `DATABASE_URL` und einem
zufällig generierten `SECRET_KEY`, du musst nichts von Hand eintragen.

Alternativ ohne Blueprint: **New → Web Service** → Repo auswählen →
Build Command `pip install -r requirements.txt`, Start Command
`gunicorn app:app` → danach separat **New → PostgreSQL** anlegen und die
`DATABASE_URL` sowie einen selbst gewählten `SECRET_KEY` unter
**Environment** eintragen.

### 4. Eigene Subdomain verbinden (optional)

Im Web-Service unter **Settings → Custom Domains** z. B.
`tausch.deine-buchhandlung.de` eintragen, dann bei IONOS einen
CNAME-Eintrag auf den von Render angezeigten Zielwert setzen (siehe
vorheriges Gespräch für die Schritt-für-Schritt-Anleitung).

## Bekannte Einschränkung: Foto-Uploads auf Render

Render-Webservices haben ein **ephemeres Dateisystem** — bei jedem Deploy
oder Neustart gehen Dateien im `static/uploads/`-Ordner verloren. Für den
Start reicht das zum Testen, für den dauerhaften Betrieb mit echten
Nutzerfotos solltest du später auf externen Speicher umstellen, z. B.:

- **Cloudinary** (kostenlose Stufe, einfache API für Bild-Uploads)
- **Render Disks** (persistenter Speicher, aber kostenpflichtig)
- **AWS S3 / Backblaze B2** (kostenlose Freikontingente)

Das betrifft nur Fotos, nicht die Datenbank — Nutzer, Bücher, Matches und
Chats bleiben in PostgreSQL dauerhaft erhalten.

## Nächste sinnvolle Ausbaustufen

- Passwort-Reset per E-Mail
- Mehrere Fotos pro Buch
- Standort-/Umkreissuche für Treffpunkte
- Melde-/Blockier-Funktion
- Bild-Komprimierung beim Upload (ähnlich `bildcomprim.py` aus der
  Buchhandlung)
