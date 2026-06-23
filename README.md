# Portal de Scrapers

Web unificada para descargar catálogos de **Beauty Depot** e inventario de **Solís Comercial** (Solcom ERP).

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

- `output/beautydepot_productos.csv` — ~2045 productos
- `output/inventario.csv` — ~516 productos (SKU, nombre, marca, cantidad, condición)

## API

| Método | Ruta |
|--------|------|
| POST | `/api/beautydepot/run` |
| POST | `/api/solcom/run` |
| GET | `/api/<source>/status` |
| GET | `/download/<source>/csv` |

## CLI

```powershell
python scrape_inventory.py
python scrape_beautydepot.py
```
