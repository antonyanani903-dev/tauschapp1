"""Zentrale Flask-Erweiterungen, getrennt von app.py, um Zirkelimporte zu vermeiden."""
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Bitte melde dich an, um fortzufahren."
login_manager.login_message_category = "info"
