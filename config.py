"""Configuración de la app. Todo lo sensible se lee de variables de entorno."""
import os

from dotenv import load_dotenv

# Carga el archivo .env (si existe) en desarrollo local.
load_dotenv()

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Clave secreta para firmar las sesiones/cookies.
    # NUNCA se deja en el código: se lee de la variable de entorno SECRET_KEY.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-solo-local-cambia-esto")

    # Base de datos. En local usa SQLite (un archivo).
    # En producción (Render) se usa DATABASE_URL (ej. Neon Postgres).
    _db_url = os.environ.get("DATABASE_URL", "").strip()
    if _db_url.startswith("postgres://"):
        # SQLAlchemy necesita el prefijo "postgresql://"
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _db_url or "sqlite:///" + os.path.join(basedir, "finanzas.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Símbolo de moneda (configurable). Por defecto pesos dominicanos.
    CURRENCY = os.environ.get("CURRENCY", "RD$")

    # Cookies de sesión más seguras
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # En producción (https) marca la cookie como segura
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") == "production"
