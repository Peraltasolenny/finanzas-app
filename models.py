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
