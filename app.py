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
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    # Formato de moneda disponible en todas las plantillas
    @app.context_processor
    def inject_helpers():
        currency = app.config.get("CURRENCY", "$")

        def money(value):
            try:
                return f"{currency} {float(value):,.2f}"
            except (TypeError, ValueError):
                return f"{currency} 0.00"

        return {"money": money, "currency": currency}

    # Crear las tablas si no existen y aplicar migración ligera de columnas nuevas
    with app.app_context():
        import models  # noqa: F401  (registra los modelos)
        db.create_all()
        models.ensure_schema()

    return app


app = create_app()


if __name__ == "__main__":
    # Solo para desarrollo local. En producción se usa gunicorn (ver Procfile).
    app.run(debug=True, host="127.0.0.1", port=5000)
