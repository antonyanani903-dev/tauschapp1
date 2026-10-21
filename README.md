# Seitentausch – Buchtausch-App

Flask + PostgreSQL. Bücher hochladen, liken, bei gegenseitigem Interesse matchen, chatten, tauschen.

## Neu in dieser Version
- **Benachrichtigungen**: Match-Fenster beim Swipen, Hinweis-Einblendungen und Zähler (Tab „Matches", Browser-Tab-Titel) für neue Matches und Nachrichten; optional Browser-Hinweise; E-Mail, wenn man gerade nicht in der App ist.
- **Konto**: Passwort vergessen (E-Mail-Link, 1 Std., einmalig), Passwort ändern, Konto löschen (alle Daten).
- **Standort**: PLZ/Ort + Umkreis (5–200 km oder überall), nächste Bücher zuerst, Entfernung auf 5 km gerundet.
- **Weitere Details**: Buch bearbeiten/pausieren/löschen, Fotos in der Datenbank, „Tausch abgeschlossen", Lesebestätigung, Schnellantworten, Blockieren & Melden, Datenschutz-Haken, Rate-Limits, CSRF-Schutz.

## Aktualisieren
1. Alle Dateien dieses Pakets auf GitHub hochladen (bestehende ersetzen; `requirements.txt` und `render.yaml` gehören dazu). Die Dateien liegen im Hauptverzeichnis des Repos.
2. Render deployt automatisch (oder: *Manual Deploy → Deploy latest commit*). Neue Tabellen/Spalten legt die App beim Start selbst an – bestehende Daten bleiben erhalten.

## Umgebungsvariablen (Render → Environment)
| Variable | Zweck |
|---|---|
| `SECRET_KEY`, `DATABASE_URL` | bereits gesetzt |
| `APP_BASE_URL` | z. B. `https://tausch.deine-domain.de` – Links in E-Mails |
| `GEO_CONTACT` | deine E-Mail – verlangt der Ortssuch-Dienst (Nominatim) |
| `MAIL_SERVER` `MAIL_PORT` `MAIL_USERNAME` `MAIL_PASSWORD` `MAIL_FROM` | E-Mail-Versand. IONOS: `smtp.ionos.de`, `587`, Postfach-Adresse + Passwort |
| `ADMIN_EMAIL` | bekommt Meldungen von Nutzern |
| `IMPRESSUM_URL`, `PRIVACY_URL` | Links im Footer / bei der Registrierung |
| `SITE_NAME` | Name (Standard: Seitentausch) |

Ohne `MAIL_*` läuft alles, nur ohne E-Mails (Passwort-Reset funktioniert dann nicht).

## Moderation
Es gibt bewusst keine Admin-Webseite. Meldungen siehst du per E-Mail und mit
`DATABASE_URL="<externe URL aus Render>" python admin_tool.py reports` (auch `ban <id>`, `unban <id>`, `handled <id>`).

## Lokal testen
```
pip install -r requirements.txt pytest
python -m pytest tests
python app.py
```
