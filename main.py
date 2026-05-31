"""Rutas principales: dashboard, transacciones, categorías, presupuesto, metas, deudas e importación."""
from datetime import date, datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy import func, extract

from extensions import db
from models import Category, Transaction, Budget, Goal, Debt
from importer import parse_statement

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
    year, month = _parse_period()
    uid = current_user.id

    def period_filter(q):
        return q.filter(
            Transaction.user_id == uid,
            extract("year", Transaction.tx_date) == year,
            extract("month", Transaction.tx_date) == month,
        )

    ingresos = period_filter(
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0.0))
    ).filter(Transaction.type == "income").scalar() or 0.0

    gastos = period_filter(
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0.0))
    ).filter(Transaction.type == "expense").scalar() or 0.0

    neto = ingresos - gastos
    tasa_ahorro = (neto / ingresos * 100) if ingresos > 0 else 0.0

    # Presupuestado vs real por categoría de gasto
    gasto_por_cat = dict(
        period_filter(
            db.session.query(Transaction.category_id, func.sum(Transaction.amount))
        ).filter(Transaction.type == "expense")
        .group_by(Transaction.category_id).all()
    )

    presupuestos = {
        b.category_id: b.amount
        for b in Budget.query.filter_by(user_id=uid, year=year, month=month).all()
    }

    categorias_gasto = Category.query.filter_by(
        user_id=uid, type="expense", is_active=True
    ).order_by(Category.name).all()

    filas_presupuesto = []
    total_presupuestado = 0.0
    for c in categorias_gasto:
        presup = presupuestos.get(c.id, 0.0)
        real = gasto_por_cat.get(c.id, 0.0)
        total_presupuestado += presup
        if presup == 0 and real == 0:
            continue
        pct = (real / presup * 100) if presup > 0 else None
        filas_presupuesto.append({
            "nombre": c.name, "presupuestado": presup, "real": real,
            "diferencia": presup - real, "pct": pct,
        })

    metas = Goal.query.filter_by(user_id=uid).order_by(Goal.created_at.desc()).all()
    deudas = Debt.query.filter_by(user_id=uid).order_by(Debt.balance.desc()).all()
    total_deuda = sum(d.balance for d in deudas)
    total_pago_min = sum(d.minimum_payment for d in deudas)

    return render_template(
        "dashboard.html",
        year=year, month=month, mes_nombre=MESES[month], meses=MESES,
        ingresos=ingresos, gastos=gastos, neto=neto, tasa_ahorro=tasa_ahorro,
        total_presupuestado=total_presupuestado,
        filas_presupuesto=filas_presupuesto,
        metas=metas, deudas=deudas,
        total_deuda=total_deuda, total_pago_min=total_pago_min,
        years=list(range(date.today().year - 3, date.today().year + 2)),
    )


# ----------------- transacciones -----------------
@main_bp.route("/transacciones", methods=["GET", "POST"])
@login_required
def transactions():
    uid = current_user.id
    if request.method == "POST":
        tipo = request.form.get("type")
        cat_id = request.form.get("category_id") or None
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
                amount=amount, description=desc, tx_date=tx_date,
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

    q = Transaction.query.filter(Transaction.user_id == uid)
    if rango_activo:
        if d_from:
            q = q.filter(Transaction.tx_date >= d_from)
        if d_to:
            q = q.filter(Transaction.tx_date <= d_to)
    else:
        q = q.filter(
            extract("year", Transaction.tx_date) == year,
            extract("month", Transaction.tx_date) == month,
        )
    txs = q.order_by(Transaction.tx_date.desc(), Transaction.id.desc()).all()

    # Totales del conjunto mostrado.
    total_ingresos = sum(t.amount for t in txs if t.type == "income")
    total_gastos = sum(t.amount for t in txs if t.type == "expense")

    categorias = Category.query.filter_by(
        user_id=uid, is_active=True).order_by(Category.type, Category.name).all()

    return render_template(
        "transactions.html", transactions=txs, categorias=categorias,
        year=year, month=month, mes_nombre=MESES[month], meses=MESES,
        hoy=date.today().isoformat(),
        years=list(range(date.today().year - 3, date.today().year + 2)),
        desde=request.args.get("desde", ""), hasta=request.args.get("hasta", ""),
        rango_activo=rango_activo,
        total_ingresos=total_ingresos, total_gastos=total_gastos,
        neto=total_ingresos - total_gastos,
    )


# ----------------- importar estado de cuenta (OCR / CSV / Excel) -----------------
@main_bp.route("/importar", methods=["GET", "POST"])
@login_required
def import_statement():
    uid = current_user.id
    categorias = Category.query.filter_by(
        user_id=uid, is_active=True).order_by(Category.type, Category.name).all()

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
                               categorias=categorias, filename=file.filename)

    return render_template("import.html", review=False, categorias=categorias)


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
        if nombre and tipo in ("income", "expense"):
            db.session.add(Category(user_id=uid, name=nombre, type=tipo))
            db.session.commit()
            flash("Categoría agregada.", "success")
        else:
            flash("Datos de categoría inválidos.", "danger")
        return redirect(url_for("main.categories"))

    ingresos = Category.query.filter_by(user_id=uid, type="income").order_by(Category.name).all()
    gastos = Category.query.filter_by(user_id=uid, type="expense").order_by(Category.name).all()
    return render_template("categories.html", ingresos=ingresos, gastos=gastos)


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
    return render_template(
        "budget.html", categorias=categorias, presupuestos=presupuestos,
        total=total, year=year, month=month, mes_nombre=MESES[month], meses=MESES,
        years=list(range(date.today().year - 3, date.today().year + 2)),
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
            db.session.add(Goal(user_id=uid, name=nombre, target_amount=target,
                                current_amount=current, target_date=target_date))
            db.session.commit()
            flash("Meta creada.", "success")
        else:
            flash("Nombre y monto objetivo son obligatorios.", "danger")
        return redirect(url_for("main.goals"))

    metas = Goal.query.filter_by(user_id=uid).order_by(Goal.created_at.desc()).all()
    return render_template("goals.html", metas=metas)


@main_bp.route("/metas/aportar/<int:goal_id>", methods=["POST"])
@login_required
def contribute_goal(goal_id):
    g = db.session.get(Goal, goal_id)
    if g and g.user_id == current_user.id:
        g.current_amount += _to_float(request.form.get("amount"))
        db.session.commit()
        flash("Aporte registrado en la meta.", "success")
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
