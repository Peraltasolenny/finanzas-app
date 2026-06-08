"""Lógica financiera central: monedas, nómina/ISR, recurrencias, salud y patrimonio."""
import calendar
from datetime import date, timedelta

from extensions import db
from models import (Transaction, RecurringRule, Account, ExchangeRate,
                    LIABILITY_KINDS)


# ----------------- multimoneda -----------------
def get_rates(user):
    """Dict {code: tasa_a_base}. La moneda base siempre vale 1."""
    base = user.settings.base_currency
    rates = {base: 1.0}
    for r in ExchangeRate.query.filter_by(user_id=user.id).all():
        rates[r.code] = r.rate_to_base
    return rates


def to_base(amount, currency, user, rates=None):
    """Convierte `amount` (en `currency`) a la moneda base del usuario."""
    base = user.settings.base_currency
    if not currency or currency == base:
        return amount
    if rates is None:
        rates = get_rates(user)
    return amount * rates.get(currency, 1.0)


def tx_value_base(t, user, rates=None):
    """Valor de la transacción en moneda base. En gastos incluye el costo (fee)."""
    val = to_base(t.amount, t.currency, user, rates)
    if t.type == "expense" and getattr(t, "fee", 0):
        val += to_base(t.fee, t.currency, user, rates)
    return val


# ----------------- nómina e ISR (DGII República Dominicana) -----------------
# Escala ANUAL del ISR asalariado (DOP). Tramos: (límite_superior, tasa, base_acumulada).
ISR_BRACKETS = [
    (416_220.00, 0.00, 0.00),
    (624_329.00, 0.15, 0.00),
    (867_123.00, 0.20, 31_216.00),
    (float("inf"), 0.25, 79_776.00),
]


def isr_anual(base_imponible_anual):
    """ISR anual según la escala de la DGII sobre el ingreso anual gravable."""
    prev_limit = 0.0
    for i, (limit, rate, acumulado) in enumerate(ISR_BRACKETS):
        if base_imponible_anual <= limit:
            if i == 0:
                return 0.0
            exceso = base_imponible_anual - ISR_BRACKETS[i - 1][0]
            return acumulado + exceso * rate
        prev_limit = limit
    return 0.0


def compute_payroll(gross_monthly, deductions):
    """Calcula el desglose mensual de nómina.

    deductions: lista de PayrollDeduction. Las de tipo 'pct'/'fixed' se restan
    primero (ej. AFP, SFS); el 'isr' se calcula sobre (bruto - esas deducciones).
    Devuelve dict con líneas y neto.
    """
    lineas = []
    base_no_isr = 0.0
    isr_rows = []
    for d in sorted(deductions, key=lambda x: x.sort_order):
        if d.kind == "isr":
            isr_rows.append(d)
            continue
        monto = (gross_monthly * d.value / 100.0) if d.kind == "pct" else d.value
        monto = round(monto, 2)
        base_no_isr += monto
        lineas.append({"name": d.name, "detail": (f"{d.value:g}%" if d.kind == "pct" else "fijo"),
                       "amount": monto})

    # ISR sobre el salario gravable anualizado.
    base_imponible_mensual = max(0.0, gross_monthly - base_no_isr)
    isr_mes = round(isr_anual(base_imponible_mensual * 12) / 12.0, 2)
    for d in isr_rows:
        lineas.append({"name": d.name, "detail": "escala DGII", "amount": isr_mes})

    total_deducciones = round(base_no_isr + isr_mes, 2)
    neto = round(gross_monthly - total_deducciones, 2)
    return {
        "bruto": round(gross_monthly, 2),
        "lineas": lineas,
        "isr_mensual": isr_mes,
        "total_deducciones": total_deducciones,
        "neto": neto,
        "tasa_efectiva": round(total_deducciones / gross_monthly * 100, 1) if gross_monthly else 0.0,
    }


# ----------------- recurrencias -----------------
def _advance(d, frequency, day_of_month=None):
    """Devuelve la siguiente fecha según la frecuencia."""
    if frequency == "weekly":
        return d + timedelta(days=7)
    if frequency == "biweekly":
        return d + timedelta(days=14)
    if frequency == "yearly":
        try:
            return d.replace(year=d.year + 1)
        except ValueError:  # 29 feb
            return d.replace(year=d.year + 1, day=28)
    # mensual: mismo día del próximo mes (ajustado al largo del mes)
    year = d.year + (1 if d.month == 12 else 0)
    month = 1 if d.month == 12 else d.month + 1
    target_day = day_of_month or d.day
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(target_day, last_day))


def generate_due_transactions(user, today=None):
    """Materializa las transacciones recurrentes vencidas hasta hoy.

    Cada transacción generada queda independiente: editarla no afecta la regla.
    Devuelve cuántas se crearon.
    """
    today = today or date.today()
    creadas = 0
    rules = RecurringRule.query.filter_by(user_id=user.id, is_active=True).all()
    for rule in rules:
        nxt = rule.next_date or rule.start_date
        # Genera todas las ocurrencias vencidas (con tope de seguridad).
        guard = 0
        while nxt and nxt <= today and (rule.end_date is None or nxt <= rule.end_date) and guard < 600:
            db.session.add(Transaction(
                user_id=user.id, type=rule.type, category_id=rule.category_id,
                account_id=rule.account_id, amount=rule.amount, currency=rule.currency,
                description=rule.description or "(recurrente)", tx_date=nxt,
                bucket=rule.bucket, recurring_rule_id=rule.id,
            ))
            creadas += 1
            guard += 1
            nxt = _advance(nxt, rule.frequency, rule.day_of_month)
        rule.next_date = nxt
    if creadas:
        db.session.commit()
    return creadas


# ----------------- salud financiera (necesidades / gustos / inversión) -----------------
def health_breakdown(user, transactions, rates=None):
    """Suma los gastos del periodo por cubeta y los compara con las metas %."""
    rates = rates or get_rates(user)
    sums = {"need": 0.0, "want": 0.0, "invest": 0.0, "sin": 0.0}
    for t in transactions:
        if t.type != "expense":
            continue
        b = t.effective_bucket or "sin"
        sums[b] = sums.get(b, 0.0) + tx_value_base(t, user, rates)
    total = sums["need"] + sums["want"] + sums["invest"] + sums["sin"]
    s = user.settings
    metas = {"need": s.pct_need, "want": s.pct_want, "invest": s.pct_invest}
    filas = []
    for key, label in (("need", "Necesidades"), ("want", "Gustos"), ("invest", "Inversión")):
        real_pct = (sums[key] / total * 100) if total > 0 else 0.0
        filas.append({
            "key": key, "label": label, "monto": sums[key],
            "real_pct": round(real_pct, 1), "meta_pct": metas[key],
            "delta": round(real_pct - metas[key], 1),
        })
    return {"filas": filas, "total": total, "sin_clasificar": sums["sin"]}


# ----------------- proyección de valor / amortización -----------------
_CAP_PERIODS = {"daily": 365, "monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}


def projected_amount(account):
    """Calcula el monto proyectado según intereses y plazo.

    - Ahorro/certificado/corretaje/complementaria: valor futuro con capitalización.
    - Préstamo: total a pagar (capital + intereses) con amortización francesa.
    Devuelve None si faltan datos.
    """
    rate = (account.interest_rate or 0) / 100.0
    months = account.term_months or 0
    if months <= 0 or rate <= 0:
        return None

    if account.kind == "prestamo":
        principal = account.original_amount or account.balance or 0
        if principal <= 0:
            return None
        i = rate / 12.0
        cuota = principal * i / (1 - (1 + i) ** (-months))
        return round(cuota * months, 2)

    # Activos que generan interés: valor futuro.
    principal = account.balance or 0
    if principal <= 0:
        return None
    years = months / 12.0
    n = _CAP_PERIODS.get(account.capitalization, 0)
    if n:  # interés compuesto
        return round(principal * (1 + rate / n) ** (n * years), 2)
    return round(principal * (1 + rate * years), 2)  # interés simple


def amortization(principal, annual_rate, months, extra=0.0, extra_interval=1):
    """Simula un préstamo con pagos extra opcionales.

    extra: monto del pago extraordinario.
    extra_interval: cada cuántos meses se aplica el extra (1=mensual, 3=4 veces/año,
                    4=3 veces/año, 12=anual).
    Devuelve dict: cuota base, meses reales, total pagado, total interés.
    """
    if principal <= 0 or months <= 0:
        return None
    i = (annual_rate / 100.0) / 12.0
    base_cuota = principal / months if i <= 0 else principal * i / (1 - (1 + i) ** (-months))
    extra = max(0.0, extra)
    interval = max(1, extra_interval)
    saldo = principal
    total_pagado = 0.0
    meses = 0
    guard = 0
    while saldo > 0.005 and guard < 1200:
        meses += 1
        guard += 1
        interes = saldo * i
        extra_mes = extra if (meses % interval == 0) else 0.0
        pago = base_cuota + extra_mes
        capital = pago - interes
        if capital <= 0:  # el pago no cubre ni el interés
            return {"cuota": round(base_cuota, 2), "meses": None,
                    "total_pagado": None, "total_interes": None, "no_amortiza": True}
        if capital > saldo:
            capital = saldo
            pago = capital + interes
        saldo -= capital
        total_pagado += pago
    return {
        "cuota": round(base_cuota, 2),
        "meses": meses,
        "total_pagado": round(total_pagado, 2),
        "total_interes": round(total_pagado - principal, 2),
        "no_amortiza": False,
    }


# ----------------- efecto de transacciones sobre el saldo de la cuenta -----------------
def _convert(amount, from_cur, to_cur, user, rates):
    """Convierte un monto de una moneda a otra (vía la moneda base)."""
    base = user.settings.base_currency
    base_val = to_base(amount, from_cur, user, rates)
    if not to_cur or to_cur == base:
        return base_val
    rate = rates.get(to_cur, 1.0)
    return base_val / rate if rate else base_val


def apply_account_effect(tx, sign=1, rates=None):
    """Ajusta el saldo de la cuenta asociada según la transacción.

    Solo aplica a cuentas tipo ACTIVO (efectivo, ahorro, corriente, inversión...).
    Gasto → baja el saldo; ingreso → sube. sign=-1 revierte el efecto.
    Las cuentas de pasivo (tarjetas/préstamos) se manejan con sus abonos, no aquí.
    """
    from models import Account, LIABILITY_KINDS
    if not tx.account_id:
        return
    acc = db.session.get(Account, tx.account_id)
    if not acc or acc.kind in LIABILITY_KINDS:
        return
    user = acc.user
    rates = rates if rates is not None else get_rates(user)
    fee = tx.fee or 0
    if tx.type == "expense":
        monto = _convert(tx.amount + fee, tx.currency, acc.currency, user, rates)
        acc.balance -= sign * monto
    else:
        monto = _convert(tx.amount - fee, tx.currency, acc.currency, user, rates)
        acc.balance += sign * monto


# ----------------- cashback automático -----------------
def apply_cashback(user, tx):
    """Si la transacción (gasto) coincide con una regla de cashback de su tarjeta,
    crea una transacción de ingreso por el cashback. Devuelve esa transacción o None."""
    from models import CashbackRule, Category
    if tx.type != "expense" or not tx.account_id or tx.amount <= 0:
        return None
    best = None
    for r in CashbackRule.query.filter_by(account_id=tx.account_id).all():
        match = False
        if r.category_id and r.category_id == tx.category_id:
            match = True
        elif r.merchant and tx.merchant and r.merchant.lower() in tx.merchant.lower():
            match = True
        elif not r.category_id and not r.merchant:
            match = True   # regla general
        if match and (best is None or r.rate > best.rate):
            best = r
    if not best or best.rate <= 0:
        return None
    monto = round(tx.amount * best.rate / 100.0, 2)
    if monto <= 0:
        return None
    from datetime import date as _date
    from models import PendingCashback
    desc = f"Cashback: {tx.description or tx.merchant or 'compra'}"

    # Diferido: si la regla paga "a fecha" y esa fecha es futura, queda pendiente.
    if best.payout == "date" and best.payout_date and best.payout_date > _date.today():
        pc = PendingCashback(user_id=user.id, account_id=tx.account_id, amount=monto,
                             currency=tx.currency, payout_date=best.payout_date,
                             description=desc, transaction_id=tx.id)
        db.session.add(pc)
        return None   # aún no se acredita

    # Inmediato (o fecha ya pasada): se acredita ahora.
    cat = _cashback_category(user)
    cb = Transaction(
        user_id=user.id, type="income", category_id=cat.id, account_id=tx.account_id,
        amount=monto, currency=tx.currency, tx_date=tx.tx_date, description=desc)
    db.session.add(cb)
    return cb


def _cashback_category(user):
    from models import Category
    cat = Category.query.filter_by(user_id=user.id, name="Cashback", type="income").first()
    if cat is None:
        cat = Category(user_id=user.id, name="Cashback", type="income")
        db.session.add(cat)
        db.session.flush()
    return cat


def credit_due_cashback(user, today=None):
    """Materializa el cashback pendiente cuya fecha de acreditación ya llegó."""
    from datetime import date as _date
    from models import PendingCashback, Transaction
    today = today or _date.today()
    pendientes = PendingCashback.query.filter_by(user_id=user.id, credited=False).filter(
        PendingCashback.payout_date <= today).all()
    n = 0
    if pendientes:
        cat = _cashback_category(user)
        for p in pendientes:
            db.session.add(Transaction(
                user_id=user.id, type="income", category_id=cat.id, account_id=p.account_id,
                amount=p.amount, currency=p.currency, tx_date=p.payout_date,
                description=p.description or "Cashback"))
            p.credited = True
            n += 1
        db.session.commit()
    return n


# ----------------- pasivos (deuda real) -----------------
def liability_value(account, user, rates=None):
    """Deuda total de un pasivo en moneda base: saldo + sub-saldos por moneda + Credimás."""
    rates = rates or get_rates(user)
    val = to_base(account.balance, account.currency, user, rates)
    for b in getattr(account, "card_balances", []):
        val += to_base(b.balance, b.currency, user, rates)
    for cl in getattr(account, "credit_lines", []):
        val += to_base(cl.amount, account.currency, user, rates)
    return val


# ----------------- patrimonio neto -----------------
def net_worth(user, rates=None):
    """Activos (cuentas/inversiones) menos pasivos (tarjetas/préstamos), en moneda base."""
    rates = rates or get_rates(user)
    activos = pasivos = 0.0
    por_banco = {}
    for a in Account.query.filter_by(user_id=user.id, is_active=True).all():
        val = liability_value(a, user, rates) if a.kind in LIABILITY_KINDS \
            else to_base(a.balance, a.currency, user, rates)
        if a.kind in LIABILITY_KINDS:
            pasivos += val
            por_banco.setdefault(a.bank or "Sin banco", {"activo": 0.0, "pasivo": 0.0})
            por_banco[a.bank or "Sin banco"]["pasivo"] += val
        else:
            activos += val
            por_banco.setdefault(a.bank or "Sin banco", {"activo": 0.0, "pasivo": 0.0})
            por_banco[a.bank or "Sin banco"]["activo"] += val
    return {
        "activos": round(activos, 2),
        "pasivos": round(pasivos, 2),
        "patrimonio": round(activos - pasivos, 2),
        "por_banco": por_banco,
    }
