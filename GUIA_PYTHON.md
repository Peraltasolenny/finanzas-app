# Guía de Python para novatos — correr la app en tu computadora

Esta guía te lleva paso a paso para probar la app **en tu propia PC** antes de subirla a internet. No necesitas saber programar; solo copiar y pegar comandos.

> Todo lo que escribas que empiece con `$` significa "escríbelo en la terminal y presiona Enter" (no escribas el `$`).

---

## 1. Instalar Python

1. Ve a **https://www.python.org/downloads/** y descarga la última versión (3.12 o superior).
2. Ejecuta el instalador.
   - **MUY IMPORTANTE en Windows:** marca la casilla **"Add Python to PATH"** antes de dar "Install Now".
3. Para confirmar que quedó instalado, abre la terminal:
   - **Windows:** busca "PowerShell" en el menú de inicio.
   - **Mac:** abre la app "Terminal".
   - Escribe:
     ```
     python --version
     ```
     (en Mac puede ser `python3 --version`). Debe mostrar algo como `Python 3.12.x`.

---

## 2. Abrir la carpeta del proyecto en la terminal

La app está en la carpeta `finanzas-app`. Tienes que "entrar" a ella desde la terminal con el comando `cd` (cambiar directorio):

```
cd ruta/hasta/finanzas-app
```

Truco: escribe `cd ` (con espacio) y luego **arrastra la carpeta** `finanzas-app` a la ventana de la terminal; pega la ruta sola.

---

## 3. Crear un "entorno virtual" (caja aislada para la app)

Esto evita que las librerías de la app se mezclen con el resto de tu sistema.

**Windows (PowerShell):**
```
python -m venv venv
venv\Scripts\activate
```

**Mac / Linux:**
```
python3 -m venv venv
source venv/bin/activate
```

Si funcionó, verás `(venv)` al inicio de la línea. Eso significa que el entorno está activo.

---

## 4. Instalar las librerías que necesita la app

```
pip install -r requirements.txt
```

Espera a que termine (descarga varias cosas la primera vez).

---

## 5. Crear tu archivo de secretos (.env)

La app necesita una **clave secreta**. Nunca se escribe dentro del código.

1. Genera una clave aleatoria con este comando:
   ```
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
   Copia el texto largo que aparece.

2. En la carpeta del proyecto verás un archivo llamado **`.env.example`**. Haz una copia y renómbrala a **`.env`**.
   - O por terminal:
     - Windows: `copy .env.example .env`
     - Mac/Linux: `cp .env.example .env`

3. Abre `.env` con el Bloc de notas / TextEdit y pega tu clave:
   ```
   SECRET_KEY=aqui-va-la-clave-larga-que-copiaste
   ```
   Deja `DATABASE_URL=` vacío (en local usará un archivo automáticamente).

> El archivo `.env` **nunca** se sube a GitHub: ya está protegido en `.gitignore`.

---

## 6. ¡Correr la app!

```
python app.py
```

Verás algo como `Running on http://127.0.0.1:5000`.
Abre tu navegador y entra a **http://127.0.0.1:5000**.

- La **primera vez** te pedirá crear tu cuenta (correo + contraseña). Ese será tu único usuario.
- Después podrás registrar ingresos/gastos, definir presupuesto, metas y deudas.

Para **detener** la app: vuelve a la terminal y presiona `Ctrl + C`.

---

## 7. Para volver a usarla otro día

Solo necesitas activar el entorno y correrla otra vez:

```
cd ruta/hasta/finanzas-app
venv\Scripts\activate      (Windows)   |   source venv/bin/activate   (Mac/Linux)
python app.py
```

---

## Problemas comunes

| Síntoma | Solución |
|---|---|
| `python no se reconoce...` (Windows) | Reinstala Python marcando "Add Python to PATH". |
| `pip no se reconoce` | Usa `python -m pip install -r requirements.txt`. |
| La página no abre | Confirma que la terminal dice "Running on..." y usa exactamente `http://127.0.0.1:5000`. |
| Olvidé mi contraseña | Borra el archivo `finanzas.db` y vuelve a registrarte (perderás los datos locales de prueba). |

Cuando ya la probaste y te gusta, pasa a **GUIA_RENDER.md** para subirla a internet y entrar desde el celular.
