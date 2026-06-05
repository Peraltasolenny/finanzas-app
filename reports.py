"""Cálculo de periodos, filtros y series para el dashboard y reportes."""
import calendar
from datetime import date, timedelta

import finance

MESES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
         "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def period_range(g, desde, hasta, today):
    """Devuelve (inicio, fin, etiqueta) según granularidad o rango personalizado."""
    if (desde or hasta) and g == "custom":
        start = desde or date(today.year, 1, 1)
        end = hasta or today
        return start, end, f"{start.strftime('%d/%m/%Y')} – {end.strftime('%d/%m/%Y')}"
    if g == "day":
        return today, today, today.strftime("%d/%m/%Y")
    if g == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6), f"Semana del {start.strftime('%d/%m')}"
    if g == "quarter":
        q = (today.month - 1) // 3
        start = date(today.year, q * 3 + 1, 1)
        m_end = q * 3 + 3
        return start, date(today.year, m_end, calendar.monthrange(today.year, m_end)[1]), \
            f"T{q + 1} {today.year}"
    if g == "year":
        return date(today.year, 1, 1), date(today.year, 12, 31), str(today.year)
    # mes (por defecto)
    last = calendar.monthrange(today.year, today.month)[1]
    return date(today.year, today.month, 1), date(today.year, today.month, last), \
        f"{MESES[today.month]} {today.year}"


def prev_range(start, end):
    """Periodo inmediatamente anterior, del mismo largo."""
    dias = (end - start).days + 1
    pend = start - timedelta(days=1)
    pstart = pend - timedelta(days=dias - 1)
    return pstart, pend


def filter_txs(query, start, end, currency=None, account_id=None, bank=None,
               category_id=None, text=None):
    """Aplica filtros comunes a un query de Transaction."""
    from models import Transaction, Account
    query = query.filter(Transaction.tx_date >= start, Transaction.tx_date <= end)
    if currency:
        query = query.filter(Transaction.currency == currency)
    if account_id:
        query = query.filter(Transaction.account_id == account_id)
    if category_id:
        query = query.filter(Transaction.category_id == category_id)
    if text:
        like = f"%{text}%"
        query = query.filter(Transaction.description.ilike(like) | Transaction.merchant.ilike(like))
    txs = query.all()
    if bank:  # filtro por banco vía la cuenta asociada (en Python)
        txs = [t for t in txs if t.account and t.account.bank == bank]
    return txs


def totals(user, txs, rates):
    """Suma ingresos, gastos y neto (convertido a moneda base; gastos incluyen el costo)."""
    ing = sum(finance.to_base(t.amount, t.currency, user, rates) for t in txs if t.type == "income")
    gas = sum(finance.tx_value_base(t, user, rates) for t in txs if t.type == "expense")
    return round(ing, 2), round(gas, 2), round(ing - gas, 2)


def trend_series(user, txs, start, end, rates):
    """Serie de ingresos vs gastos agrupada por día/semana/mes según el largo del rango."""
    dias = (end - start).days + 1
    if dias <= 31:
        modo = "day"
    elif dias <= 95:
        modo = "week"
    else:
        modo = "month"

    buckets = {}     # clave -> [orden, label, ingresos, gastos]

    def keyfor(d):
        if modo == "day":
            return d.isoformat(), d.strftime("%d/%m")
        if modo == "week":
            wk = d - timedelta(days=d.weekday())
            return wk.isoformat(), wk.strftime("%d/%m")
        return f"{d.year}-{d.month:02d}", f"{MESES[d.month][:3]} {d.year}"

    for t in txs:
        k, label = keyfor(t.tx_date)
        b = buckets.setdefault(k, [k, label, 0.0, 0.0])
        if t.type == "income":
            b[2] += finance.to_base(t.amount, t.currency, user, rates)
        else:
            b[3] += finance.tx_value_base(t, user, rates)
    ordenado = sorted(buckets.values(), key=lambda x: x[0])
    return {
        "labels": [b[1] for b in ordenado],
        "ingresos": [round(b[2], 2) for b in ordenado],
        "gastos": [round(b[3], 2) for b in ordenado],
    }


def category_breakdown(user, txs, rates, tipo):
    """Desglose por categoría (monto + %) para un tipo (income/expense)."""
    cats = {}
    for t in txs:
        if t.type != tipo:
            continue
        nombre = t.category.name if t.category else "Sin categoría"
        cats[nombre] = cats.get(nombre, 0.0) + finance.tx_value_base(t, user, rates)
    total = sum(cats.values())
    items = sorted(cats.items(), key=lambda x: -x[1])
    return [{"nombre": n, "monto": round(v, 2),
             "pct": round(v / total * 100, 1) if total > 0 else 0.0} for n, v in items], round(total, 2)


def expense_distribution(user, txs, rates):
    """Distribución de gastos por categoría (para gráfico circular)."""
    cats = {}
    for t in txs:
        if t.type != "expense":
            continue
        nombre = t.category.name if t.category else "Sin categoría"
        cats[nombre] = cats.get(nombre, 0.0) + finance.tx_value_base(t, user, rates)
    items = sorted(cats.items(), key=lambda x: -x[1])
    return {"labels": [i[0] for i in items], "valores": [round(i[1], 2) for i in items]}
