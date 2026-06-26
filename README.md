# Portal de Scrapers

Web unificada para descargar catálogos de **Beauty Depot**, **Pinturas Biotech**, **Molvu** e inventario de **Solís Comercial** (Solcom ERP).

## Setup local

```powershell
cd Documents\projects\Srapping
pip install -r requirements.txt
playwright install chromium
```

Copia `.env.example` a `.env` y completa `SOLCOM_EMAIL`, `SOLCOM_PASSWORD` y opcionalmente `SCRAPE_SECRET`.

## Uso local

Doble clic en `iniciar.bat` o:

```powershell
python app.py
```

Abre http://127.0.0.1:5050

## Deploy en Render

1. Conecta el repo [leandrogomezc/scrapers-portal](https://github.com/leandrogomezc/scrapers-portal) en [Render](https://render.com)
2. Render usa `render.yaml` automáticamente
3. Configura variables secretas en el dashboard:
   - `SCRAPE_SECRET`
   - `SOLCOM_EMAIL`
   - `SOLCOM_PASSWORD`
4. `USE_PLAYWRIGHT=false` (default) — Solís Comercial usa auth HTTP sin navegador
5. URL de producción: `https://scrapers-portal.onrender.com` (o la que asigne Render)

**Start command:** `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1`

## Variables de entorno

| Variable | Descripción |
|---|---|
| `SCRAPE_SECRET` | Token para proteger `POST /api/*/run` |
| `SOLCOM_EMAIL` | Usuario ERP Solís Comercial |
| `SOLCOM_PASSWORD` | Contraseña ERP |
| `SOLCOM_BASE_URL` | Default `https://solcom-erp.vercel.app` |
| `SUPABASE_URL` | URL Supabase del ERP |
| `SUPABASE_ANON_KEY` | Clave pública publishable |
| `USE_PLAYWRIGHT` | `false` en Render; `true` solo si quieres modo navegador local |

## Salida CSV

- `output/beautydepot_productos.csv` — ~2045 productos (catálogo completo)
- `output/biotech_productos.csv` — catálogo pinturasbiotech.com (Odoo, ~207 productos)
- `output/molvu_productos.csv` — catálogo molvu.com.gt (Shopify)
- `output/beautydepot_actualizacion.xlsx` — actualización parcial desde archivo maestro
- `output/inventario.csv` — ~516 productos (SKU, nombre, marca, cantidad, condición)
- `output/solcom_actualizacion.xlsx` — actualización parcial Punto Digital desde archivo maestro

## Actualización de inventario Beauty Depot

Flujo para generar un CSV que **solo modifica** `Precio` e inventario en la columna `Beauty Depot`:

1. Sube tu **archivo maestro** (`.xlsx` recomendado, también `.csv`) con columnas obligatorias: `SKU`, `Precio`, `Beauty Depot` (puede incluir otras columnas).
2. Ejecuta el scrape de Beauty Depot.
3. Pulsa **Generar actualización**.
4. Descarga `beautydepot_actualizacion.xlsx` (o `.csv` si subiste CSV) e impórtalo en tu sistema.

Reglas por fila del maestro (comparación por `SKU`):

| Columna | Si el SKU está en el scrape | Si no está en el scrape |
|---------|----------------------------|-------------------------|
| `Precio` | Precio de venta al público del scrape | Sin cambio (valor original) |
| `Beauty Depot` | `10` | `0` |

**Render Free:** el archivo maestro subido se guarda en disco efímero; vuelve a subirlo tras cada redeploy.

## Actualización de inventario Solís Comercial

Flujo para generar un archivo que **solo modifica** la columna `Punto Digital`:

1. Sube tu **archivo maestro** (`.xlsx` recomendado, también `.csv`) con columnas obligatorias: `SKU`, `Punto Digital`.
2. Ejecuta el scrape de Solís Comercial.
3. Pulsa **Generar actualización**.
4. Descarga `solcom_actualizacion.xlsx` e impórtalo en tu sistema.

Reglas por fila del maestro (comparación por `SKU`):

| Columna | Si el SKU está en el scrape | Si no está en el scrape |
|---------|----------------------------|-------------------------|
| `Punto Digital` | Cantidad real del inventario Solcom | `0` |

Si un SKU aparece más de una vez en el scrape, se suman las cantidades.

## API

| Método | Ruta |
|--------|------|
| POST | `/api/beautydepot/run` |
| POST | `/api/biotech/run` |
| POST | `/api/molvu/run` |
| POST | `/api/beautydepot/upload-master` |
| POST | `/api/beautydepot/generate-update` |
| GET | `/api/beautydepot/update-status` |
| POST | `/api/solcom/run` |
| POST | `/api/solcom/upload-master` |
| POST | `/api/solcom/generate-update` |
| GET | `/api/solcom/update-status` |
| GET | `/api/<source>/status` |
| GET | `/download/<source>/csv` |
| GET | `/download/beautydepot/update-csv` |
| GET | `/download/solcom/update-csv` |

## CLI

```powershell
python scrape_inventory.py
python scrape_beautydepot.py
python scrape_biotech.py
python scrape_molvu.py
```
