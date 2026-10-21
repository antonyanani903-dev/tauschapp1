"""Legt Tabellen an und ergaenzt neue Spalten (macht die App beim Start ohnehin automatisch).

Aufruf lokal: python init_db.py
"""
from app import create_app
from extensions import db
from migrate import ensure_columns

app = create_app()

with app.app_context():
    db.create_all()
    ensure_columns(db)
    print("Datenbank ist auf dem neuesten Stand.")
