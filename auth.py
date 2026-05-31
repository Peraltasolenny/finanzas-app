"""Rutas de autenticación: registro (solo el primer usuario), login y logout."""
import time

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db
from models import User, seed_default_categories

auth_bp = Blueprint("auth", __name__)

# Protección simple contra fuerza bruta: cuenta intentos fallidos por email.
# (Suficiente para una app personal de un solo usuario.)
_failed = {}
MAX_ATTEMPTS = 5
LOCK_SECONDS = 300  # 5 minutos


def _is_locked(email):
    info = _failed.get(email)
    if not info:
        return False
    count, last = info
    if count >= MAX_ATTEMPTS and (time.time() - last) < LOCK_SECONDS:
        return True
    return False


def _register_fail(email):
    count, _ = _failed.get(email, (0, 0))
    _failed[email] = (count + 1, time.time())


def _clear_fail(email):
    _failed.pop(email, None)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    # Registro abierto SOLO si todavía no existe ningún usuario.
    # Así la app queda privada para ti, sin que otros puedan crear cuentas.
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
            user = User(email=email, name=name or email.split("@")[0])
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            seed_default_categories(user)
            login_user(user)
            flash("¡Cuenta creada! Ya puedes empezar a registrar tus finanzas.", "success")
            return redirect(url_for("main.dashboard"))

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    # Si no hay usuarios, manda al registro.
    if User.query.first() is None:
        return redirect(url_for("auth.register"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if _is_locked(email):
            flash("Demasiados intentos. Espera unos minutos e inténtalo de nuevo.", "danger")
            return render_template("login.html")

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            _clear_fail(email)
            login_user(user, remember=True)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("main.dashboard"))
        else:
            _register_fail(email)
            flash("Correo o contraseña incorrectos.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada.", "info")
    return redirect(url_for("auth.login"))
