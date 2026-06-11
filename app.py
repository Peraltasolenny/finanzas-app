"""Punto de entrada de la app (application factory)."""
from flask import Flask

from config import Config
from extensions import db, login_manager, csrf


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Inicializar extensiones
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Registrar modelos y blueprints
    from auth import auth_bp
    from main import main_bp
    from v2 import v2_bp
    from email_import import email_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(v2_bp)
    app.register_blueprint(email_bp)

    # Formato de moneda disponible en todas las plantillas
    @app.context_processor
    def inject_helpers():
        from flask_login import current_user
        currency = app.config.get("CURRENCY", "$")
        # Usa la moneda base del usuario autenticado, si aplica.
        try:
            if current_user.is_authenticated:
                currency = current_user.settings.base_currency
        except Exception:
            pass

        def money(value, code=None):
            try:
                return f"{code or currency} {float(value):,.2f}"
            except (TypeError, ValueError):
                return f"{code or currency} 0.00"

        # Monedas disponibles para los desplegables: base + las que tengan tasa.
        monedas = [currency]
        bancos_nombres = []
        bancos_data = {}
        try:
            if current_user.is_authenticated:
                from models import ExchangeRate, Bank
                for r in ExchangeRate.query.filter_by(user_id=current_user.id).order_by(ExchangeRate.code).all():
                    if r.code not in monedas:
                        monedas.append(r.code)
                for b in Bank.query.filter_by(user_id=current_user.id).order_by(Bank.name).all():
                    bancos_nombres.append(b.name)
                    bancos_data[b.name] = {"rate": b.reference_rate or "",
                                           "currencies": b.currencies or ""}
        except Exception:
            pass

        return {"money": money, "currency": currency, "monedas": monedas,
                "bancos_nombres": bancos_nombres, "bancos_data": bancos_data}

    # Crear las tablas si no existen y aplicar migración ligera de columnas nuevas
    with app.app_context():
        import models  # noqa: F401  (registra los modelos)
        db.create_all()
        models.ensure_schema()

        # Diagnóstico: deja claro en los logs a qué base de datos se conecta.
        # Si dice "sqlite" en Render, los datos se borrarán al dormir la app:
        # falta configurar DATABASE_URL (Neon) en Environment Variables.
        uri = app.config["SQLALCHEMY_DATABASE_URI"]
        backend = uri.split("://", 1)[0]
        host = uri.split("@")[-1].split("/")[0] if "@" in uri else "archivo local"
        try:
            n_users = models.User.query.count()
        except Exception:
            n_users = "?"
        print(f"[FINANZAS] Base de datos: {backend} | host: {host} | usuarios: {n_users}",
              flush=True)
        if backend.startswith("sqlite"):
            print("[FINANZAS] ⚠️  Usando SQLite LOCAL. En Render esto se borra al reiniciar. "
                  "Configura DATABASE_URL (Neon) en Environment Variables.", flush=True)

    return app


app = create_app()


if __name__ == "__main__":
    # Solo para desarrollo local. En producción se usa gunicorn (ver Procfile).
    app.run(debug=True, host="127.0.0.1", port=5000)
