"""Modelos de datos (tablas) de la app de finanzas personales.

v2: multimoneda, cuentas/productos (cuentas, tarjetas, préstamos, inversiones),
transacciones recurrentes, nómina con deducciones e ISR, salud financiera
(necesidades/gustos/inversión) y configuración por usuario.
"""
from datetime import datetime, date

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db, login_manager


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# Clasificación 50/30/20: a qué "cubeta" pertenece un gasto.
BUCKETS = {"need": "Necesidad", "want": "Gusto", "invest": "Inversión"}

# Tipos de producto financiero (Account.kind).
ACCOUNT_KINDS = {
    "ahorro": "Cuenta de ahorro",
    "complementaria": "Ahorro complementaria",
    "corriente": "Cuenta corriente",
    "tarjeta": "Tarjeta de crédito",
    "prestamo": "Préstamo",
    "certificado": "Certificado financiero",
    "corretaje": "Cuenta de corretaje",
    "inversion": "Inversión",
    "bien": "Bien / activo",
}
# Productos que son pasivos (lo que debes): restan al patrimonio.
LIABILITY_KINDS = {"tarjeta", "prestamo"}

# Frecuencias de capitalización de intereses.
CAPITALIZATIONS = {
    "none": "Sin capitalización", "daily": "Diaria", "monthly": "Mensual",
    "quarterly": "Trimestral", "semiannual": "Semestral", "annual": "Anual",
}


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)

    totp_secret = db.Column(db.String(64), nullable=True)
    totp_enabled = db.Column(db.Boolean, nullable=False, default=False)

    cedula = db.Column(db.String(20), nullable=True)
    # Familia a la que pertenece (comparten metas/gastos/patrimonio).
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=True)

    categories = db.relationship("Category", backref="user", cascade="all, delete-orphan")
    transactions = db.relationship("Transaction", backref="user", cascade="all, delete-orphan")
    budgets = db.relationship("Budget", backref="user", cascade="all, delete-orphan")
    goals = db.relationship("Goal", backref="user", cascade="all, delete-orphan")
    debts = db.relationship("Debt", backref="user", cascade="all, delete-orphan")
    accounts = db.relationship("Account", backref="user", cascade="all, delete-orphan")
    recurring_rules = db.relationship("RecurringRule", backref="user", cascade="all, delete-orphan")
    exchange_rates = db.relationship("ExchangeRate", backref="user", cascade="all, delete-orphan")
    deductions = db.relationship("PayrollDeduction", backref="user", cascade="all, delete-orphan")
    banks = db.relationship("Bank", backref="user", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # ---- 2FA (TOTP) ----
    def verify_totp(self, code):
        if not self.totp_secret:
            return False
        import pyotp
        return pyotp.TOTP(self.totp_secret).verify((code or "").strip(), valid_window=1)

    def totp_uri(self, issuer="Mis Finanzas"):
        import pyotp
        return pyotp.totp.TOTP(self.totp_secret).provisioning_uri(
            name=self.email, issuer_name=issuer)

    @property
    def settings(self):
        """Devuelve (creando si hace falta) la configuración del usuario."""
        s = UserSettings.query.filter_by(user_id=self.id).first()
        if s is None:
            s = UserSettings(user_id=self.id)
            db.session.add(s)
            db.session.commit()
        return s


class UserSettings(db.Model):
    """Configuración personalizada por usuario (centralizada en /configuracion)."""
    __tablename__ = "user_settings"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)

    # Moneda base en la que se consolidan todos los totales.
    base_currency = db.Column(db.String(8), nullable=False, default="RD$")

    # Salud financiera: metas de distribución (50/30/20 por defecto).
    pct_need = db.Column(db.Float, nullable=False, default=50.0)
    pct_want = db.Column(db.Float, nullable=False, default=30.0)
    pct_invest = db.Column(db.Float, nullable=False, default=20.0)

    # Nómina: salario bruto y frecuencia (para la vista de impuestos/ISR).
    gross_salary = db.Column(db.Float, nullable=False, default=0.0)
    salary_frequency = db.Column(db.String(12), nullable=False, default="monthly")  # monthly/biweekly


class ExchangeRate(db.Model):
    """Tasa de cambio manual: 1 unidad de `code` = `rate_to_base` en moneda base."""
    __tablename__ = "exchange_rates"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    code = db.Column(db.String(8), nullable=False)            # ej. "USD"
    rate_to_base = db.Column(db.Float, nullable=False, default=1.0)
    __table_args__ = (
        db.UniqueConstraint("user_id", "code", name="uq_rate"),
    )


class Bank(db.Model):
    """Banco/entidad del usuario: país, monedas que maneja y tasa referencial."""
    __tablename__ = "banks"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    country = db.Column(db.String(80), nullable=True)
    currencies = db.Column(db.String(120), nullable=True)       # ej. "RD$, US$"
    reference_rate = db.Column(db.Float, nullable=True)         # tasa referencial anual %


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    type = db.Column(db.String(10), nullable=False, default="expense")  # income/expense
    is_active = db.Column(db.Boolean, default=True)
    # Salud financiera: cubeta sugerida (need/want/invest). Solo aplica a gastos.
    bucket = db.Column(db.String(10), nullable=True)
    # Subcategorías: una categoría puede tener categoría padre.
    parent_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    subcategories = db.relationship("Category", backref=db.backref("parent", remote_side=[id]))


class Account(db.Model):
    """Producto financiero: cuenta de ahorro/corriente, tarjeta, préstamo o inversión."""
    __tablename__ = "accounts"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    bank = db.Column(db.String(120), nullable=False, default="")
    kind = db.Column(db.String(16), nullable=False, default="ahorro")
    currency = db.Column(db.String(8), nullable=False, default="RD$")
    balance = db.Column(db.Float, nullable=False, default=0.0)   # saldo (o deuda si es pasivo)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # Tarjeta de crédito
    credit_limit = db.Column(db.Float, nullable=True)
    cutoff_day = db.Column(db.Integer, nullable=True)
    due_day = db.Column(db.Integer, nullable=True)

    # Tarjeta / préstamo
    interest_rate = db.Column(db.Float, nullable=False, default=0.0)  # % anual
    minimum_payment = db.Column(db.Float, nullable=False, default=0.0)
    original_amount = db.Column(db.Float, nullable=True)             # monto original del préstamo

    # Capitalización de intereses (ahorro/certificado/corretaje).
    capitalization = db.Column(db.String(12), nullable=False, default="none")
    capitalization_date = db.Column(db.Date, nullable=True)
    start_date = db.Column(db.Date, nullable=True)   # fecha de inicio (préstamo/certificado)

    # Préstamo / certificado / corretaje
    term_months = db.Column(db.Integer, nullable=True)              # plazo en meses
    projected_amount = db.Column(db.Float, nullable=True)          # monto proyectado / a vencimiento
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    maturity_date = db.Column(db.Date, nullable=True)             # fecha de vencimiento
    auto_renew = db.Column(db.Boolean, nullable=False, default=False)  # renovación
    capitalizable = db.Column(db.Boolean, nullable=False, default=False)
    early_redemption = db.Column(db.Boolean, nullable=False, default=False)  # redención anticipada
    broker = db.Column(db.String(120), nullable=True)            # puesto de bolsa (corretaje)

    # Ahorro complementaria (sub-cuenta ligada a una principal)
    parent_account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)
    recurring_amount = db.Column(db.Float, nullable=True)        # aporte recurrente
    recurring_day = db.Column(db.Integer, nullable=True)         # día del aporte

    # Tarjeta: beneficio y extras
    benefit_type = db.Column(db.String(12), nullable=False, default="none")  # none/cashback/miles
    benefit_detail = db.Column(db.String(120), nullable=True)   # ej. "1.5% cashback"
    has_terminal = db.Column(db.Boolean, nullable=False, default=False)

    # Cuenta compartida
    is_joint = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    category = db.relationship("Category", foreign_keys=[category_id])
    card_balances = db.relationship("CardBalance", backref="account",
                                    cascade="all, delete-orphan", foreign_keys="CardBalance.account_id")
    credit_lines = db.relationship("CreditLine", backref="account",
                                   cascade="all, delete-orphan")
    cashback_rules = db.relationship("CashbackRule", backref="account",
                                     cascade="all, delete-orphan")

    @property
    def is_liability(self):
        return self.kind in LIABILITY_KINDS

    @property
    def utilization(self):
        """Porcentaje de utilización de una tarjeta (saldo / límite)."""
        if self.kind != "tarjeta" or not self.credit_limit:
            return None
        return min(999, round(self.balance / self.credit_limit * 100, 1))

    @property
    def available_credit(self):
        if self.kind != "tarjeta" or self.credit_limit is None:
            return None
        return max(0.0, self.credit_limit - self.balance)


class Transaction(db.Model):
    __tablename__ = "transactions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)
    type = db.Column(db.String(10), nullable=False)  # income / expense
    amount = db.Column(db.Float, nullable=False, default=0.0)
    currency = db.Column(db.String(8), nullable=True)  # None = moneda base
    description = db.Column(db.String(255), default="")
    merchant = db.Column(db.String(120), nullable=True)        # comercio
    fee = db.Column(db.Float, nullable=False, default=0.0)      # costo por transacción
    tx_date = db.Column(db.Date, nullable=False, default=date.today)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Salud financiera: override puntual de cubeta (si None, usa la de la categoría).
    bucket = db.Column(db.String(10), nullable=True)
    # Si vino de una regla recurrente (para histórico; no la afecta al editar).
    recurring_rule_id = db.Column(db.Integer, db.ForeignKey("recurring_rules.id"), nullable=True)

    category = db.relationship("Category")
    account = db.relationship("Account")

    @property
    def effective_bucket(self):
        if self.bucket:
            return self.bucket
        return self.category.bucket if self.category else None


class RecurringRule(db.Model):
    """Regla de transacción recurrente; genera transacciones reales en sus fechas."""
    __tablename__ = "recurring_rules"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    type = db.Column(db.String(10), nullable=False, default="expense")
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    currency = db.Column(db.String(8), nullable=True)
    description = db.Column(db.String(255), default="")
    bucket = db.Column(db.String(10), nullable=True)

    # weekly / biweekly / monthly / yearly
    frequency = db.Column(db.String(12), nullable=False, default="monthly")
    day_of_month = db.Column(db.Integer, nullable=True)   # para mensual/quincenal
    start_date = db.Column(db.Date, nullable=False, default=date.today)
    end_date = db.Column(db.Date, nullable=True)
    next_date = db.Column(db.Date, nullable=True)         # próxima fecha a generar
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    source = db.Column(db.String(16), nullable=True)      # ej. "payroll" (generada por nómina)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    category = db.relationship("Category")
    account = db.relationship("Account")


class PayrollDeduction(db.Model):
    """Deducción recurrente del salario (AFP, SFS, ISR u otras)."""
    __tablename__ = "payroll_deductions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    # "pct" (% del bruto), "fixed" (monto fijo) o "isr" (calculado por escala DGII)
    kind = db.Column(db.String(10), nullable=False, default="pct")
    value = db.Column(db.Float, nullable=False, default=0.0)  # % o monto; ignorado si kind=isr
    sort_order = db.Column(db.Integer, nullable=False, default=0)


class Budget(db.Model):
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


class BudgetTemplate(db.Model):
    """Plantilla de presupuesto reutilizable (mensual, evento, vacaciones, etc.)."""
    __tablename__ = "budget_templates"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="mensual")  # mensual/evento/salida/vacaciones/extraordinario
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship("BudgetTemplateItem", backref="template",
                            cascade="all, delete-orphan")

    @property
    def total(self):
        return sum(i.amount for i in self.items)


class BudgetTemplateItem(db.Model):
    __tablename__ = "budget_template_items"
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("budget_templates.id"), nullable=False)
    category_name = db.Column(db.String(80), nullable=False)
    bucket = db.Column(db.String(10), nullable=True)
    amount = db.Column(db.Float, nullable=False, default=0.0)


class Goal(db.Model):
    __tablename__ = "goals"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    target_amount = db.Column(db.Float, nullable=False, default=0.0)
    current_amount = db.Column(db.Float, nullable=False, default=0.0)
    target_date = db.Column(db.Date, nullable=True)
    priority = db.Column(db.Integer, nullable=False, default=2)   # 1 alta, 2 media, 3 baja
    currency = db.Column(db.String(8), nullable=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    is_shared = db.Column(db.Boolean, nullable=False, default=False)  # meta familiar
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    account = db.relationship("Account")
    category = db.relationship("Category")

    @property
    def progress(self):
        if self.target_amount <= 0:
            return 0
        return min(100, round(self.current_amount / self.target_amount * 100, 1))


class CardBalance(db.Model):
    """Sub-saldo de una tarjeta en una moneda específica (doble saldo)."""
    __tablename__ = "card_balances"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    currency = db.Column(db.String(8), nullable=False, default="RD$")
    balance = db.Column(db.Float, nullable=False, default=0.0)
    credit_limit = db.Column(db.Float, nullable=True)
    minimum_payment = db.Column(db.Float, nullable=False, default=0.0)
    cutoff_day = db.Column(db.Integer, nullable=True)
    due_day = db.Column(db.Integer, nullable=True)

    @property
    def utilization(self):
        if not self.credit_limit:
            return None
        return min(999, round(self.balance / self.credit_limit * 100, 1))


class CreditLine(db.Model):
    """Crédito extra / Credimás ligado a una tarjeta."""
    __tablename__ = "credit_lines"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False, default="Crédito extra")
    amount = db.Column(db.Float, nullable=False, default=0.0)
    installments = db.Column(db.Integer, nullable=True)         # cuotas
    interest_rate = db.Column(db.Float, nullable=False, default=0.0)
    fees = db.Column(db.Float, nullable=False, default=0.0)     # comisiones/avances


class CashbackRule(db.Model):
    """Regla de cashback/recompensa por categoría o comercio de una tarjeta."""
    __tablename__ = "cashback_rules"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    merchant = db.Column(db.String(120), nullable=True)
    rate = db.Column(db.Float, nullable=False, default=0.0)     # % de cashback / millas por unidad
    payout = db.Column(db.String(12), nullable=False, default="immediate")  # immediate / date
    payout_date = db.Column(db.Date, nullable=True)

    category = db.relationship("Category")


class PendingCashback(db.Model):
    """Cashback acumulado que se acreditará en una fecha futura (acreditación diferida)."""
    __tablename__ = "pending_cashback"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    currency = db.Column(db.String(8), nullable=True)
    payout_date = db.Column(db.Date, nullable=False, default=date.today)
    description = db.Column(db.String(255), default="")
    credited = db.Column(db.Boolean, nullable=False, default=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    account = db.relationship("Account")


class Family(db.Model):
    """Grupo familiar: usuarios que comparten metas, gastos y patrimonio."""
    __tablename__ = "families"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, default="Mi familia")
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AccountShare(db.Model):
    """Acceso de otro usuario a una cuenta (visibilidad / registro)."""
    __tablename__ = "account_shares"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    can_register = db.Column(db.Boolean, nullable=False, default=False)  # puede registrar o solo ver
    __table_args__ = (
        db.UniqueConstraint("account_id", "user_id", name="uq_share"),
    )

    account = db.relationship("Account", backref=db.backref("shares", cascade="all, delete-orphan"))
    user = db.relationship("User")


class Debt(db.Model):
    """Deuda. (Se mantiene por compatibilidad; los préstamos/tarjetas nuevos usan Account.)"""
    __tablename__ = "debts"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    balance = db.Column(db.Float, nullable=False, default=0.0)
    interest_rate = db.Column(db.Float, nullable=False, default=0.0)
    minimum_payment = db.Column(db.Float, nullable=False, default=0.0)
    due_day = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class DebtPayment(db.Model):
    """Histórico de abonos a una cuenta de pasivo (tarjeta/préstamo)."""
    __tablename__ = "debt_payments"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    balance_after = db.Column(db.Float, nullable=False, default=0.0)
    pay_date = db.Column(db.Date, nullable=False, default=date.today)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=True)

    account = db.relationship("Account")


# Categorías por defecto, ahora con cubeta (need/want/invest) sugerida.
DEFAULT_CATEGORIES = [
    # Ingresos (sin cubeta)
    ("Salario", "income", None),
    ("Ingresos extra", "income", None),
    ("Ingresos pasivos", "income", None),
    # Necesidades
    ("Vivienda", "expense", "need"),
    ("Servicios (luz, agua, internet)", "expense", "need"),
    ("Seguros", "expense", "need"),
    ("Transporte", "expense", "need"),
    ("Educación", "expense", "need"),
    ("Pago de deudas", "expense", "need"),
    ("Supermercado", "expense", "need"),
    ("Salud", "expense", "need"),
    ("Hogar", "expense", "need"),
    # Gustos
    ("Suscripciones", "expense", "want"),
    ("Comida fuera", "expense", "want"),
    ("Cuidado personal", "expense", "want"),
    ("Ocio y entretenimiento", "expense", "want"),
    ("Regalos y eventos", "expense", "want"),
    # Inversión
    ("Ahorro e inversión", "expense", "invest"),
]


def seed_default_categories(user):
    for nombre, tipo, bucket in DEFAULT_CATEGORIES:
        db.session.add(Category(user_id=user.id, name=nombre, type=tipo, bucket=bucket))
    db.session.commit()


DEFAULT_TEMPLATES = {
    "Presupuesto mensual": ("mensual", [
        ("Vivienda", "need", 0), ("Servicios (luz, agua, internet)", "need", 0),
        ("Supermercado", "need", 0), ("Transporte", "need", 0),
        ("Comida fuera", "want", 0), ("Ocio y entretenimiento", "want", 0),
        ("Ahorro e inversión", "invest", 0)]),
    "Evento / salida": ("evento", [
        ("Comida fuera", "want", 0), ("Transporte", "need", 0),
        ("Ocio y entretenimiento", "want", 0), ("Regalos y eventos", "want", 0)]),
    "Vacaciones": ("vacaciones", [
        ("Transporte", "need", 0), ("Hospedaje", "want", 0), ("Comida fuera", "want", 0),
        ("Ocio y entretenimiento", "want", 0), ("Cuidado personal", "want", 0)]),
}


def seed_budget_templates(user):
    for nombre, (kind, items) in DEFAULT_TEMPLATES.items():
        t = BudgetTemplate(user_id=user.id, name=nombre, kind=kind)
        db.session.add(t)
        db.session.flush()
        for cat, bucket, amount in items:
            db.session.add(BudgetTemplateItem(template_id=t.id, category_name=cat,
                                              bucket=bucket, amount=amount))
    db.session.commit()


def seed_default_deductions(user):
    """Plantilla dominicana de deducciones de nómina (editable)."""
    plantilla = [
        ("AFP (pensión)", "pct", 2.87, 1),
        ("SFS (salud)", "pct", 3.04, 2),
        ("ISR (impuesto sobre la renta)", "isr", 0.0, 3),
    ]
    for nombre, kind, value, orden in plantilla:
        db.session.add(PayrollDeduction(
            user_id=user.id, name=nombre, kind=kind, value=value, sort_order=orden))
    db.session.commit()


def ensure_schema():
    """Migración ligera: crea tablas nuevas y agrega columnas que falten.

    Funciona en SQLite (desarrollo) y PostgreSQL (producción), sin perder datos.
    """
    from sqlalchemy import inspect, text

    # create_all (llamado por app.py antes) ya crea las tablas nuevas.
    insp = inspect(db.engine)

    def add_missing(table, columns):
        existing = {c["name"] for c in insp.get_columns(table)}
        changed = False
        for col, ddl in columns.items():
            if col not in existing:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
                changed = True
        return changed

    cambios = False
    cambios |= add_missing("users", {
        "is_admin": "BOOLEAN DEFAULT FALSE",
        "is_archived": "BOOLEAN DEFAULT FALSE",
        "totp_secret": "VARCHAR(64)",
        "totp_enabled": "BOOLEAN DEFAULT FALSE",
        "cedula": "VARCHAR(20)",
        "family_id": "INTEGER",
    })
    cambios |= add_missing("categories", {
        "bucket": "VARCHAR(10)",
        "parent_id": "INTEGER",
    })
    cambios |= add_missing("transactions", {
        "account_id": "INTEGER",
        "currency": "VARCHAR(8)",
        "bucket": "VARCHAR(10)",
        "recurring_rule_id": "INTEGER",
        "merchant": "VARCHAR(120)",
        "fee": "FLOAT DEFAULT 0",
    })
    cambios |= add_missing("recurring_rules", {"source": "VARCHAR(16)"})
    cambios |= add_missing("goals", {
        "description": "VARCHAR(255)",
        "priority": "INTEGER DEFAULT 2",
        "currency": "VARCHAR(8)",
        "account_id": "INTEGER",
        "category_id": "INTEGER",
        "is_shared": "BOOLEAN DEFAULT FALSE",
    })
    cambios |= add_missing("accounts", {
        "capitalization": "VARCHAR(12) DEFAULT 'none'",
        "capitalization_date": "DATE",
        "start_date": "DATE",
        "term_months": "INTEGER",
        "projected_amount": "FLOAT",
        "category_id": "INTEGER",
        "maturity_date": "DATE",
        "auto_renew": "BOOLEAN DEFAULT FALSE",
        "capitalizable": "BOOLEAN DEFAULT FALSE",
        "early_redemption": "BOOLEAN DEFAULT FALSE",
        "broker": "VARCHAR(120)",
        "parent_account_id": "INTEGER",
        "recurring_amount": "FLOAT",
        "recurring_day": "INTEGER",
        "benefit_type": "VARCHAR(12) DEFAULT 'none'",
        "benefit_detail": "VARCHAR(120)",
        "has_terminal": "BOOLEAN DEFAULT FALSE",
        "is_joint": "BOOLEAN DEFAULT FALSE",
    })
    if cambios:
        db.session.commit()

    # Primer usuario => administrador.
    if not User.query.filter_by(is_admin=True).first():
        primero = User.query.order_by(User.id).first()
        if primero:
            primero.is_admin = True
            db.session.commit()

    # Asegura configuración y plantilla de deducciones para usuarios existentes.
    for u in User.query.all():
        if UserSettings.query.filter_by(user_id=u.id).first() is None:
            db.session.add(UserSettings(user_id=u.id))
        if PayrollDeduction.query.filter_by(user_id=u.id).first() is None:
            seed_default_deductions(u)
    db.session.commit()
