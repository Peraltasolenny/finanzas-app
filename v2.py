"""Rutas v2: configuración, productos, salud financiera, patrimonio,
proyecciones, recurrentes y nómina/ISR."""
from datetime import date, datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy import extract, func

from extensions import db
from models import (Category, Transaction, Account, RecurringRule, ExchangeRate,
                    PayrollDeduction, Budget, Goal, ACCOUNT_KINDS, BUCKETS,
                    CAPITALIZATIONS, CardBalance, CreditLine, CashbackRule,
                    Family, AccountShare, User, LIABILITY_KINDS, Bank)
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


def _add_months(d, months):
    import calendar
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last))


def _reconciliar_fechas(acc):
    """Calcula vencimiento desde inicio+plazo, o el plazo desde inicio+vencimiento."""
    if acc.start_date and acc.term_months and not acc.maturity_date:
        acc.maturity_date = _add_months(acc.start_date, acc.term_months)
    elif acc.start_date and acc.maturity_date and not acc.term_months:
        meses = (acc.maturity_date.year - acc.start_date.year) * 12 + \
                (acc.maturity_date.month - acc.start_date.month)
        acc.term_months = max(1, meses)
    elif acc.start_date and acc.term_months and acc.maturity_date:
        # Si ambos están, el inicio + plazo manda (la fecha se ajusta al plazo).
        acc.maturity_date = _add_months(acc.start_date, acc.term_months)


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
    bancos = Bank.query.filter_by(user_id=current_user.id).order_by(Bank.name).all()
    return render_template("configuracion.html", s=s, categorias=categorias,
                           tasas=tasas, buckets=BUCKETS, bancos=bancos)


@v2_bp.route("/configuracion/banco", methods=["POST"])
@login_required
def guardar_banco():
    name = request.form.get("name", "").strip()
    if name:
        db.session.add(Bank(
            user_id=current_user.id, name=name,
            country=request.form.get("country", "").strip() or None,
            currencies=request.form.get("currencies", "").strip() or None,
            reference_rate=_f(request.form.get("reference_rate")) or None,
        ))
        db.session.commit()
        flash("Banco agregado.", "success")
    else:
        flash("El nombre del banco es obligatorio.", "danger")
    return redirect(url_for("v2.configuracion"))


@v2_bp.route("/configuracion/banco/<int:bank_id>/eliminar", methods=["POST"])
@login_required
def eliminar_banco(bank_id):
    b = db.session.get(Bank, bank_id)
    if b and b.user_id == current_user.id:
        db.session.delete(b)
        db.session.commit()
        flash("Banco eliminado.", "info")
    return redirect(url_for("v2.configuracion"))


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
    accounts = Account.query.filter_by(user_id=current_user.id, is_active=True).order_by(
        Account.name).all()
    nominas_rec = RecurringRule.query.filter_by(
        user_id=current_user.id, source="payroll").count()
    return render_template("nomina.html", s=s, deducciones=deducciones,
                           desglose=desglose, accounts=accounts, nominas_rec=nominas_rec,
                           kinds={"pct": "% del bruto", "fixed": "Monto fijo",
                                  "isr": "ISR (escala DGII)"})


@v2_bp.route("/nomina/recurrencias", methods=["POST"])
@login_required
def generar_recurrencias_nomina():
    """Crea recurrencias de ingreso (salario) y gastos (deducciones) desde la nómina.

    Permite dividir el mes en 1 o 2 partidas (mensual o quincenal).
    """
    uid = current_user.id
    s = current_user.settings
    if s.gross_salary <= 0:
        flash("Primero define tu salario bruto.", "danger")
        return redirect(url_for("v2.nomina"))

    parts = 2 if request.form.get("parts") == "2" else 1
    account_id = _int(request.form.get("account_id"))
    day1 = _int(request.form.get("day1")) or 30
    day2 = _int(request.form.get("day2")) or 15
    dias = [day1] if parts == 1 else [day2, day1]   # quincena: primero el 15, luego el 30

    deducciones = PayrollDeduction.query.filter_by(user_id=uid).order_by(
        PayrollDeduction.sort_order).all()
    bruto_mensual = s.gross_salary if s.salary_frequency == "monthly" else s.gross_salary * 2
    desglose = finance.compute_payroll(bruto_mensual, deducciones)

    # Categorías de ingreso (Salario) y de deducciones.
    cat_sal = Category.query.filter_by(user_id=uid, name="Salario", type="income").first()
    if cat_sal is None:
        cat_sal = Category(user_id=uid, name="Salario", type="income")
        db.session.add(cat_sal)
        db.session.flush()
    cat_ded = Category.query.filter_by(user_id=uid, name="Impuestos y deducciones",
                                       type="expense").first()
    if cat_ded is None:
        cat_ded = Category(user_id=uid, name="Impuestos y deducciones", type="expense", bucket="need")
        db.session.add(cat_ded)
        db.session.flush()

    # Elimina recurrencias de nómina anteriores para regenerarlas.
    RecurringRule.query.filter_by(user_id=uid, source="payroll").delete()

    hoy = date.today()
    base = s.base_currency
    creadas = 0
    for d in dias:
        start = date(hoy.year, hoy.month, min(d, 28))
        # Ingreso (salario) de esta partida.
        db.session.add(RecurringRule(
            user_id=uid, type="income", category_id=cat_sal.id, account_id=account_id,
            amount=round(desglose["bruto"] / parts, 2), currency=base,
            description="[Nómina] Salario", frequency="monthly", day_of_month=d,
            start_date=start, next_date=start, source="payroll"))
        creadas += 1
        # Deducciones de esta partida (cada una como gasto).
        for line in desglose["lineas"]:
            db.session.add(RecurringRule(
                user_id=uid, type="expense", category_id=cat_ded.id, account_id=account_id,
                amount=round(line["amount"] / parts, 2), currency=base, bucket="need",
                description=f"[Nómina] {line['name']}", frequency="monthly", day_of_month=d,
                start_date=start, next_date=start, source="payroll"))
            creadas += 1
    db.session.commit()
    flash(f"Se generaron {creadas} recurrencias de nómina ({parts} partida(s) al mes). "
          f"Míralas y edítalas en Recurrentes.", "success")
    return redirect(url_for("v2.nomina"))


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
            start_date=_date(f.get("start_date")),
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
            # Reconciliar fecha de inicio, plazo y vencimiento.
            _reconciliar_fechas(acc)
            # Calcula el monto proyectado con intereses y plazo (si hay datos).
            calc = finance.projected_amount(acc)
            if calc is not None:
                acc.projected_amount = calc
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
    return _producto_view(["ahorro", "corriente"], "Cuentas")


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


def _owned_account(acc_id):
    a = db.session.get(Account, acc_id)
    return a if a and a.user_id == current_user.id else None


@v2_bp.route("/productos/tarjeta/<int:acc_id>")
@login_required
def tarjeta_detalle(acc_id):
    card = _owned_account(acc_id)
    if not card:
        flash("Tarjeta no encontrada.", "danger")
        return redirect(url_for("v2.tarjetas"))
    categorias = Category.query.filter_by(
        user_id=current_user.id, type="expense", is_active=True).order_by(Category.name).all()
    return render_template("tarjeta_detalle.html", card=card, categorias=categorias,
                           base=current_user.settings.base_currency)


@v2_bp.route("/productos/tarjeta/<int:acc_id>/saldo", methods=["POST"])
@login_required
def agregar_saldo(acc_id):
    card = _owned_account(acc_id)
    if card:
        db.session.add(CardBalance(
            account_id=card.id,
            currency=request.form.get("currency", "").strip() or current_user.settings.base_currency,
            balance=_f(request.form.get("balance")),
            credit_limit=_f(request.form.get("credit_limit")) or None,
            minimum_payment=_f(request.form.get("minimum_payment")),
            cutoff_day=_int(request.form.get("cutoff_day")),
            due_day=_int(request.form.get("due_day")),
        ))
        db.session.commit()
        flash("Sub-saldo agregado.", "success")
    return redirect(url_for("v2.tarjeta_detalle", acc_id=acc_id))


@v2_bp.route("/productos/saldo/<int:bal_id>/eliminar", methods=["POST"])
@login_required
def eliminar_saldo(bal_id):
    b = db.session.get(CardBalance, bal_id)
    if b and b.account.user_id == current_user.id:
        acc_id = b.account_id
        db.session.delete(b)
        db.session.commit()
        return redirect(url_for("v2.tarjeta_detalle", acc_id=acc_id))
    return redirect(url_for("v2.tarjetas"))


@v2_bp.route("/productos/tarjeta/<int:acc_id>/credito", methods=["POST"])
@login_required
def agregar_credito(acc_id):
    card = _owned_account(acc_id)
    if card:
        db.session.add(CreditLine(
            account_id=card.id, name=request.form.get("name", "").strip() or "Crédito extra",
            amount=_f(request.form.get("amount")),
            installments=_int(request.form.get("installments")),
            interest_rate=_f(request.form.get("interest_rate")),
            fees=_f(request.form.get("fees")),
        ))
        db.session.commit()
        flash("Crédito extra agregado.", "success")
    return redirect(url_for("v2.tarjeta_detalle", acc_id=acc_id))


@v2_bp.route("/productos/credito/<int:line_id>/eliminar", methods=["POST"])
@login_required
def eliminar_credito(line_id):
    cl = db.session.get(CreditLine, line_id)
    if cl and cl.account.user_id == current_user.id:
        acc_id = cl.account_id
        db.session.delete(cl)
        db.session.commit()
        return redirect(url_for("v2.tarjeta_detalle", acc_id=acc_id))
    return redirect(url_for("v2.tarjetas"))


@v2_bp.route("/productos/tarjeta/<int:acc_id>/cashback", methods=["POST"])
@login_required
def agregar_cashback(acc_id):
    card = _owned_account(acc_id)
    if card:
        db.session.add(CashbackRule(
            account_id=card.id, category_id=_int(request.form.get("category_id")),
            merchant=request.form.get("merchant", "").strip() or None,
            rate=_f(request.form.get("rate")),
            payout=request.form.get("payout", "immediate"),
            payout_date=_date(request.form.get("payout_date")),
        ))
        db.session.commit()
        flash("Regla de cashback agregada.", "success")
    return redirect(url_for("v2.tarjeta_detalle", acc_id=acc_id))


@v2_bp.route("/productos/cashback/<int:rule_id>/eliminar", methods=["POST"])
@login_required
def eliminar_cashback(rule_id):
    r = db.session.get(CashbackRule, rule_id)
    if r and r.account.user_id == current_user.id:
        acc_id = r.account_id
        db.session.delete(r)
        db.session.commit()
        return redirect(url_for("v2.tarjeta_detalle", acc_id=acc_id))
    return redirect(url_for("v2.tarjetas"))


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
    neto = ingresos - gastos
    tasa_ahorro = (neto / ingresos * 100) if ingresos > 0 else 0.0

    # Ahorro líquido = cuentas de ahorro/corriente, convertido a base.
    ahorro_liquido = sum(
        finance.to_base(a.balance, a.currency, current_user, rates)
        for a in Account.query.filter_by(user_id=current_user.id, is_active=True)
        .filter(Account.kind.in_(["ahorro", "corriente"])).all())

    # Gasto promedio mensual (últimos 3 meses).
    sumg = cont = 0
    for i in range(3):
        y = year + (month - 1 - i) // 12
        m = (month - 1 - i) % 12 + 1
        mt = Transaction.query.filter(
            Transaction.user_id == current_user.id, Transaction.type == "expense",
            extract("year", Transaction.tx_date) == y,
            extract("month", Transaction.tx_date) == m).all()
        g = sum(finance.to_base(t.amount, t.currency, current_user, rates) for t in mt)
        if g > 0:
            sumg += g
            cont += 1
    gasto_prom = (sumg / cont) if cont else gastos

    # Deuda y pagos mínimos.
    from models import LIABILITY_KINDS, Debt
    pasivos = Account.query.filter_by(user_id=current_user.id, is_active=True).filter(
        Account.kind.in_(LIABILITY_KINDS)).all()
    deudas = Debt.query.filter_by(user_id=current_user.id).all()
    pago_min = sum(finance.to_base(a.minimum_payment, a.currency, current_user, rates) for a in pasivos) \
        + sum(d.minimum_payment for d in deudas)

    # Métricas.
    cobertura_meses = round(ahorro_liquido / gasto_prom, 1) if gasto_prom > 0 else 0.0
    carga_deuda = round(pago_min / ingresos * 100, 1) if ingresos > 0 else 0.0
    flujo_libre = round(neto - pago_min, 2)

    # Puntaje (0-100): ahorro (40) + emergencia (30) + deuda (30).
    p_ahorro = min(40, max(0, tasa_ahorro / 20 * 40))
    p_emerg = min(30, cobertura_meses / 6 * 30)
    p_deuda = 30 * (1 - min(carga_deuda, 50) / 50)
    score = max(0, min(100, round(p_ahorro + p_emerg + p_deuda)))
    estado = ("Excelente" if score >= 75 else "Bien" if score >= 50
              else "Mejorable" if score >= 30 else "Atención")

    # Sugerencias.
    sugerencias = []
    if tasa_ahorro < 20:
        sugerencias.append(f"Tu tasa de ahorro es {tasa_ahorro:.1f}%. Apunta al menos al 20%: revisa gastos en 'Gustos'.")
    if cobertura_meses < 3:
        sugerencias.append(f"Tu fondo de emergencia cubre {cobertura_meses} meses. Lo ideal son 3 a 6 meses de gastos.")
    if carga_deuda > 35:
        sugerencias.append(f"Tus pagos de deuda son {carga_deuda:.0f}% de tus ingresos (alto). Intenta bajar de 35%.")
    if health["sin_clasificar"] > 0:
        sugerencias.append("Tienes gastos sin clasificar; asígnales un grupo en Configuración para una mejor lectura.")
    if not sugerencias:
        sugerencias.append("¡Vas muy bien! Mantén tu tasa de ahorro y tu fondo de emergencia.")

    return render_template("salud.html", health=health, year=year, month=month,
                           meses=MESES, mes_nombre=MESES[month],
                           years=list(range(hoy.year - 3, hoy.year + 2)),
                           ingresos=ingresos, gastos=gastos, tasa_ahorro=tasa_ahorro,
                           score=score, estado=estado, sugerencias=sugerencias,
                           ahorro_liquido=ahorro_liquido, gasto_prom=gasto_prom,
                           cobertura_meses=cobertura_meses, carga_deuda=carga_deuda,
                           flujo_libre=flujo_libre, s=current_user.settings,
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


@v2_bp.route("/recurrentes/<int:rule_id>/editar", methods=["GET", "POST"])
@login_required
def editar_recurrente(rule_id):
    r = db.session.get(RecurringRule, rule_id)
    if not r or r.user_id != current_user.id:
        flash("Regla no encontrada.", "danger")
        return redirect(url_for("v2.recurrentes"))
    uid = current_user.id
    if request.method == "POST":
        r.type = request.form.get("type", r.type)
        r.category_id = _int(request.form.get("category_id"))
        r.account_id = _int(request.form.get("account_id"))
        r.amount = _f(request.form.get("amount"))
        r.description = request.form.get("description", "").strip()
        r.frequency = request.form.get("frequency", r.frequency)
        nuevo_inicio = _date(request.form.get("start_date"))
        if nuevo_inicio:
            r.start_date = nuevo_inicio
            r.day_of_month = nuevo_inicio.day
            # Si la próxima fecha quedó antes del nuevo inicio, ajústala.
            if not r.next_date or r.next_date < nuevo_inicio:
                r.next_date = nuevo_inicio
        r.end_date = _date(request.form.get("end_date"))
        db.session.commit()
        flash("Regla recurrente actualizada (las transacciones ya generadas no cambian).", "success")
        return redirect(url_for("v2.recurrentes"))

    categorias = Category.query.filter_by(user_id=uid, is_active=True).order_by(
        Category.type, Category.name).all()
    accounts = Account.query.filter_by(user_id=uid, is_active=True).order_by(Account.name).all()
    freqs = {"weekly": "Semanal", "biweekly": "Quincenal", "monthly": "Mensual", "yearly": "Anual"}
    return render_template("recurrente_edit.html", r=r, categorias=categorias,
                           accounts=accounts, freqs=freqs)


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
@v2_bp.route("/patrimonio", methods=["GET", "POST"])
@login_required
def patrimonio():
    uid = current_user.id
    if request.method == "POST":
        # Registrar un bien / activo (ej. vehículo, propiedad).
        nombre = request.form.get("name", "").strip()
        if nombre:
            db.session.add(Account(
                user_id=uid, name=nombre, kind="bien",
                bank=request.form.get("bank", "").strip(),
                currency=request.form.get("currency", "").strip() or current_user.settings.base_currency,
                balance=_f(request.form.get("balance"))))
            db.session.commit()
            flash("Bien/activo registrado.", "success")
        return redirect(url_for("v2.patrimonio"))

    rates = finance.get_rates(current_user)
    nw = finance.net_worth(current_user, rates)
    cuentas = Account.query.filter_by(user_id=uid, is_active=True).order_by(
        Account.kind, Account.name).all()
    detalle = [{"a": a, "base_val": finance.to_base(a.balance, a.currency, current_user, rates)}
               for a in cuentas]

    # Ratio de deuda y composición del patrimonio (activos por tipo).
    ratio_deuda = round(nw["pasivos"] / nw["activos"] * 100, 1) if nw["activos"] > 0 else 0.0
    composicion = {}
    for d in detalle:
        if d["a"].kind not in LIABILITY_KINDS:
            label = ACCOUNT_KINDS.get(d["a"].kind, d["a"].kind)
            composicion[label] = composicion.get(label, 0.0) + d["base_val"]
    comp_items = sorted(composicion.items(), key=lambda x: -x[1])
    comp_chart = {"labels": [c[0] for c in comp_items], "valores": [round(c[1], 2) for c in comp_items]}

    return render_template("patrimonio.html", nw=nw, detalle=detalle,
                           kind_labels=ACCOUNT_KINDS, base=current_user.settings.base_currency,
                           ratio_deuda=ratio_deuda, comp_chart=comp_chart,
                           monedas=reports_monedas_v2(current_user))


def reports_monedas_v2(user):
    from models import ExchangeRate
    ms = [user.settings.base_currency]
    for r in ExchangeRate.query.filter_by(user_id=user.id).order_by(ExchangeRate.code).all():
        if r.code not in ms:
            ms.append(r.code)
    return ms


# ----------------- FAMILIA -----------------
@v2_bp.route("/familia", methods=["GET", "POST"])
@login_required
def familia():
    uid = current_user.id
    fam = db.session.get(Family, current_user.family_id) if current_user.family_id else None

    if request.method == "POST":
        # Crear familia (el usuario actual queda como dueño y miembro).
        nombre = request.form.get("name", "").strip() or "Mi familia"
        if fam is None:
            fam = Family(name=nombre, owner_id=uid)
            db.session.add(fam)
            db.session.flush()
            current_user.family_id = fam.id
            db.session.commit()
            flash("Familia creada.", "success")
        return redirect(url_for("v2.familia"))

    if fam is None:
        return render_template("familia.html", fam=None)

    miembros = User.query.filter_by(family_id=fam.id).order_by(User.id).all()
    rates = finance.get_rates(current_user)
    base = current_user.settings.base_currency

    def patrimonio_de(u):
        act = pas = 0.0
        for a in Account.query.filter_by(user_id=u.id, is_active=True).all():
            v = finance.to_base(a.balance, a.currency, current_user, rates)
            if a.kind in LIABILITY_KINDS:
                pas += v
            else:
                act += v
        return act - pas

    resumen = []
    total_patrimonio = 0.0
    for m in miembros:
        p = patrimonio_de(m)
        total_patrimonio += p
        resumen.append({"u": m, "patrimonio": p, "is_owner": m.id == fam.owner_id})

    # Metas familiares (compartidas) de todos los miembros.
    ids = [m.id for m in miembros]
    metas = Goal.query.filter(Goal.user_id.in_(ids), Goal.is_shared.is_(True)).all()

    # Gastos compartidos del mes (suma de gastos de todos los miembros).
    hoy = date.today()
    txs = Transaction.query.filter(
        Transaction.user_id.in_(ids), Transaction.type == "expense",
        extract("year", Transaction.tx_date) == hoy.year,
        extract("month", Transaction.tx_date) == hoy.month).all()
    gastos_mes = sum(finance.to_base(t.amount, t.currency, current_user, rates) for t in txs)

    # Cuentas que me han compartido y cuentas que yo comparto.
    compartidas_conmigo = AccountShare.query.filter_by(user_id=uid).all()
    mis_cuentas = Account.query.filter_by(user_id=uid, is_active=True).order_by(Account.name).all()

    return render_template("familia.html", fam=fam, miembros=miembros, resumen=resumen,
                           total_patrimonio=total_patrimonio, metas=metas, gastos_mes=gastos_mes,
                           es_dueno=(fam.owner_id == uid), base=base,
                           compartidas_conmigo=compartidas_conmigo, mis_cuentas=mis_cuentas,
                           mes=MESES[hoy.month])


@v2_bp.route("/familia/miembro", methods=["POST"])
@login_required
def agregar_miembro():
    fam = db.session.get(Family, current_user.family_id) if current_user.family_id else None
    if not fam or fam.owner_id != current_user.id:
        flash("Solo el dueño de la familia puede agregar miembros.", "danger")
        return redirect(url_for("v2.familia"))
    email = request.form.get("email", "").strip().lower()
    u = User.query.filter_by(email=email).first()
    if not u:
        flash("No existe un usuario con ese correo. Pídele al admin que cree su cuenta primero.", "danger")
    elif u.family_id:
        flash("Ese usuario ya pertenece a una familia.", "danger")
    else:
        u.family_id = fam.id
        db.session.commit()
        flash(f"{u.name or u.email} se unió a la familia.", "success")
    return redirect(url_for("v2.familia"))


@v2_bp.route("/familia/miembro/<int:user_id>/quitar", methods=["POST"])
@login_required
def quitar_miembro(user_id):
    fam = db.session.get(Family, current_user.family_id) if current_user.family_id else None
    u = db.session.get(User, user_id)
    if not fam or fam.owner_id != current_user.id:
        flash("Solo el dueño puede quitar miembros.", "danger")
    elif u and u.id == fam.owner_id:
        flash("El dueño no puede quitarse a sí mismo (elimina la familia).", "danger")
    elif u and u.family_id == fam.id:
        u.family_id = None
        db.session.commit()
        flash("Miembro removido de la familia.", "info")
    return redirect(url_for("v2.familia"))


@v2_bp.route("/familia/salir", methods=["POST"])
@login_required
def salir_familia():
    current_user.family_id = None
    db.session.commit()
    flash("Saliste de la familia.", "info")
    return redirect(url_for("v2.familia"))


@v2_bp.route("/familia/compartir", methods=["POST"])
@login_required
def compartir_cuenta():
    acc = _owned_account(_int(request.form.get("account_id")))
    u = db.session.get(User, _int(request.form.get("user_id")))
    can_register = request.form.get("can_register") == "on"
    if acc and u and u.id != current_user.id:
        share = AccountShare.query.filter_by(account_id=acc.id, user_id=u.id).first()
        if share is None:
            share = AccountShare(account_id=acc.id, user_id=u.id)
            db.session.add(share)
        share.can_register = can_register
        db.session.commit()
        flash(f"Cuenta compartida con {u.name or u.email}.", "success")
    return redirect(url_for("v2.familia"))


@v2_bp.route("/familia/compartir/<int:share_id>/quitar", methods=["POST"])
@login_required
def quitar_comparticion(share_id):
    sh = db.session.get(AccountShare, share_id)
    if sh and sh.account.user_id == current_user.id:
        db.session.delete(sh)
        db.session.commit()
        flash("Se quitó el acceso compartido.", "info")
    return redirect(url_for("v2.familia"))


# ----------------- SIMULADOR DE PAGOS EXTRAORDINARIOS -----------------
@v2_bp.route("/simulador")
@login_required
def simulador():
    uid = current_user.id
    prestamos = Account.query.filter_by(user_id=uid, kind="prestamo", is_active=True).all()

    loan_id = _int(request.args.get("loan_id"))
    principal = _f(request.args.get("principal"))
    rate = _f(request.args.get("rate"))
    months = _int(request.args.get("months")) or 0
    extra = _f(request.args.get("extra"))

    sel = None
    if loan_id:
        a = _owned_account(loan_id)
        if a:
            sel = a.id
            principal = a.original_amount or a.balance
            rate = a.interest_rate
            months = a.term_months or 0

    # Frecuencia del pago extra: 1=mensual, 3=4/año, 4=3/año, 12=anual.
    freqs_extra = {"1": "Mensual", "3": "4 veces al año", "4": "3 veces al año", "12": "Anual"}
    freq = request.args.get("freq", "1")
    interval = _int(freq) or 1

    base_sim = finance.amortization(principal, rate, months) if principal and months else None
    extra_sim = (finance.amortization(principal, rate, months, extra, interval)
                 if principal and months and extra else None)
    ahorro = None
    if base_sim and extra_sim and not base_sim.get("no_amortiza") and not extra_sim.get("no_amortiza"):
        ahorro = {
            "meses": base_sim["meses"] - extra_sim["meses"],
            "interes": round(base_sim["total_interes"] - extra_sim["total_interes"], 2),
        }
    return render_template("simulador.html", prestamos=prestamos, sel=sel,
                           principal=principal, rate=rate, months=months, extra=extra,
                           freq=freq, freqs_extra=freqs_extra,
                           base_sim=base_sim, extra_sim=extra_sim, ahorro=ahorro,
                           base=current_user.settings.base_currency)


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
    proy = [{"meses": n, "patrimonio": round(nw["patrimonio"] + prom_neto * n, 2)} for n in (3, 6, 12, 24)]

    # Simulador: ingreso/ahorro extra mensual y su impacto a 12 meses.
    extra = _f(request.args.get("extra"))
    ahorro_12 = round(prom_neto * 12, 2)
    patrimonio_12 = round(nw["patrimonio"] + prom_neto * 12, 2)
    # Deuda proyectada a 12 meses (reduciendo por pagos mínimos, sin intereses; estimación).
    from models import LIABILITY_KINDS, Debt
    pago_min = sum(finance.to_base(a.minimum_payment, a.currency, current_user, rates)
                   for a in Account.query.filter_by(user_id=uid, is_active=True)
                   .filter(Account.kind.in_(LIABILITY_KINDS)).all()) \
        + sum(d.minimum_payment for d in Debt.query.filter_by(user_id=uid).all())
    deuda_hoy = nw["pasivos"]
    deuda_12 = round(max(0.0, deuda_hoy - pago_min * 12), 2)
    # Con el extra:
    ahorro_12_extra = round((prom_neto + extra) * 12, 2)
    patrimonio_12_extra = round(nw["patrimonio"] + (prom_neto + extra) * 12, 2)

    return render_template("proyecciones.html", meses_data=meses_data, prom_ing=prom_ing,
                           prom_gas=prom_gas, prom_neto=prom_neto, nw=nw, proy=proy,
                           ahorro_12=ahorro_12, patrimonio_12=patrimonio_12,
                           deuda_hoy=deuda_hoy, deuda_12=deuda_12, extra=extra,
                           ahorro_12_extra=ahorro_12_extra, patrimonio_12_extra=patrimonio_12_extra,
                           base=current_user.settings.base_currency)
