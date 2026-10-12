"""Einmaliges Anlegen aller Tabellen.

Die App ruft db.create_all() zwar schon beim Start automatisch auf, aber
dieses Skript ist nuetzlich, um das gezielt von Hand auszuloesen oder
spaeter eigene Migrationsschritte (wie bei der Buchhandlung) zu ergaenzen.

Aufruf lokal:      python init_db.py
Aufruf auf Render:  im Shell-Tab des Web-Service (nur auf bezahlten Plans
                     verfuegbar) oder einmalig lokal gegen die externe
                     Render-Postgres-URL ausfuehren.
"""
from app import create_app
from extensions import db

app = create_app()

with app.app_context():
    db.create_all()
    print("Alle Tabellen wurden angelegt (oder existierten bereits).")
