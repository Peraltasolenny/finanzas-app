"""Rutas principales: dashboard, transacciones, categorías, presupuesto, metas, deudas e importación."""
from datetime import date, datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy import func, extract

from extensions import db
from models import Category, Transaction, Budget, Goal, Debt
from importer import parse_statement
import finance
import reports

main_bp = Blueprint("main", __name__)


# ----------------- utilidades -----------------
def _parse_period():
    """Lee year/month de la query string; por defecto el mes actual."""
    today = date.today()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    if not 1 <= month <= 12:
        month = today.month
    return year, month


def _parse_iso_date(value):
    """Convierte 'YYYY-MM-DD' a date, o None si está vacío/inválido."""
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _int_or(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


MESES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
         "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


# ----------------- dashboard -----------------
@main_bp.route("/")
@login_required
def dashboard():
    uid = current_user.id
    today = date.today()
    finance.generate_due_transactions(current_user)

    # Filtros: granularidad, rango personalizado, moneda, comparación.
    g = request.args.get("g", "month")
    desde = _parse_iso_date(request.args.get("desde"))
    hasta = _parse_iso_date(request.args.get("hasta"))
    cur = request.args.get("cur") or None
    comparar = request.args.get("cmp") == "1"

    start, end, periodo_label = reports.period_range(g, desde, hasta, today)
    rates = finance.get_rates(current_user)
    base = current_user.settings.base_currency

    from models import Account, LIABILITY_KINDS
    txs = reports.filter_txs(
        Transaction.query.filter(Transaction.user_id == uid), start, end, currency=cur)
    ingresos, gastos, neto = reports.totals(current_user, txs, rates)
    tasa_ahorro = (neto / ingresos * 100) if ingresos > 0 else 0.0

    comp = None
    if comparar:
        pstart, pend = reports.prev_range(start, end)
        ptxs = reports.filter_txs(
            Transaction.query.filter(Transaction.user_id == uid), pstart, pend, currency=cur)
        ping, pgas, pneto = reports.totals(current_user, ptxs, rates)
        comp = {"ingresos": ping, "gastos": pgas, "neto": pneto,
                "label": f"{pstart.strftime('%d/%m')} – {pend.strftime('%d/%m')}"}

    # Monto en inversiones (certificados, corretaje, inversión).
    inv_kinds = ["inversion", "certificado", "corretaje"]
    inversiones_total = sum(
        finance.to_base(a.balance, a.currency, current_user, rates)
        for a in Account.query.filter_by(user_id=uid, is_active=True)
        .filter(Account.kind.in_(inv_kinds)).all())

    # Avance total de metas (%).
    metas = Goal.query.filter_by(user_id=uid).order_by(Goal.priority, Goal.created_at.desc()).all()
    tgt = sum(m.target_amount for m in metas)
    cur_tot = sum(m.current_amount for m in metas)
    avance_metas = round(cur_tot / tgt * 100, 1) if tgt > 0 else 0.0

    # Deudas (pasivos + heredadas).
    pasivos = Account.query.filter_by(user_id=uid, is_active=True).filter(
        Account.kind.in_(LIABILITY_KINDS)).all()
    deudas = Debt.query.filter_by(user_id=uid).all()
    total_deuda = sum(d.balance for d in deudas) + sum(a.balance for a in pasivos)
    total_pago_min = sum(d.minimum_payment for d in deudas) + sum(a.minimum_payment for a in pasivos)

    # Gráficos.
    distribucion_pie = reports.expense_distribution(current_user, txs, rates)
    tendencia = reports.trend_series(current_user, txs, start, end, rates)
    salud = finance.health_breakdown(current_user, [t for t in txs if t.type == "expense"], rates)

    # Presupuesto vs real (referido al mes de fin del rango).
    presupuestos = {b.category_id: b.amount for b in Budget.query.filter_by(
        user_id=uid, year=end.year, month=end.month).all()}
    gasto_por_cat = {}
    for t in txs:
        if t.type == "expense":
            gasto_por_cat[t.category_id] = gasto_por_cat.get(t.category_id, 0.0) + \
                finance.to_base(t.amount, t.currency, current_user, rates)
    filas_presupuesto = []
    for c in Category.query.filter_by(user_id=uid, type="expense", is_active=True).order_by(Category.name).all():
        presup = presupuestos.get(c.id, 0.0)
        real = gasto_por_cat.get(c.id, 0.0)
        if presup == 0 and real == 0:
            continue
        filas_presupuesto.append({"nombre": c.name, "presupuestado": presup, "real": real,
                                  "pct": (real / presup * 100) if presup > 0 else None})

    return render_template(
        "dashboard.html", g=g, periodo_label=periodo_label,
        desde=start.isoformat(), hasta=end.isoformat(), cur=cur, comparar=comparar, comp=comp,
        base=base, monedas=reports_monedas(current_user),
        ingresos=ingresos, gastos=gastos, neto=neto, tasa_ahorro=tasa_ahorro,
        inversiones_total=inversiones_total, avance_metas=avance_metas,
        metas=metas, deudas=deudas, pasivos=pasivos,
        total_deuda=total_deuda, total_pago_min=total_pago_min,
        distribucion_pie=distribucion_pie, tendencia=tendencia, salud=salud,
        filas_presupuesto=filas_presupuesto,
    )


def reports_monedas(user):
    """Lista de monedas del usuario (base + tasas) para el filtro."""
    from models import ExchangeRate
    ms = [user.settings.base_currency]
    for r in ExchangeRate.query.filter_by(user_id=user.id).order_by(ExchangeRate.code).all():
        if r.code not in ms:
            ms.append(r.code)
    return ms


# ----------------- transacciones -----------------
@main_bp.route("/transacciones", methods=["GET", "POST"])
@login_required
def transactions():
    uid = current_user.id
    if request.method == "POST":
        tipo = request.form.get("type")
        cat_id = request.form.get("category_id") or None
        acc_id = request.form.get("account_id") or None
        currency = request.form.get("currency", "").strip() or None
        bucket = request.form.get("bucket") or None
        amount = _to_float(request.form.get("amount"))
        desc = request.form.get("description", "").strip()
        fecha_str = request.form.get("tx_date")
        try:
            tx_date = datetime.strptime(fecha_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            tx_date = date.today()

        if tipo not in ("income", "expense"):
            flash("Tipo de transacción inválido.", "danger")
        elif amount <= 0:
            flash("El monto debe ser mayor que cero.", "danger")
        else:
            db.session.add(Transaction(
                user_id=uid, type=tipo, category_id=int(cat_id) if cat_id else None,
                account_id=int(acc_id) if acc_id else None, currency=currency,
                bucket=bucket if bucket in ("need", "want", "invest") else None,
                amount=amount, description=desc, tx_date=tx_date,
                merchant=request.form.get("merchant", "").strip() or None,
                fee=_to_float(request.form.get("fee")),
            ))
            db.session.commit()
            flash("Transacción registrada.", "success")
        return redirect(url_for("main.transactions",
                                year=tx_date.year, month=tx_date.month))

    year, month = _parse_period()

    # Filtro opcional por rango de fechas (tiene prioridad sobre el mes).
    d_from = _parse_iso_date(request.args.get("desde"))
    d_to = _parse_iso_date(request.args.get("hasta"))
    rango_activo = bool(d_from or d_to)

    # Filtros adicionales.
    f_cur = request.args.get("cur") or None
    f_acc = _int_or(request.args.get("cuenta"), None)
    f_cat = _int_or(request.args.get("categoria"), None)
    f_bank = request.args.get("banco") or None
    f_text = request.args.get("q") or None
    filtros_activos = rango_activo or any([f_cur, f_acc, f_cat, f_bank, f_text])

    q = Transaction.query.filter(Transaction.user_id == uid)
    if not rango_activo:
        q = q.filter(extract("year", Transaction.tx_date) == year,
                     extract("month", Transaction.tx_date) == month)
        d_from = d_from or date(year, month, 1)
        d_to = d_to or date(year, month, 28)
    q = reports.filter_txs(q, d_from or date(year, month, 1), d_to or date.today(),
                           currency=f_cur, account_id=f_acc, bank=f_bank,
                           category_id=f_cat, text=f_text)
    txs = sorted(q, key=lambda t: (t.tx_date, t.id), reverse=True)

    # Totales del conjunto mostrado (convertidos a base).
    rates = finance.get_rates(current_user)
    total_ingresos = sum(finance.to_base(t.amount, t.currency, current_user, rates)
                         for t in txs if t.type == "income")
    total_gastos = sum(finance.to_base(t.amount, t.currency, current_user, rates)
                       for t in txs if t.type == "expense")

    categorias = Category.query.filter_by(
        user_id=uid, is_active=True).order_by(Category.type, Category.name).all()
    from models import Account, AccountShare
    accounts = Account.query.filter_by(user_id=uid, is_active=True).order_by(Account.name).all()
    for sh in AccountShare.query.filter_by(user_id=uid, can_register=True).all():
        if sh.account and sh.account.is_active:
            accounts.append(sh.account)
    bancos = sorted({a.bank for a in accounts if a.bank})
    distribucion_pie = reports.expense_distribution(current_user, txs, rates)

    return render_template(
        "transactions.html", transactions=txs, categorias=categorias, accounts=accounts,
        bancos=bancos, base=current_user.settings.base_currency,
        monedas=reports_monedas(current_user), distribucion_pie=distribucion_pie,
        year=year, month=month, mes_nombre=MESES[month], meses=MESES,
        hoy=date.today().isoformat(),
        years=list(range(date.today().year - 3, date.today().year + 2)),
        desde=request.args.get("desde", ""), hasta=request.args.get("hasta", ""),
        rango_activo=rango_activo, filtros_activos=filtros_activos,
        f_cur=f_cur or "", f_acc=f_acc, f_cat=f_cat, f_bank=f_bank or "", f_text=f_text or "",
        total_ingresos=total_ingresos, total_gastos=total_gastos,
        neto=total_ingresos - total_gastos,
    )


@main_bp.route("/transacciones/editar/<int:tx_id>", methods=["GET", "POST"])
@login_required
def edit_transaction(tx_id):
    """Edita una transacción puntual. Si vino de una regla recurrente, editarla
    NO afecta la regla ni las demás ocurrencias."""
    uid = current_user.id
    tx = db.session.get(Transaction, tx_id)
    if not tx or tx.user_id != uid:
        flash("Transacción no encontrada.", "danger")
        return redirect(url_for("main.transactions"))

    if request.method == "POST":
        tx.type = request.form.get("type") if request.form.get("type") in ("income", "expense") else tx.type
        cat_id = request.form.get("category_id") or None
        acc_id = request.form.get("account_id") or None
        tx.category_id = int(cat_id) if cat_id else None
        tx.account_id = int(acc_id) if acc_id else None
        tx.currency = request.form.get("currency", "").strip() or None
        b = request.form.get("bucket") or None
        tx.bucket = b if b in ("need", "want", "invest") else None
        tx.amount = _to_float(request.form.get("amount"))
        tx.description = request.form.get("description", "").strip()
        tx.merchant = request.form.get("merchant", "").strip() or None
        tx.fee = _to_float(request.form.get("fee"))
        d = _parse_iso_date(request.form.get("tx_date"))
        if d:
            tx.tx_date = d
        db.session.commit()
        flash("Transacción actualizada.", "success")
        return redirect(url_for("main.transactions", year=tx.tx_date.year, month=tx.tx_date.month))

    from models import Account
    categorias = Category.query.filter_by(user_id=uid, is_active=True).order_by(
        Category.type, Category.name).all()
    accounts = Account.query.filter_by(user_id=uid, is_active=True).order_by(Account.name).all()
    return render_template("transaction_edit.html", tx=tx, categorias=categorias,
                           accounts=accounts, base=current_user.settings.base_currency)


# ----------------- importar estado de cuenta (OCR / CSV / Excel) -----------------
@main_bp.route("/importar", methods=["GET", "POST"])
@login_required
def import_statement():
    uid = current_user.id
    categorias = Category.query.filter_by(
        user_id=uid, is_active=True).order_by(Category.type, Category.name).all()
    from models import Account
    accounts = Account.query.filter_by(user_id=uid, is_active=True).order_by(
        Account.name).all()
    base = current_user.settings.base_currency

    if request.method == "POST":
        file = request.files.get("statement")
        if not file or not file.filename:
            flash("Selecciona un archivo (PDF, CSV o Excel).", "danger")
            return redirect(url_for("main.import_statement"))

        rows, error = parse_statement(file.stream, file.filename)
        if error:
            flash(error, "danger")
            return redirect(url_for("main.import_statement"))

        flash(f"Se detectaron {len(rows)} movimientos. Revísalos y confirma cuáles importar.", "info")
        return render_template("import.html", review=True, rows=rows,
                               categorias=categorias, accounts=accounts, base=base,
                               # cuenta preseleccionada si la subieron en el paso 1
                               sel_account=request.form.get("account_id", ""),
                               filename=file.filename)

    return render_template("import.html", review=False, categorias=categorias,
                           accounts=accounts, base=base)


@main_bp.route("/importar/confirmar", methods=["POST"])
@login_required
def import_confirm():
    uid = current_user.id
    fechas = request.form.getlist("tx_date")
    descripciones = request.form.getlist("description")
    montos = request.form.getlist("amount")
    tipos = request.form.getlist("type")
    cats = request.form.getlist("category_id")
    incluir = set(request.form.getlist("include"))  # índices de filas marcadas

    # Cuenta/banco y moneda que se aplican a todos los movimientos importados.
    acc_id = request.form.get("account_id") or None
    account_id = int(acc_id) if acc_id else None
    currency = request.form.get("currency", "").strip() or None

    count = 0
    for i in range(len(fechas)):
        if str(i) not in incluir:
            continue
        amount = _to_float(montos[i] if i < len(montos) else 0)
        if amount <= 0:
            continue
        tipo = tipos[i] if i < len(tipos) and tipos[i] in ("income", "expense") else "expense"
        cat = cats[i] if i < len(cats) and cats[i] else None
        desc = (descripciones[i] if i < len(descripciones) else "")[:255]
        db.session.add(Transaction(
            user_id=uid, type=tipo,
            category_id=int(cat) if cat else None,
            account_id=account_id, currency=currency,
            amount=amount, description=desc,
            tx_date=_parse_iso_date(fechas[i]) or date.today(),
        ))
        count += 1
    db.session.commit()
    flash(f"{count} transacciones importadas." if count else "No se importó ninguna transacción.",
          "success" if count else "info")
    return redirect(url_for("main.transactions"))


@main_bp.route("/transacciones/eliminar/<int:tx_id>", methods=["POST"])
@login_required
def delete_transaction(tx_id):
    tx = db.session.get(Transaction, tx_id)
    if tx and tx.user_id == current_user.id:
        db.session.delete(tx)
        db.session.commit()
        flash("Transacción eliminada.", "info")
    return redirect(request.referrer or url_for("main.transactions"))


# ----------------- categorías -----------------
@main_bp.route("/categorias", methods=["GET", "POST"])
@login_required
def categories():
    uid = current_user.id
    if request.method == "POST":
        nombre = request.form.get("name", "").strip()
        tipo = request.form.get("type")
        parent_id = _int_or(request.form.get("parent_id"), None)
        if nombre and tipo in ("income", "expense"):
            db.session.add(Category(user_id=uid, name=nombre, type=tipo, parent_id=parent_id))
            db.session.commit()
            flash("Categoría agregada.", "success")
        else:
            flash("Datos de categoría inválidos.", "danger")
        return redirect(url_for("main.categories"))

    ingresos = Category.query.filter_by(user_id=uid, type="income").order_by(Category.name).all()
    gastos = Category.query.filter_by(user_id=uid, type="expense").order_by(Category.name).all()
    # Posibles categorías padre (las que no son ya subcategorías).
    padres = Category.query.filter_by(user_id=uid, parent_id=None).order_by(
        Category.type, Category.name).all()
    return render_template("categories.html", ingresos=ingresos, gastos=gastos, padres=padres)


@main_bp.route("/categorias/toggle/<int:cat_id>", methods=["POST"])
@login_required
def toggle_category(cat_id):
    c = db.session.get(Category, cat_id)
    if c and c.user_id == current_user.id:
        c.is_active = not c.is_active
        db.session.commit()
    return redirect(url_for("main.categories"))


# ----------------- presupuesto -----------------
@main_bp.route("/presupuesto", methods=["GET", "POST"])
@login_required
def budget():
    uid = current_user.id
    year, month = _parse_period()

    if request.method == "POST":
        for c in Category.query.filter_by(user_id=uid, type="expense", is_active=True).all():
            field = f"cat_{c.id}"
            if field in request.form:
                monto = _to_float(request.form.get(field))
                b = Budget.query.filter_by(
                    user_id=uid, category_id=c.id, year=year, month=month).first()
                if b is None:
                    b = Budget(user_id=uid, category_id=c.id, year=year, month=month)
                    db.session.add(b)
                b.amount = monto
        db.session.commit()
        flash("Presupuesto guardado.", "success")
        return redirect(url_for("main.budget", year=year, month=month))

    categorias = Category.query.filter_by(
        user_id=uid, type="expense", is_active=True).order_by(Category.name).all()
    presupuestos = {
        b.category_id: b.amount
        for b in Budget.query.filter_by(user_id=uid, year=year, month=month).all()
    }
    total = sum(presupuestos.values())

    # Histórico: presupuestado vs. gastado de los últimos 6 meses (hasta el seleccionado).
    rates = finance.get_rates(current_user)
    historico = []
    for i in range(5, -1, -1):
        y = year + (month - 1 - i) // 12
        m = (month - 1 - i) % 12 + 1
        presup = db.session.query(func.coalesce(func.sum(Budget.amount), 0.0)).filter_by(
            user_id=uid, year=y, month=m).scalar() or 0.0
        txs = Transaction.query.filter(
            Transaction.user_id == uid, Transaction.type == "expense",
            extract("year", Transaction.tx_date) == y,
            extract("month", Transaction.tx_date) == m).all()
        gastado = sum(finance.to_base(t.amount, t.currency, current_user, rates) for t in txs)
        pct = (gastado / presup * 100) if presup > 0 else None
        historico.append({"label": f"{MESES[m][:3]} {y}", "presupuestado": presup,
                          "gastado": gastado, "pct": pct})

    return render_template(
        "budget.html", categorias=categorias, presupuestos=presupuestos,
        total=total, year=year, month=month, mes_nombre=MESES[month], meses=MESES,
        years=list(range(date.today().year - 3, date.today().year + 2)),
        historico=historico,
    )


# ----------------- metas de ahorro -----------------
@main_bp.route("/metas", methods=["GET", "POST"])
@login_required
def goals():
    uid = current_user.id
    if request.method == "POST":
        nombre = request.form.get("name", "").strip()
        target = _to_float(request.form.get("target_amount"))
        current = _to_float(request.form.get("current_amount"))
        fecha_str = request.form.get("target_date")
        try:
            target_date = datetime.strptime(fecha_str, "%Y-%m-%d").date() if fecha_str else None
        except ValueError:
            target_date = None
        if nombre and target > 0:
            db.session.add(Goal(
                user_id=uid, name=nombre, target_amount=target, current_amount=current,
                target_date=target_date,
                description=request.form.get("description", "").strip() or None,
                priority=_int_or(request.form.get("priority"), 2),
                currency=request.form.get("currency", "").strip() or None,
                account_id=_int_or(request.form.get("account_id"), None),
                category_id=_int_or(request.form.get("category_id"), None),
                is_shared=request.form.get("is_shared") == "on",
            ))
            db.session.commit()
            flash("Meta creada.", "success")
        else:
            flash("Nombre y monto objetivo son obligatorios.", "danger")
        return redirect(url_for("main.goals"))

    from models import Account
    metas = Goal.query.filter_by(user_id=uid).order_by(
        Goal.priority, Goal.created_at.desc()).all()
    categorias = Category.query.filter_by(user_id=uid, is_active=True).order_by(Category.name).all()
    accounts = Account.query.filter_by(user_id=uid, is_active=True).order_by(Account.name).all()
    return render_template("goals.html", metas=metas, categorias=categorias,
                           accounts=accounts, base=current_user.settings.base_currency)


@main_bp.route("/metas/editar/<int:goal_id>", methods=["GET", "POST"])
@login_required
def edit_goal(goal_id):
    uid = current_user.id
    g = db.session.get(Goal, goal_id)
    if not g or g.user_id != uid:
        flash("Meta no encontrada.", "danger")
        return redirect(url_for("main.goals"))

    if request.method == "POST":
        nombre = request.form.get("name", "").strip()
        target = _to_float(request.form.get("target_amount"))
        if not nombre or target <= 0:
            flash("Nombre y monto objetivo son obligatorios.", "danger")
            return redirect(url_for("main.edit_goal", goal_id=goal_id))
        g.name = nombre
        g.description = request.form.get("description", "").strip() or None
        g.target_amount = target
        g.current_amount = _to_float(request.form.get("current_amount"))
        g.currency = request.form.get("currency", "").strip() or None
        g.target_date = _parse_iso_date(request.form.get("target_date"))
        g.priority = _int_or(request.form.get("priority"), 2)
        g.account_id = _int_or(request.form.get("account_id"), None)
        g.category_id = _int_or(request.form.get("category_id"), None)
        g.is_shared = request.form.get("is_shared") == "on"
        db.session.commit()
        flash("Meta actualizada.", "success")
        return redirect(url_for("main.goals"))

    from models import Account
    categorias = Category.query.filter_by(user_id=uid, is_active=True).order_by(Category.name).all()
    accounts = Account.query.filter_by(user_id=uid, is_active=True).order_by(Account.name).all()
    return render_template("goal_edit.html", g=g, categorias=categorias,
                           accounts=accounts, base=current_user.settings.base_currency)


@main_bp.route("/metas/aportar/<int:goal_id>", methods=["POST"])
@login_required
def contribute_goal(goal_id):
    g = db.session.get(Goal, goal_id)
    monto = _to_float(request.form.get("amount"))
    if g and g.user_id == current_user.id and monto > 0:
        g.current_amount += monto

        # Refleja el aporte como transacción (gasto destinado a inversión/ahorro).
        cat = g.category or Category.query.filter_by(
            user_id=current_user.id, name="Ahorro e inversión", type="expense").first()
        if cat is None:
            cat = Category(user_id=current_user.id, name="Ahorro e inversión",
                           type="expense", bucket="invest")
            db.session.add(cat)
            db.session.flush()
        db.session.add(Transaction(
            user_id=current_user.id, type="expense", category_id=cat.id,
            account_id=g.account_id, amount=monto, currency=g.currency,
            description=f"Aporte a meta: {g.name}", tx_date=date.today(), bucket="invest",
        ))
        db.session.commit()
        flash("Aporte registrado en la meta y reflejado en tus transacciones.", "success")
    return redirect(url_for("main.goals"))


@main_bp.route("/metas/eliminar/<int:goal_id>", methods=["POST"])
@login_required
def delete_goal(goal_id):
    g = db.session.get(Goal, goal_id)
    if g and g.user_id == current_user.id:
        db.session.delete(g)
        db.session.commit()
        flash("Meta eliminada.", "info")
    return redirect(url_for("main.goals"))


# ----------------- deudas -----------------
@main_bp.route("/deudas", methods=["GET", "POST"])
@login_required
def debts():
    uid = current_user.id
    if request.method == "POST":
        nombre = request.form.get("name", "").strip()
        balance = _to_float(request.form.get("balance"))
        rate = _to_float(request.form.get("interest_rate"))
        minimo = _to_float(request.form.get("minimum_payment"))
        try:
            due_day = int(request.form.get("due_day")) if request.form.get("due_day") else None
        except ValueError:
            due_day = None
        if nombre:
            db.session.add(Debt(user_id=uid, name=nombre, balance=balance,
                                interest_rate=rate, minimum_payment=minimo, due_day=due_day))
            db.session.commit()
            flash("Deuda registrada.", "success")
        else:
            flash("El nombre de la deuda es obligatorio.", "danger")
        return redirect(url_for("main.debts"))

    deudas = Debt.query.filter_by(user_id=uid).order_by(Debt.interest_rate.desc()).all()
    total = sum(d.balance for d in deudas)
    total_min = sum(d.minimum_payment for d in deudas)
    return render_template("debts.html", deudas=deudas, total=total, total_min=total_min)


@main_bp.route("/deudas/pagar/<int:debt_id>", methods=["POST"])
@login_required
def pay_debt(debt_id):
    d = db.session.get(Debt, debt_id)
    if d and d.user_id == current_user.id:
        d.balance = max(0.0, d.balance - _to_float(request.form.get("amount")))
        db.session.commit()
        flash("Pago aplicado a la deuda.", "success")
    return redirect(url_for("main.debts"))


@main_bp.route("/deudas/eliminar/<int:debt_id>", methods=["POST"])
@login_required
def delete_debt(debt_id):
    d = db.session.get(Debt, debt_id)
    if d and d.user_id == current_user.id:
        db.session.delete(d)
        db.session.commit()
        flash("Deuda eliminada.", "info")
    return redirect(url_for("main.debts"))
