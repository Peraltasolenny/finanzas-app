# Mis Finanzas — Tema Violeta (claro / oscuro)

Rediseño visual **listo para tu repo Flask**. No cambia ninguna ruta, modelo ni
variable: solo reemplaza los templates y el CSS. Funciona con tu Bootstrap 5.3
actual usando su modo oscuro nativo (`data-bs-theme`).

## Qué incluye
```
templates/
  base.html          ← navbar violeta + botón de tema (☀/🌙) + fuentes
  dashboard.html     ← KPIs rediseñados (Ingresos / Gastos / Balance / Tasa de ahorro)
  transactions.html
  budget.html
  goals.html
  debts.html
  categories.html
  login.html         ← tarjeta de acceso violeta
  register.html
  _cat_row.html
  _period.html
static/
  style.css          ← sistema de diseño completo (paleta violeta, claro y oscuro)
preview.html         ← vista previa estática (NO va al repo; solo para verla)
```

## Cómo integrarlo (2 pasos)
1. Copia la carpeta `templates/` y el archivo `static/style.css` dentro de tu
   proyecto, **reemplazando** los actuales.
2. Listo. Arranca tu app como siempre (`flask run` / gunicorn).

> Conserva tus variables Jinja, `csrf_token()`, `money()`, `current_user`,
> `url_for(...)` y los bloques `{% block %}` exactamente como estaban.

## Tema claro / oscuro
- Por defecto arranca en **oscuro**.
- El botón ☀/🌙 del navbar alterna y **guarda la preferencia** en `localStorage`
  (`fin-theme`). Se aplica antes de pintar, sin parpadeo.
- Todo el color sale de variables CSS en `static/style.css`
  (`--fin-accent`, `--fin-bg`, etc.). Cambia el violeta ahí si quieres otro tono.

## Notas
- `preview.html` es solo para previsualizar con datos de ejemplo; no lo subas al repo.
- Las fuentes (Manrope + Space Grotesk) y Bootstrap se cargan por CDN, igual que antes.
