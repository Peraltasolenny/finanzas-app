"""Extensiones de Flask, inicializadas una sola vez y compartidas por la app."""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message = "Por favor inicia sesión para continuar."
login_manager.login_message_category = "warning"
