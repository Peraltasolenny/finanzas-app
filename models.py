"""Modelos de datos (tablas) de la app de finanzas personales."""
from datetime import datetime, date

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db, login_manager


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Gestión de usuarios (solo el admin administra a los demás).
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    # Un usuario archivado no puede iniciar sesión, pero sus datos se conservan.
    is_archived = db.Column(db.Boolean, nullable=False, default=False)

    # Autenticación en dos pasos (TOTP, estilo Google Authenticator).
    totp_secret = db.Column(db.String(64), nullable=True)
    totp_enabled = db.Column(db.Boolean, nullable=False, default=False)

    categories = db.relationship("Category", backref="user", cascade="all, delete-orphan")
    transactions = db.relationship("Transaction", backref="user", cascade="all, delete-orphan")
    budgets = db.relationship("Budget", backref="user", cascade="all, delete-orphan")
    goals = db.relationship("Goal", backref="user", cascade="all, delete-orphan")
    debts = db.relationship("Debt", backref="user", cascade="all, delete-orphan")

    def set_password(self, password):
        # Hash seguro (scrypt). La contraseña en texto plano nunca se guarda.
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # ---- 2FA (TOTP) ----
    def verify_totp(self, code):
        """Valida un código de 6 dígitos contra el secreto TOTP del usuario."""
        if not self.totp_secret:
            return False
        import pyotp
        return pyotp.TOTP(self.totp_secret).verify((code or "").strip(), valid_window=1)

    def totp_uri(self, issuer="Mis Finanzas"):
        """URI otpauth:// para generar el código QR de configuración."""
        import pyotp
        return pyotp.totp.TOTP(self.totp_secret).provisioning_uri(
            name=self.email, issuer_name=issuer)


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    # "income" (ingreso) o "expense" (gasto)
    type = db.Column(db.String(10), nullable=False, default="expense")
    is_active = db.Column(db.Boolean, default=True)


class Transaction(db.Model):
    __tablename__ = "transactions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    type = db.Column(db.String(10), nullable=False)  # income / expense
    amount = db.Column(db.Float, nullable=False, default=0.0)
    description = db.Column(db.String(255), default="")
    tx_date = db.Column(db.Date, nullable=False, default=date.today)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    category = db.relationship("Category")


class Budget(db.Model):
    """Monto presupuestado para una categoría en un mes/año dado."""
    __tablename__ = "budgets"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.Float, nullable=False, default=0.0)

    category = db.relationship("Category")
    __table_args__ = (
        db.UniqueConstraint("user_id", "category_id", "year", "month", name="uq_budget"),
    )


class Goal(db.Model):
    """Meta de ahorro."""
    __tablename__ = "goals"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    target_amount = db.Column(db.Float, nullable=False, default=0.0)
    current_amount = db.Column(db.Float, nullable=False, default=0.0)
    target_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def progress(self):
        if self.target_amount <= 0:
            return 0
        return min(100, round(self.current_amount / self.target_amount * 100, 1))


class Debt(db.Model):
    """Deuda (tarjeta, préstamo, etc.)."""
    __tablename__ = "debts"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    balance = db.Column(db.Float, nullable=False, default=0.0)
    interest_rate = db.Column(db.Float, nullable=False, default=0.0)  # % anual
    minimum_payment = db.Column(db.Float, nullable=False, default=0.0)
    due_day = db.Column(db.Integer, nullable=True)  # día de corte/pago (1-31)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# Categorías que se crean automáticamente para un usuario nuevo.
DEFAULT_CATEGORIES = [
    # Ingresos
    ("Salario", "income"),
    ("Ingresos extra", "income"),
    ("Ingresos pasivos", "income"),
    # Gastos fijos
    ("Vivienda", "expense"),
    ("Servicios (luz, agua, internet)", "expense"),
    ("Seguros", "expense"),
    ("Transporte", "expense"),
    ("Educación", "expense"),
    ("Suscripciones", "expense"),
    ("Pago de deudas", "expense"),
    # Gastos variables
    ("Supermercado", "expense"),
    ("Comida fuera", "expense"),
    ("Salud", "expense"),
    ("Cuidado personal", "expense"),
    ("Hogar", "expense"),
    ("Ocio y entretenimiento", "expense"),
    ("Regalos y eventos", "expense"),
    ("Ahorro e inversión", "expense"),
]


def seed_default_categories(user):
    for nombre, tipo in DEFAULT_CATEGORIES:
        db.session.add(Category(user_id=user.id, name=nombre, type=tipo))
    db.session.commit()


def ensure_schema():
    """Migración ligera: agrega columnas nuevas a 'users' si faltan y marca admin.

    Evita perder la base de datos existente al añadir is_admin / is_archived / 2FA.
    Funciona en SQLite (desarrollo) y PostgreSQL (producción).
    """
    from sqlalchemy import inspect, text

    insp = inspect(db.engine)
    existing = {c["name"] for c in insp.get_columns("users")}
    nuevas = {
        "is_admin": "BOOLEAN DEFAULT FALSE",
        "is_archived": "BOOLEAN DEFAULT FALSE",
        "totp_secret": "VARCHAR(64)",
        "totp_enabled": "BOOLEAN DEFAULT FALSE",
    }
    cambios = False
    for col, ddl in nuevas.items():
        if col not in existing:
            db.session.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))
            cambios = True
    if cambios:
        db.session.commit()

    # El primer usuario (el más antiguo) queda como administrador si nadie lo es.
    if not User.query.filter_by(is_admin=True).first():
        primero = User.query.order_by(User.id).first()
        if primero:
            primero.is_admin = True
            db.session.commit()
