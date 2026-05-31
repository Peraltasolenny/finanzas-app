# 💰 Mis Finanzas — app personal de presupuesto y seguimiento

App web hecha en Python (Flask) para llevar tus finanzas personales: ingresos, gastos, presupuesto mensual (presupuestado vs. real), tasa de ahorro, metas de ahorro y deudas. Con **login** y datos privados para una sola persona.

## Qué incluye

- **Login seguro** — contraseñas cifradas (hash), sesiones firmadas, registro cerrado tras el primer usuario y bloqueo tras varios intentos fallidos.
- **Transacciones** — registra ingresos y gastos por categoría y fecha.
- **Categorías** — 18 categorías listas (vivienda, supermercado, salario, ahorro, etc.) y puedes crear las tuyas.
- **Presupuesto mensual** — define cuánto piensas gastar por categoría y compáralo con lo real.
- **Tasa de ahorro** y balance neto calculados automáticamente.
- **Metas de ahorro** — con barra de progreso y aportes.
- **Deudas** — saldo, tasa de interés, pago mínimo y día de pago.

## Seguridad de las llaves (importante)

- Ningún secreto está en el código: todo se lee de **variables de entorno**.
- En local usas un archivo **`.env`** (protegido por `.gitignore`, nunca se sube).
- En producción los secretos van en el panel de **Render**.
- Plantilla de variables en **`.env.example`**.

## Cómo empezar

1. **Probarla en tu PC:** sigue **`GUIA_PYTHON.md`**.
2. **Subirla a internet (gratis):** sigue **`GUIA_RENDER.md`** (Render + Neon Postgres).

## Estructura

```
finanzas-app/
├── app.py            # arranque de la app
├── config.py         # configuración (lee variables de entorno)
├── extensions.py     # db, login, csrf
├── models.py         # tablas: usuario, categoría, transacción, presupuesto, meta, deuda
├── auth.py           # registro / login / logout
├── main.py           # dashboard y todas las secciones
├── templates/        # páginas HTML
├── static/           # estilos
├── requirements.txt  # librerías
├── Procfile          # comando de arranque en Render (gunicorn)
├── .env.example      # plantilla de variables (copiar a .env)
└── .gitignore        # evita subir .env y la base local
```

## Stack

Python · Flask · Flask-SQLAlchemy · Flask-Login · Flask-WTF (CSRF) · SQLite (local) / PostgreSQL-Neon (producción) · Bootstrap 5.
