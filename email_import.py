"""Importación de transacciones desde correos de notificación bancaria.

Soporta dos modos:
  1. Gmail OAuth: conecta la cuenta Gmail del usuario, busca correos de bancos
     y parsea los consumos automáticamente.
  2. Pegar texto: el usuario copia el cuerpo del correo y se parsea sin OAuth.

Bancos soportados: BHD León, Banco Popular, Qik, BDI y formato genérico RD.
"""
import json
import os
import re
from datetime import date, datetime

from flask import (Blueprint, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import current_user, login_required

from extensions import db
from models import Account, Category, GmailToken, Transaction

email_bp = Blueprint("email_import", __name__)

# ---------------------------------------------------------------------------
# PARSERS DE CORREO BANCARIO
# ---------------------------------------------------------------------------

_MES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
        "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
        "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
        "noviembre": 11, "diciembre": 12}

_DATE_FMTS = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y",
              "%Y-%m-%d", "%m/%d/%Y")


def _parse_date(text):
    text = (text or "").strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})\s+([a-záéíóú]+)\.?\s+(\d{4})", text, re.I)
    if m:
        mon = _MES.get(m.group(2).lower())
        if mon:
            try:
                return date(int(m.group(3)), mon, int(m.group(1)))
            except ValueError:
                pass
    return None


def _parse_amount(text):
    if not text:
        return None
    s = re.sub(r"(RD\$|US\$|USD|DOP|\$)", "", str(text)).strip().replace(" ", "")
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("()-")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if re.search(r",\d{3}$", s) else s.replace(",", ".")
    try:
        return abs(float(s))
    except ValueError:
        return None


def _currency(text):
    text = (text or "").upper()
    if "US$" in text or "USD" in text:
        return "USD"
    return "RD$"


# Patrón genérico para correos de bancos dominicanos:
# "cargo/consumo/débito de RD$ X,XXX.XX en [comercio] el [fecha]"
_GENERIC_PATTERNS = [
    # BHD León / Qik: "cargo de RD$1,500.00 en FARMACIA el 10/06/2026"
    re.compile(
        r"(?:cargo|consumo|d[eé]bito)\s+de\s+"
        r"((?:RD\$|US\$|USD|DOP|\$)\s*[\d,\.]+)"
        r"(?:.{0,80}?en\s+([A-Z][^\n\r,\.]{2,50}?))?"
        r"(?:.{0,40}?el\s+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}))?",
        re.I | re.S,
    ),
    # Banco Popular: "se realizó un cargo de RD$ 1,500.00"
    re.compile(
        r"se\s+realiz[oó]\s+un\s+(?:cargo|consumo)\s+de\s+"
        r"((?:RD\$|US\$|USD|DOP|\$)\s*[\d,\.]+)"
        r"(?:.{0,80}?en\s+([A-Z][^\n\r,\.]{2,50}?))?"
        r"(?:.{0,40}?(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}))?",
        re.I | re.S,
    ),
    # BDI / genérico: "transacción de RD$500 en..."
    re.compile(
        r"transacci[oó]n\s+de\s+"
        r"((?:RD\$|US\$|USD|DOP|\$)\s*[\d,\.]+)"
        r"(?:.{0,80}?en\s+([A-Z][^\n\r,\.]{2,50}?))?"
        r"(?:.{0,40}?(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}))?",
        re.I | re.S,
    ),
    # Genérico: "consumo por $X.XX" (Qik / apps)
    re.compile(
        r"consumo\s+(?:por|de)\s+"
        r"((?:RD\$|US\$|USD|DOP|\$)\s*[\d,\.]+)"
        r"(?:.{0,80}?(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}))?",
        re.I | re.S,
    ),
]


def parse_bank_email(text):
    """Extrae transacciones de un correo de notificación bancaria.

    Devuelve lista de dicts:
        {tx_date, amount, currency, description, merchant}
    """
    results = []
    for pat in _GENERIC_PATTERNS:
        for m in pat.finditer(text):
            amount_str = m.group(1)
            amount = _parse_amount(amount_str)
            if not amount or amount <= 0:
                continue
            cur = _currency(amount_str)

            # Merchant / comercio
            try:
                merchant = (m.group(2) or "").strip(" .,\n\r").title() or None
            except IndexError:
                merchant = None

            # Fecha
            tx_date = None
            for g in range(2, m.lastindex + 1):
                try:
                    val = (m.group(g) or "").strip()
                    parsed = _parse_date(val)
                    if parsed:
                        tx_date = parsed
                        break
                except IndexError:
                    pass
            if tx_date is None:
                tx_date = date.today()

            desc = merchant or "Consumo bancario"
            results.append({
                "tx_date": tx_date.isoformat(),
                "amount": round(amount, 2),
                "currency": cur,
                "description": desc[:120],
                "merchant": merchant,
            })

    # Deduplica por (amount, date) si el mismo correo matchea varios patrones.
    seen = set()
    unique = []
    for r in results:
        key = (r["amount"], r["tx_date"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


# ---------------------------------------------------------------------------
# GMAIL OAUTH
# ---------------------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_REDIRECT_PATH = "/importar/correo/callback"


def _get_flow(redirect_uri):
    from google_auth_oauthlib.flow import Flow  # type: ignore
    client_config = {
        "web": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "redirect_uris": [redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)
    return flow


def _fernet():
    from cryptography.fernet import Fernet
    import base64, hashlib
    key = hashlib.sha256(
        (os.environ.get("SECRET_KEY") or "dev-key").encode()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _save_token(user_id, creds_json):
    f = _fernet()
    encrypted = f.encrypt(creds_json.encode()).decode()
    tok = GmailToken.query.filter_by(user_id=user_id).first()
    if tok:
        tok.token_json = encrypted
    else:
        db.session.add(GmailToken(user_id=user_id, token_json=encrypted))
    db.session.commit()


def _load_token(user_id):
    tok = GmailToken.query.filter_by(user_id=user_id).first()
    if not tok:
        return None
    try:
        f = _fernet()
        return json.loads(f.decrypt(tok.token_json.encode()).decode())
    except Exception:
        return None


def _gmail_service(user_id):
    """Devuelve un servicio Gmail autenticado, o None si no hay token."""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build  # type: ignore
    except ImportError:
        return None, "Librerías de Google no instaladas."

    token_data = _load_token(user_id)
    if not token_data:
        return None, "No hay cuenta Gmail conectada."

    creds = Credentials(**token_data)
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        _save_token(user_id, creds.to_json())

    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return svc, None


def _fetch_bank_emails(svc, max_results=30):
    """Busca correos de notificación de consumo en Gmail."""
    query = (
        "subject:(cargo OR consumo OR débito OR notificación OR transacción) "
        "newer_than:30d"
    )
    result = svc.users().messages().list(userId="me", q=query,
                                         maxResults=max_results).execute()
    messages = result.get("messages", [])

    emails = []
    for msg_ref in messages:
        msg = svc.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()
        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        subject = headers.get("Subject", "")
        sender = headers.get("From", "")

        body = _extract_body(payload)
        emails.append({
            "id": msg_ref["id"],
            "subject": subject,
            "from": sender,
            "body": body,
            "snippet": msg.get("snippet", ""),
        })
    return emails


def _extract_body(payload):
    """Extrae el texto plano del cuerpo de un mensaje Gmail."""
    import base64 as _b64

    def _decode(data):
        return _b64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    mime = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")
    if body_data:
        return _decode(body_data)
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            d = part.get("body", {}).get("data", "")
            if d:
                return _decode(d)
    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text.strip():
            return text
    return payload.get("snippet", "")


# ---------------------------------------------------------------------------
# RUTAS
# ---------------------------------------------------------------------------

@email_bp.route("/importar/correo", methods=["GET"])
@login_required
def email_import_page():
    connected = GmailToken.query.filter_by(user_id=current_user.id).first() is not None
    accounts = Account.query.filter_by(
        user_id=current_user.id, is_active=True
    ).order_by(Account.name).all()
    categorias = Category.query.filter_by(
        user_id=current_user.id, type="expense", is_active=True
    ).order_by(Category.name).all()
    google_configured = bool(
        os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")
    )
    return render_template(
        "email_import.html",
        connected=connected,
        accounts=accounts,
        categorias=categorias,
        google_configured=google_configured,
    )


@email_bp.route("/importar/correo/conectar")
@login_required
def gmail_connect():
    if not (os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")):
        flash("Configura GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET en el archivo .env.", "danger")
        return redirect(url_for("email_import.email_import_page"))

    redirect_uri = url_for("email_import.gmail_callback", _external=True)
    flow = _get_flow(redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    session["gmail_oauth_state"] = state
    return redirect(auth_url)


@email_bp.route("/importar/correo/callback")
@login_required
def gmail_callback():
    state = session.pop("gmail_oauth_state", None)
    if not state or request.args.get("state") != state:
        flash("Error de seguridad en la autorización de Gmail.", "danger")
        return redirect(url_for("email_import.email_import_page"))

    redirect_uri = url_for("email_import.gmail_callback", _external=True)
    flow = _get_flow(redirect_uri)
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    _save_token(current_user.id, creds.to_json())
    flash("Gmail conectado correctamente.", "success")
    return redirect(url_for("email_import.gmail_fetch"))


@email_bp.route("/importar/correo/desconectar", methods=["POST"])
@login_required
def gmail_disconnect():
    tok = GmailToken.query.filter_by(user_id=current_user.id).first()
    if tok:
        db.session.delete(tok)
        db.session.commit()
    flash("Cuenta Gmail desconectada.", "info")
    return redirect(url_for("email_import.email_import_page"))


@email_bp.route("/importar/correo/buscar")
@login_required
def gmail_fetch():
    svc, err = _gmail_service(current_user.id)
    if err:
        flash(err, "danger")
        return redirect(url_for("email_import.email_import_page"))

    emails = _fetch_bank_emails(svc)
    parsed = []
    for em in emails:
        txs = parse_bank_email(em["body"] or em["snippet"])
        for t in txs:
            t["email_subject"] = em["subject"]
            t["email_from"] = em["from"]
            parsed.append(t)

    accounts = Account.query.filter_by(
        user_id=current_user.id, is_active=True
    ).order_by(Account.name).all()
    categorias = Category.query.filter_by(
        user_id=current_user.id, type="expense", is_active=True
    ).order_by(Category.name).all()
    return render_template(
        "email_import.html",
        connected=True,
        google_configured=True,
        accounts=accounts,
        categorias=categorias,
        parsed=parsed,
    )


@email_bp.route("/importar/correo/pegar", methods=["POST"])
@login_required
def paste_email():
    texto = request.form.get("texto", "")
    parsed = parse_bank_email(texto) if texto.strip() else []
    accounts = Account.query.filter_by(
        user_id=current_user.id, is_active=True
    ).order_by(Account.name).all()
    categorias = Category.query.filter_by(
        user_id=current_user.id, type="expense", is_active=True
    ).order_by(Category.name).all()
    connected = GmailToken.query.filter_by(user_id=current_user.id).first() is not None
    if not parsed:
        flash("No se detectaron transacciones en el texto pegado.", "warning")
    return render_template(
        "email_import.html",
        connected=connected,
        google_configured=bool(
            os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")
        ),
        accounts=accounts,
        categorias=categorias,
        parsed=parsed,
        texto_pegado=texto,
    )


@email_bp.route("/importar/correo/confirmar", methods=["POST"])
@login_required
def confirm_email_import():
    """Crea las transacciones seleccionadas por el usuario."""
    indices = request.form.getlist("sel")
    txs_json = request.form.get("txs_json", "[]")
    try:
        txs_data = json.loads(txs_json)
    except (json.JSONDecodeError, ValueError):
        flash("Error procesando datos.", "danger")
        return redirect(url_for("email_import.email_import_page"))

    creadas = 0
    for i in indices:
        try:
            t = txs_data[int(i)]
        except (IndexError, ValueError):
            continue

        amount = float(t.get("amount", 0))
        if amount <= 0:
            continue

        tx_date_str = t.get("tx_date", date.today().isoformat())
        try:
            tx_date = date.fromisoformat(tx_date_str)
        except ValueError:
            tx_date = date.today()

        acc_id = request.form.get(f"account_{i}")
        cat_id = request.form.get(f"category_{i}")
        description = request.form.get(f"desc_{i}", t.get("description", "Consumo bancario"))

        tx = Transaction(
            user_id=current_user.id,
            type="expense",
            amount=amount,
            currency=t.get("currency", "RD$"),
            description=description[:255],
            merchant=t.get("merchant"),
            tx_date=tx_date,
            account_id=int(acc_id) if acc_id else None,
            category_id=int(cat_id) if cat_id else None,
        )
        db.session.add(tx)
        creadas += 1

        # Ajusta saldo de la cuenta (gasto baja el saldo)
        if acc_id:
            acc = db.session.get(Account, int(acc_id))
            if acc and acc.kind == "tarjeta":
                acc.balance = round(acc.balance + amount, 2)

    if creadas:
        db.session.commit()
        flash(f"{creadas} transacción(es) importadas correctamente.", "success")
    else:
        flash("No se seleccionó ninguna transacción.", "warning")

    return redirect(url_for("main.transactions"))
