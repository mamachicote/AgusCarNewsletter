# 🚗 Buscador de autos para Agus

Este repo busca todos los días autos compactos automáticos en **Mercado Libre Argentina** y **Kavak Argentina**, los filtra según criterios pensados para Agus (hasta USD 18.000, hasta 100.000 km, año 2012 o más nuevo, zona CABA + Buenos Aires) y manda un **email HTML con las oportunidades nuevas del día** a `agustina.machicote@gmail.com`.

Corre solo todos los días a las **9 AM hora Argentina** usando GitHub Actions — no hace falta dejar nada prendido.

---

## 📦 Qué hay en este repo

- `buscador_autos.py` — el script principal (scraping + filtrado + scoring + email).
- `diagnostico.py` — script de diagnóstico para verificar que los scrapers funcionan, sin filtros y sin envío de email.
- `requirements.txt` — dependencias de Python.
- `.github/workflows/buscar_autos.yml` — cron diario.
- `.github/workflows/diagnostico.yml` — workflow manual para correr el diagnóstico.
- `README.md` — este archivo.

---

## 🚀 Setup paso a paso

### 1. Crear el repo en GitHub

1. Entrá a https://github.com/new y creá un repo **privado** (ej: `buscador-autos-agus`).
2. Subí los 4 archivos de este proyecto al repo. Podés hacerlo desde la interfaz web (botón **Add file → Upload files**) o desde la terminal si te manejás con git.

### 2. Generar una "App Password" de Gmail

La cuenta de Gmail que mande el email necesita una **App Password** (no sirve la contraseña normal).

1. Activá la verificación en 2 pasos si todavía no lo hiciste: https://myaccount.google.com/security
2. Entrá a https://myaccount.google.com/apppasswords
3. Generá una nueva contraseña de aplicación (nombre: "Buscador de autos").
4. Google te va a mostrar una clave de 16 caracteres — **copiala**, sólo se muestra una vez.

### 3. Cargar los secrets en GitHub

En el repo, andá a **Settings → Secrets and variables → Actions → New repository secret** y agregá dos secrets:

| Nombre            | Valor                                       |
|-------------------|---------------------------------------------|
| `SENDER_EMAIL`    | Tu cuenta de Gmail (ej: `tuvieja@gmail.com`) |
| `SENDER_PASSWORD` | La App Password de 16 caracteres del paso 2 |

### 4. Correr el workflow por primera vez (manual)

1. Andá a la pestaña **Actions** del repo.
2. Si te aparece un cartel pidiendo habilitar los workflows, clickeá para aceptarlo.
3. En la barra izquierda, clickeá **"Buscar autos diariamente"**.
4. Arriba a la derecha, clickeá **"Run workflow" → "Run workflow"**.
5. Esperá 1-3 minutos y revisá que el email haya llegado a `agustina.machicote@gmail.com`.

### 5. Listo

A partir de ahora corre solo todos los días a las 9 AM Argentina. No tenés que hacer nada.

Si querés ver los logs de una corrida (para verificar que funciona o para debuggear), entrá a la pestaña **Actions** y clickeá sobre la corrida que te interese.

---

## 🧠 Qué criterios usa

- **Presupuesto:** hasta USD 18.000
- **Kilometraje:** hasta 100.000 km
- **Año mínimo:** 2012
- **Transmisión:** automática
- **Tamaño:** compactos / chicos (Toyota Etios, Honda Fit/City, Suzuki Vitara, Chevrolet Onix, VW Polo/Gol, Peugeot 208, Renault Sandero, Fiat Argo, Ford Ka)
- **Zona:** CABA + Provincia de Buenos Aires.
  Excepción: si aparece un auto muy bueno fuera de AMBA, entra en el email como "oportunidad destacada" (solo los 3 mejores del día).

Cada auto que aparece viene con un mini benchmark: score de confiabilidad, consumo, mantenimiento, pros y contras.

Para no repetir autos entre emails, el workflow guarda un historial (`sent_cars.json`) en la cache de Actions.

---

## 🛠 Si algo deja de funcionar

**Síntoma:** el email llega vacío o con pocos resultados durante varios días seguidos.

**Causa probable:** Mercado Libre o Kavak cambiaron los selectores CSS de sus páginas, o el runner de GitHub está siendo bloqueado.

**Cómo verificarlo (sin tocar código):** correr el workflow de diagnóstico.

1. Andá a la pestaña **Actions** del repo.
2. Clickeá **"Diagnóstico de scrapers"** → **Run workflow**.
3. Mirá los logs: te va a decir cuántas cards encontró por modelo, cuántas estaban en USD, cuántas se rechazaron y por qué. Si ves `0 cards` significa que ML está sirviendo otro HTML (probablemente captcha o página vacía); si ves `0 listings en USD` significa que están todas en pesos.

También se puede correr localmente:
```bash
pip install -r requirements.txt
python diagnostico.py            # todos los modelos
python diagnostico.py --model fit  # solo uno
```

**Cómo arreglarlo:** hay que tocar las funciones `scrape_mercadolibre()` (busca selectores como `li.ui-search-layout__item`) y `scrape_kavak()` dentro de `buscador_autos.py`. Si no te sentís con ganas de tocar el código, pasale el repo y el error a cualquier persona que programe — es un ajuste de 10-30 minutos.

**Nota sobre Kavak:** la web de Kavak es una SPA (se renderiza con JavaScript), así que el scraping pasivo no encuentra autos en el HTML. Por ahora el script depende mayormente de Mercado Libre y deja Kavak como best-effort. Si querés sumar Kavak con resultados, hay que migrar a Playwright (browser headless), que es bastante más laburo.

**Para correrlo localmente** (y ver el output antes de pushear cambios):

```bash
pip install -r requirements.txt
export SENDER_EMAIL="tuvieja@gmail.com"
export SENDER_PASSWORD="tu-app-password-de-16-chars"
python buscador_autos.py
```

---

## 💌 Destinatario

El email está hardcodeado para que llegue a `agustina.machicote@gmail.com`. Si querés cambiarlo, es la constante `DESTINATARIO` al principio de `buscador_autos.py`.
