# Guía de Render para novatos — subir la app a internet (gratis)

Al terminar esta guía tendrás una **dirección web** (ej. `https://mis-finanzas.onrender.com`) para entrar desde tu celular o cualquier computadora, con tu usuario y contraseña.

Usaremos **dos servicios gratis**:
- **Render** → donde "vive" y corre la app.
- **Neon** → la base de datos donde se guardan tus datos de verdad.

> ⚠️ **Por qué Neon y no la base de datos de Render:** el plan gratis de Render borra los archivos cada vez que la app se reinicia, y su base de datos gratis **expira a los 30 días**. Neon es gratis, **no expira** y conserva tus datos. Por eso la conectamos.

---

## PARTE A — Subir tu código a GitHub

(Ya tienes GitHub conectado, así que esto es rápido.)

1. Crea un **repositorio nuevo y privado** en GitHub (botón "New" → marca **Private**). No le agregues README.
2. Sube la carpeta `finanzas-app` a ese repositorio.
   - Si usas GitHub Desktop: arrastra la carpeta, escribe un mensaje y dale "Commit" → "Push".
   - Antes de subir, confirma que **NO** aparece el archivo `.env` en la lista (debe estar oculto; ya está protegido). Sí deben subir: `app.py`, `requirements.txt`, `Procfile`, las carpetas `templates/` y `static/`, etc.

> Regla de oro: el `.env` **nunca** se sube. Las llaves secretas se ponen directo en Render (Parte C).

---

## PARTE B — Crear la base de datos en Neon (gratis)

1. Entra a **https://neon.tech** y crea una cuenta (puedes usar tu cuenta de GitHub).
2. Crea un proyecto nuevo (cualquier nombre, ej. "finanzas"). Elige la región más cercana.
3. Cuando lo cree, busca la sección **"Connection string"** (cadena de conexión).
4. Copia esa cadena. Se ve así:
   ```
   postgresql://usuario:contraseña@ep-xxxx.neon.tech/neondb?sslmode=require
   ```
   Guárdala un momento; la usarás en el paso C.5.

---

## PARTE C — Crear el servicio en Render

1. Entra a **https://render.com** y crea una cuenta (puedes entrar con GitHub).
2. En el panel, haz clic en **New +** → **Web Service**.
3. Conecta tu cuenta de GitHub y **elige el repositorio** que creaste en la Parte A.
4. Render te mostrará un formulario. Llénalo así:
   - **Name:** `mis-finanzas` (será parte de tu dirección web).
   - **Region:** la más cercana a ti.
   - **Branch:** `main`.
   - **Runtime / Language:** Python 3.
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Instance Type:** **Free**.

5. Antes de crear, baja a **"Environment Variables"** (o "Advanced") y agrega estas variables —**aquí van los secretos, no en el código**:

   | Key (nombre) | Value (valor) |
   |---|---|
   | `SECRET_KEY` | una clave larga aleatoria (genérala abajo 👇) |
   | `DATABASE_URL` | la cadena de conexión de Neon (Parte B.4) |
   | `FLASK_ENV` | `production` |
   | `CURRENCY` | `RD$` (o lo que prefieras) |

   Para generar el `SECRET_KEY`, en tu terminal local corre:
   ```
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
   y pega el resultado.

6. Haz clic en **Create Web Service**. Render empezará a construir y desplegar (tarda 2–4 minutos la primera vez). Cuando veas "Live" o "Your service is live", está listo.

7. Arriba aparece tu dirección web (ej. `https://mis-finanzas.onrender.com`). Ábrela:
   - La primera vez crea tu cuenta (correo + contraseña). ¡Ya puedes usarla desde el celular!

---

## Cómo actualizar la app después

Cada vez que cambies algo en el código y lo subas a GitHub (push a la rama `main`), **Render la vuelve a desplegar solo**. No tienes que hacer nada más.

---

## Cosas que debes saber del plan gratis

- **Se "duerme" tras 15 minutos sin uso.** La primera vez que entres después de un rato tardará ~30–60 segundos en despertar. Es normal. (Con el plan de ~7 USD/mes queda siempre encendida.)
- **Tus datos están a salvo** porque viven en Neon, no en Render.
- **HTTPS (candado de seguridad):** Render lo pone automático. Tu conexión va cifrada.

---

## Mantenerla funcionando (revisión rápida)

- **Respaldo de datos:** Neon hace copias automáticas (Point-in-Time Restore) en su plan gratis. Aun así, cada cierto tiempo entra a Neon y verifica que tu proyecto siga activo.
- **Errores:** si algo falla, en Render entra a tu servicio → pestaña **Logs**; ahí se ve el mensaje de error.
- **Seguridad:** mantén el repositorio en **privado** y nunca pegues tus llaves (`SECRET_KEY`, `DATABASE_URL`) en chats, correos ni en el código.
- **Actualizaciones:** cada algunos meses conviene actualizar las versiones en `requirements.txt`. Avísame y te ayudo.

---

## Si algo sale mal

| Problema | Qué revisar |
|---|---|
| "Application failed to respond" | Que el **Start Command** sea exactamente `gunicorn app:app`. |
| Error de base de datos | Que `DATABASE_URL` esté bien pegada y completa (incluye `?sslmode=require`). |
| Build falla | Mira los **Logs** del build; suele ser una línea de `requirements.txt`. Mándame el error. |
| La página tarda mucho la primera vez | Es el "despertar" del plan gratis. Espera ~1 minuto. |
