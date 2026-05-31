"""Autenticación y gestión de usuarios.

- Registro: solo el PRIMER usuario (que queda como administrador).
- Login: bloquea cuentas archivadas y soporta 2FA (TOTP).
- Panel de usuarios (solo admin): crear, archivar, eliminar y resetear contraseñas.
- Seguridad (cada usuario): cambiar su contraseña y activar/desactivar 2FA.
"""
import base64
import io
import time
from functools import wraps

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, session)
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db
from models import User, seed_default_categories

auth_bp = Blueprint("auth", __name__)

# Protección simple contra fuerza bruta: cuenta intentos fallidos por email.
_failed = {}
MAX_ATTEMPTS = 5
LOCK_SECONDS = 300  # 5 minutos


def _is_locked(email):
    info = _failed.get(email)
    if not info:
        return False
    count, last = info
    return count >= MAX_ATTEMPTS and (time.time() - last) < LOCK_SECONDS


def _register_fail(email):
    count, _ = _failed.get(email, (0, 0))
    _failed[email] = (count + 1, time.time())


def _clear_fail(email):
    _failed.pop(email, None)


def admin_required(f):
    """Restringe una vista al administrador."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            flash("Solo el administrador puede acceder a esta sección.", "danger")
            return redirect(url_for("main.dashboard"))
        return f(*args, **kwargs)
    return wrapper


def _qr_data_uri(uri):
    """Genera un PNG del código QR como data URI embebible en HTML."""
    import qrcode

    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ----------------- registro (solo el primer usuario / admin) -----------------
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if User.query.first() is not None:
        flash("El registro está cerrado. Inicia sesión.", "info")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        if not email or not password:
            flash("Correo y contraseña son obligatorios.", "danger")
        elif password != password2:
            flash("Las contraseñas no coinciden.", "danger")
        elif len(password) < 8:
            flash("La contraseña debe tener al menos 8 caracteres.", "danger")
        else:
            # El primer usuario es el administrador.
            user = User(email=email, name=name or email.split("@")[0], is_admin=True)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            seed_default_categories(user)
            login_user(user)
            flash("¡Cuenta de administrador creada! Ya puedes empezar.", "success")
            return redirect(url_for("main.dashboard"))

    return render_template("register.html")


# ----------------- login (+ 2FA) -----------------
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if User.query.first() is None:
        return redirect(url_for("auth.register"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if _is_locked(email):
            flash("Demasiados intentos. Espera unos minutos e inténtalo de nuevo.", "danger")
            return render_template("login.html")

        user = User.query.filter_by(email=email).first()
        if user and user.is_archived:
            flash("Esta cuenta está archivada. Contacta al administrador.", "danger")
            return render_template("login.html")

        if user and user.check_password(password):
            _clear_fail(email)
            if user.totp_enabled:
                # Paso 2: pedir el código de la app de autenticación.
                session["pre_2fa_user"] = user.id
                session["pre_2fa_next"] = request.args.get("next")
                return redirect(url_for("auth.login_2fa"))
            login_user(user, remember=True)
            return redirect(request.args.get("next") or url_for("main.dashboard"))

        _register_fail(email)
        flash("Correo o contraseña incorrectos.", "danger")

    return render_template("login.html")


@auth_bp.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    uid = session.get("pre_2fa_user")
    if not uid:
        return redirect(url_for("auth.login"))
    user = db.session.get(User, uid)
    if not user:
        session.pop("pre_2fa_user", None)
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        if user.verify_totp(request.form.get("code")):
            next_page = session.pop("pre_2fa_next", None)
            session.pop("pre_2fa_user", None)
            login_user(user, remember=True)
            return redirect(next_page or url_for("main.dashboard"))
        flash("Código inválido. Intenta de nuevo.", "danger")

    return render_template("login_2fa.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada.", "info")
    return redirect(url_for("auth.login"))


# ----------------- panel de usuarios (solo admin) -----------------
@auth_bp.route("/usuarios", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        es_admin = request.form.get("is_admin") == "on"

        if not email or not password:
            flash("Correo y contraseña son obligatorios.", "danger")
        elif len(password) < 8:
            flash("La contraseña debe tener al menos 8 caracteres.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("Ya existe un usuario con ese correo.", "danger")
        else:
            user = User(email=email, name=name or email.split("@")[0], is_admin=es_admin)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            seed_default_categories(user)
            flash(f"Usuario {email} creado.", "success")
        return redirect(url_for("auth.users"))

    usuarios = User.query.order_by(User.is_archived, User.id).all()
    return render_template("users.html", usuarios=usuarios)


@auth_bp.route("/usuarios/<int:user_id>/archivar", methods=["POST"])
@admin_required
def toggle_archive_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("Usuario no encontrado.", "danger")
    elif user.id == current_user.id:
        flash("No puedes archivar tu propia cuenta.", "danger")
    else:
        user.is_archived = not user.is_archived
        db.session.commit()
        flash(f"Usuario {'archivado' if user.is_archived else 'reactivado'}.", "info")
    return redirect(url_for("auth.users"))


@auth_bp.route("/usuarios/<int:user_id>/eliminar", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("Usuario no encontrado.", "danger")
    elif user.id == current_user.id:
        flash("No puedes eliminar tu propia cuenta.", "danger")
    elif user.is_admin and User.query.filter_by(is_admin=True).count() <= 1:
        flash("No puedes eliminar al único administrador.", "danger")
    else:
        # Elimina al usuario y todos sus datos (cascade en las relaciones).
        db.session.delete(user)
        db.session.commit()
        flash("Usuario y sus datos eliminados.", "info")
    return redirect(url_for("auth.users"))


@auth_bp.route("/usuarios/<int:user_id>/reset", methods=["POST"])
@admin_required
def reset_user_password(user_id):
    user = db.session.get(User, user_id)
    new_pw = request.form.get("password", "")
    if not user:
        flash("Usuario no encontrado.", "danger")
    elif len(new_pw) < 8:
        flash("La nueva contraseña debe tener al menos 8 caracteres.", "danger")
    else:
        user.set_password(new_pw)
        # Por seguridad, resetear la contraseña desactiva el 2FA del usuario.
        user.totp_enabled = False
        user.totp_secret = None
        db.session.commit()
        flash(f"Contraseña de {user.email} restablecida. (Su 2FA se desactivó.)", "success")
    return redirect(url_for("auth.users"))


# ----------------- seguridad personal (cambiar clave + 2FA) -----------------
@auth_bp.route("/seguridad")
@login_required
def security():
    return render_template("security.html")


@auth_bp.route("/seguridad/password", methods=["POST"])
@login_required
def change_password():
    actual = request.form.get("current_password", "")
    nueva = request.form.get("new_password", "")
    nueva2 = request.form.get("new_password2", "")
    if not current_user.check_password(actual):
        flash("Tu contraseña actual es incorrecta.", "danger")
    elif len(nueva) < 8:
        flash("La nueva contraseña debe tener al menos 8 caracteres.", "danger")
    elif nueva != nueva2:
        flash("Las contraseñas nuevas no coinciden.", "danger")
    else:
        current_user.set_password(nueva)
        db.session.commit()
        flash("Contraseña actualizada.", "success")
    return redirect(url_for("auth.security"))


@auth_bp.route("/seguridad/2fa/activar", methods=["POST"])
@login_required
def enable_2fa():
    """Genera un secreto y muestra el QR para confirmar la activación."""
    import pyotp

    if current_user.totp_enabled:
        flash("El 2FA ya está activo.", "info")
        return redirect(url_for("auth.security"))
    current_user.totp_secret = pyotp.random_base32()
    db.session.commit()
    qr = _qr_data_uri(current_user.totp_uri())
    return render_template("security.html", setup_2fa=True, qr=qr,
                           secret=current_user.totp_secret)


@auth_bp.route("/seguridad/2fa/confirmar", methods=["POST"])
@login_required
def confirm_2fa():
    if current_user.verify_totp(request.form.get("code")):
        current_user.totp_enabled = True
        db.session.commit()
        flash("¡2FA activado! A partir de ahora se te pedirá un código al entrar.", "success")
    else:
        flash("Código inválido. Vuelve a intentar activar el 2FA.", "danger")
    return redirect(url_for("auth.security"))


@auth_bp.route("/seguridad/2fa/desactivar", methods=["POST"])
@login_required
def disable_2fa():
    if not current_user.check_password(request.form.get("password", "")):
        flash("Contraseña incorrecta. No se desactivó el 2FA.", "danger")
    else:
        current_user.totp_enabled = False
        current_user.totp_secret = None
        db.session.commit()
        flash("2FA desactivado.", "info")
    return redirect(url_for("auth.security"))
