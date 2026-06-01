"""Rutas v2: configuración, productos, salud financiera, patrimonio,
proyecciones, recurrentes y nómina/ISR."""
from datetime import date, datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy import extract, func

from extensions import db
from models import (Category, Transaction, Account, RecurringRule, ExchangeRate,
                    PayrollDeduction, Budget, Goal, ACCOUNT_KINDS, BUCKETS,
                    CAPITALIZATIONS)
import finance

v2_bp = Blueprint("v2", __name__)


def _f(value, default=0.0):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


MESES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
         "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


# ----------------- CONFIGURACIÓN -----------------
@v2_bp.route("/configuracion", methods=["GET", "POST"])
@login_required
def configuracion():
    s = current_user.settings
    if request.method == "POST":
        s.base_currency = request.form.get("base_currency", "RD$").strip() or "RD$"
        s.pct_need = _f(request.form.get("pct_need"), 50)
        s.pct_want = _f(request.form.get("pct_want"), 30)
        s.pct_invest = _f(request.form.get("pct_invest"), 20)
        # Cubetas de categorías (salud financiera).
        for c in Category.query.filter_by(user_id=current_user.id, type="expense").all():
            val = request.form.get(f"bucket_{c.id}")
            c.bucket = val if val in BUCKETS else None
        db.session.commit()
        flash("Configuración guardada.", "success")
        return redirect(url_for("v2.configuracion"))

    categorias = Category.query.filter_by(
        user_id=current_user.id, type="expense").order_by(Category.name).all()
    tasas = ExchangeRate.query.filter_by(user_id=current_user.id).order_by(ExchangeRate.code).all()
    return render_template("configuracion.html", s=s, categorias=categorias,
                           tasas=tasas, buckets=BUCKETS)


@v2_bp.route("/configuracion/tasas", methods=["POST"])
@login_required
def guardar_tasa():
    code = request.form.get("code", "").strip().upper()
    rate = _f(request.form.get("rate_to_base"))
    if code and rate > 0:
        t = ExchangeRate.query.filter_by(user_id=current_user.id, code=code).first()
        if t is None:
            t = ExchangeRate(user_id=current_user.id, code=code)
            db.session.add(t)
        t.rate_to_base = rate
        db.session.commit()
        flash(f"Tasa de {code} guardada.", "success")
    else:
        flash("Código y tasa válidos son obligatorios.", "danger")
    return redirect(url_for("v2.configuracion"))


@v2_bp.route("/configuracion/tasas/<int:rate_id>/eliminar", methods=["POST"])
@login_required
def eliminar_tasa(rate_id):
    t = db.session.get(ExchangeRate, rate_id)
    if t and t.user_id == current_user.id:
        db.session.delete(t)
        db.session.commit()
        flash("Tasa eliminada.", "info")
    return redirect(url_for("v2.configuracion"))


# ----------------- NÓMINA / ISR -----------------
@v2_bp.route("/nomina", methods=["GET", "POST"])
@login_required
def nomina():
    s = current_user.settings
    if request.method == "POST":
        s.gross_salary = _f(request.form.get("gross_salary"))
        s.salary_frequency = request.form.get("salary_frequency", "monthly")
        db.session.commit()
        flash("Salario guardado.", "success")
        return redirect(url_for("v2.nomina"))

    deducciones = PayrollDeduction.query.filter_by(
        user_id=current_user.id).order_by(PayrollDeduction.sort_order).all()
    # El cálculo del ISR es mensual; si la frecuencia es quincenal, anualizamos x2/mes.
    bruto_mensual = s.gross_salary if s.salary_frequency == "monthly" else s.gross_salary * 2
    desglose = finance.compute_payroll(bruto_mensual, deducciones)
    return render_template("nomina.html", s=s, deducciones=deducciones,
                           desglose=desglose, kinds={"pct": "% del bruto",
                           "fixed": "Monto fijo", "isr": "ISR (escala DGII)"})


@v2_bp.route("/nomina/deduccion", methods=["POST"])
@login_required
def agregar_deduccion():
    name = request.form.get("name", "").strip()
    kind = request.form.get("kind", "pct")
    value = _f(request.form.get("value"))
    if name and kind in ("pct", "fixed", "isr"):
        n = PayrollDeduction.query.filter_by(user_id=current_user.id).count()
        db.session.add(PayrollDeduction(user_id=current_user.id, name=name,
                                        kind=kind, value=value, sort_order=n + 1))
        db.session.commit()
        flash("Deducción agregada.", "success")
    return redirect(url_for("v2.nomina"))


@v2_bp.route("/nomina/deduccion/<int:ded_id>/eliminar", methods=["POST"])
@login_required
def eliminar_deduccion(ded_id):
    d = db.session.get(PayrollDeduction, ded_id)
    if d and d.user_id == current_user.id:
        db.session.delete(d)
        db.session.commit()
        flash("Deducción eliminada.", "info")
    return redirect(url_for("v2.nomina"))


# ----------------- PRODUCTOS (cuentas, tarjetas, préstamos, inversiones) -----------------
def _producto_view(kinds, titulo, plantilla="accounts.html"):
    uid = current_user.id
    if request.method == "POST":
        kind = request.form.get("kind")
        if kind not in kinds:
            kind = kinds[0]
        f = request.form
        acc = Account(
            user_id=uid, name=f.get("name", "").strip(),
            bank=f.get("bank", "").strip(), kind=kind,
            currency=f.get("currency", "").strip() or current_user.settings.base_currency,
            balance=_f(f.get("balance")),
            credit_limit=_f(f.get("credit_limit")) or None,
            cutoff_day=_int(f.get("cutoff_day")),
            due_day=_int(f.get("due_day")),
            interest_rate=_f(f.get("interest_rate")),
            minimum_payment=_f(f.get("minimum_payment")),
            original_amount=_f(f.get("original_amount")) or None,
            # capitalización
            capitalization=f.get("capitalization", "none"),
            capitalization_date=_date(f.get("capitalization_date")),
            # préstamo / certificado / corretaje
            term_months=_int(f.get("term_months")),
            projected_amount=_f(f.get("projected_amount")) or None,
            category_id=_int(f.get("category_id")),
            maturity_date=_date(f.get("maturity_date")),
            auto_renew=f.get("auto_renew") == "on",
            capitalizable=f.get("capitalizable") == "on",
            early_redemption=f.get("early_redemption") == "on",
            broker=f.get("broker", "").strip() or None,
            # complementaria
            parent_account_id=_int(f.get("parent_account_id")),
            recurring_amount=_f(f.get("recurring_amount")) or None,
            recurring_day=_int(f.get("recurring_day")),
            # tarjeta: beneficio
            benefit_type=f.get("benefit_type", "none"),
            benefit_detail=f.get("benefit_detail", "").strip() or None,
            has_terminal=f.get("has_terminal") == "on",
            # compartida
            is_joint=f.get("is_joint") == "on",
        )
        if not acc.name:
            flash("El nombre es obligatorio.", "danger")
        else:
            db.session.add(acc)
            db.session.commit()
            flash("Producto agregado.", "success")
        return redirect(request.path)

    cuentas = Account.query.filter_by(user_id=uid).filter(
        Account.kind.in_(kinds)).order_by(Account.is_active.desc(), Account.bank, Account.name).all()
    categorias = Category.query.filter_by(user_id=uid, is_active=True).order_by(Category.name).all()
    # Cuentas de ahorro disponibles como "principal" para complementarias.
    parents = Account.query.filter_by(user_id=uid, is_active=True).filter(
        Account.kind.in_(["ahorro", "corriente"])).order_by(Account.name).all()
    return render_template(plantilla, cuentas=cuentas, kinds=kinds, titulo=titulo,
                           kind_labels=ACCOUNT_KINDS, caps=CAPITALIZATIONS,
                           categorias=categorias, parents=parents,
                           base=current_user.settings.base_currency)


@v2_bp.route("/productos/cuentas", methods=["GET", "POST"])
@login_required
def cuentas():
    return _producto_view(["ahorro", "corriente"], "Cuentas de ahorro")


@v2_bp.route("/productos/tarjetas", methods=["GET", "POST"])
@login_required
def tarjetas():
    return _producto_view(["tarjeta"], "Tarjetas de crédito", "tarjetas.html")


@v2_bp.route("/productos/prestamos", methods=["GET", "POST"])
@login_required
def prestamos():
    return _producto_view(["prestamo"], "Préstamos")


@v2_bp.route("/productos/inversiones", methods=["GET", "POST"])
@login_required
def inversiones():
    return _producto_view(["inversion"], "Inversiones")


@v2_bp.route("/productos/complementarias", methods=["GET", "POST"])
@login_required
def complementarias():
    return _producto_view(["complementaria"], "Ahorro complementaria")


@v2_bp.route("/productos/certificados", methods=["GET", "POST"])
@login_required
def certificados():
    return _producto_view(["certificado"], "Certificados financieros")


@v2_bp.route("/productos/corretaje", methods=["GET", "POST"])
@login_required
def corretaje():
    return _producto_view(["corretaje"], "Cuentas de corretaje")


@v2_bp.route("/productos/<int:acc_id>/eliminar", methods=["POST"])
@login_required
def eliminar_producto(acc_id):
    a = db.session.get(Account, acc_id)
    if a and a.user_id == current_user.id:
        db.session.delete(a)
        db.session.commit()
        flash("Producto eliminado.", "info")
    return redirect(request.referrer or url_for("v2.cuentas"))


@v2_bp.route("/productos/<int:acc_id>/abono", methods=["POST"])
@login_required
def abonar_producto(acc_id):
    """Abono a una tarjeta/préstamo: baja el saldo, registra histórico y crea
    una transacción de gasto (categoría 'Pago de deudas') para que afecte el presupuesto."""
    from models import DebtPayment
    a = db.session.get(Account, acc_id)
    monto = _f(request.form.get("amount"))
    if not (a and a.user_id == current_user.id):
        flash("Producto no encontrado.", "danger")
        return redirect(request.referrer or url_for("v2.tarjetas"))
    if monto <= 0:
        flash("El monto del abono debe ser mayor que cero.", "danger")
        return redirect(request.referrer or url_for("v2.tarjetas"))

    a.balance = max(0.0, a.balance - monto)

    # Categoría "Pago de deudas" (se crea si no existe) para reflejar en presupuesto.
    cat = Category.query.filter_by(user_id=current_user.id, name="Pago de deudas",
                                   type="expense").first()
    if cat is None:
        cat = Category(user_id=current_user.id, name="Pago de deudas",
                       type="expense", bucket="need")
        db.session.add(cat)
        db.session.flush()

    tx = Transaction(user_id=current_user.id, type="expense", category_id=cat.id,
                     account_id=a.id, amount=monto, currency=a.currency,
                     description=f"Abono a {a.name}", tx_date=date.today(), bucket="need")
    db.session.add(tx)
    db.session.flush()
    db.session.add(DebtPayment(user_id=current_user.id, account_id=a.id, amount=monto,
                               balance_after=a.balance, pay_date=date.today(),
                               transaction_id=tx.id))
    db.session.commit()
    flash(f"Abono registrado. Nuevo saldo: {a.balance:,.2f}. Se reflejó en tu presupuesto.", "success")
    return redirect(request.referrer or url_for("v2.tarjetas"))


# ----------------- POR BANCO -----------------
@v2_bp.route("/por-banco")
@login_required
def por_banco():
    rates = finance.get_rates(current_user)
    nw = finance.net_worth(current_user, rates)
    # Transacciones del mes actual agrupadas por banco (vía la cuenta asociada).
    hoy = date.today()
    txs = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        extract("year", Transaction.tx_date) == hoy.year,
        extract("month", Transaction.tx_date) == hoy.month,
    ).all()
    mov_banco = {}
    for t in txs:
        banco = t.account.bank if t.account and t.account.bank else "Sin banco"
        d = mov_banco.setdefault(banco, {"ingresos": 0.0, "gastos": 0.0})
        val = finance.to_base(t.amount, t.currency, current_user, rates)
        d["ingresos" if t.type == "income" else "gastos"] += val
    return render_template("por_banco.html", nw=nw, mov_banco=mov_banco,
                           base=current_user.settings.base_currency, mes=MESES[hoy.month])


# ----------------- SALUD FINANCIERA -----------------
@v2_bp.route("/salud")
@login_required
def salud():
    hoy = date.today()
    try:
        year = int(request.args.get("year", hoy.year))
        month = int(request.args.get("month", hoy.month))
    except (TypeError, ValueError):
        year, month = hoy.year, hoy.month
    txs = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        extract("year", Transaction.tx_date) == year,
        extract("month", Transaction.tx_date) == month,
    ).all()
    rates = finance.get_rates(current_user)
    health = finance.health_breakdown(current_user, txs, rates)
    ingresos = sum(finance.to_base(t.amount, t.currency, current_user, rates)
                   for t in txs if t.type == "income")
    gastos = health["total"]
    tasa_ahorro = ((ingresos - gastos) / ingresos * 100) if ingresos > 0 else 0.0
    # Puntaje simple de salud (0-100): cercanía a metas + tasa de ahorro positiva.
    desvio = sum(abs(f["delta"]) for f in health["filas"])
    score = max(0, min(100, round(100 - desvio / 2 + (10 if ingresos > gastos else -10))))
    return render_template("salud.html", health=health, year=year, month=month,
                           meses=MESES, mes_nombre=MESES[month],
                           years=list(range(hoy.year - 3, hoy.year + 2)),
                           ingresos=ingresos, gastos=gastos, tasa_ahorro=tasa_ahorro,
                           score=score, s=current_user.settings,
                           base=current_user.settings.base_currency)


# ----------------- RECURRENTES -----------------
@v2_bp.route("/recurrentes", methods=["GET", "POST"])
@login_required
def recurrentes():
    uid = current_user.id
    if request.method == "POST":
        amount = _f(request.form.get("amount"))
        start = _date(request.form.get("start_date")) or date.today()
        if amount <= 0:
            flash("El monto debe ser mayor que cero.", "danger")
        else:
            r = RecurringRule(
                user_id=uid, type=request.form.get("type", "expense"),
                category_id=_int(request.form.get("category_id")),
                account_id=_int(request.form.get("account_id")),
                amount=amount, description=request.form.get("description", "").strip(),
                frequency=request.form.get("frequency", "monthly"),
                day_of_month=start.day, start_date=start, next_date=start,
                end_date=_date(request.form.get("end_date")),
            )
            db.session.add(r)
            db.session.commit()
            flash("Regla recurrente creada. Las transacciones se generarán en sus fechas.", "success")
        return redirect(url_for("v2.recurrentes"))

    # Genera las pendientes hasta hoy al entrar.
    creadas = finance.generate_due_transactions(current_user)
    if creadas:
        flash(f"Se generaron {creadas} transacciones recurrentes pendientes.", "info")
    reglas = RecurringRule.query.filter_by(user_id=uid).order_by(
        RecurringRule.is_active.desc(), RecurringRule.next_date).all()
    categorias = Category.query.filter_by(user_id=uid, is_active=True).order_by(
        Category.type, Category.name).all()
    accounts = Account.query.filter_by(user_id=uid, is_active=True).order_by(Account.name).all()
    freqs = {"weekly": "Semanal", "biweekly": "Quincenal", "monthly": "Mensual", "yearly": "Anual"}
    return render_template("recurrentes.html", reglas=reglas, categorias=categorias,
                           accounts=accounts, freqs=freqs, hoy=date.today().isoformat())


@v2_bp.route("/recurrentes/<int:rule_id>/toggle", methods=["POST"])
@login_required
def toggle_recurrente(rule_id):
    r = db.session.get(RecurringRule, rule_id)
    if r and r.user_id == current_user.id:
        r.is_active = not r.is_active
        db.session.commit()
    return redirect(url_for("v2.recurrentes"))


@v2_bp.route("/recurrentes/<int:rule_id>/eliminar", methods=["POST"])
@login_required
def eliminar_recurrente(rule_id):
    r = db.session.get(RecurringRule, rule_id)
    if r and r.user_id == current_user.id:
        db.session.delete(r)
        db.session.commit()
        flash("Regla recurrente eliminada (las transacciones ya generadas se conservan).", "info")
    return redirect(url_for("v2.recurrentes"))


# ----------------- PATRIMONIO -----------------
@v2_bp.route("/patrimonio")
@login_required
def patrimonio():
    rates = finance.get_rates(current_user)
    nw = finance.net_worth(current_user, rates)
    cuentas = Account.query.filter_by(user_id=current_user.id, is_active=True).order_by(
        Account.kind, Account.name).all()
    detalle = [{"a": a, "base_val": finance.to_base(a.balance, a.currency, current_user, rates)}
               for a in cuentas]
    return render_template("patrimonio.html", nw=nw, detalle=detalle,
                           kind_labels=ACCOUNT_KINDS, base=current_user.settings.base_currency)


# ----------------- PROYECCIONES -----------------
@v2_bp.route("/proyecciones")
@login_required
def proyecciones():
    uid = current_user.id
    rates = finance.get_rates(current_user)
    # Promedio de ingresos/gastos de los últimos 6 meses.
    hoy = date.today()
    meses_data = []
    for i in range(5, -1, -1):
        y = hoy.year + (hoy.month - 1 - i) // 12
        m = (hoy.month - 1 - i) % 12 + 1
        txs = Transaction.query.filter(
            Transaction.user_id == uid,
            extract("year", Transaction.tx_date) == y,
            extract("month", Transaction.tx_date) == m).all()
        ing = sum(finance.to_base(t.amount, t.currency, current_user, rates) for t in txs if t.type == "income")
        gas = sum(finance.to_base(t.amount, t.currency, current_user, rates) for t in txs if t.type == "expense")
        meses_data.append({"label": f"{MESES[m][:3]} {y}", "ingresos": ing, "gastos": gas, "neto": ing - gas})

    con_datos = [m for m in meses_data if m["ingresos"] or m["gastos"]]
    prom_ing = sum(m["ingresos"] for m in con_datos) / len(con_datos) if con_datos else 0.0
    prom_gas = sum(m["gastos"] for m in con_datos) / len(con_datos) if con_datos else 0.0
    prom_neto = prom_ing - prom_gas
    nw = finance.net_worth(current_user, rates)
    # Proyección de patrimonio a 6 y 12 meses con el ahorro promedio.
    proy = [{"meses": n, "patrimonio": round(nw["patrimonio"] + prom_neto * n, 2)} for n in (3, 6, 12, 24)]
    return render_template("proyecciones.html", meses_data=meses_data, prom_ing=prom_ing,
                           prom_gas=prom_gas, prom_neto=prom_neto, nw=nw, proy=proy,
                           base=current_user.settings.base_currency)
