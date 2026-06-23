# Portal de Scrapers

Web unificada para descargar catálogos de **Beauty Depot** e inventario de **Solís Comercial** (Solcom ERP).

## Setup

```powershell
cd Documents\projects\Srapping
pip install -r requirements.txt
playwright install chromium
```

Copia `.env.example` a `.env` y completa:

```env
BEAUTY_DEPOT_DIR=c:\Users\leand\beautydepot-scraper
SOLCOM_EMAIL=tu-correo@ejemplo.com
SOLCOM_PASSWORD=tu-contrasena
SOLCOM_BASE_URL=https://solcom-erp.vercel.app
SCRAPE_SECRET=opcional
```

## Uso web (recomendado)

Doble clic en `iniciar.bat` o:

```powershell
python app.py
```

Abre http://127.0.0.1:5050

- **Beauty Depot** — catálogo ~2045 productos (~70 s)
- **Solís Comercial** — inventario ~516 productos (~25 s)

Ambos pueden ejecutarse en paralelo. CSV en `output/`:

- `beautydepot_productos.csv`
- `inventario.csv`

## Uso CLI (solo Solcom)

```powershell
python scrape_inventory.py
```

## API

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/beautydepot/run` | Inicia scrape Beauty Depot |
| POST | `/api/solcom/run` | Inicia scrape Solís Comercial |
| GET | `/api/<source>/status` | Estado del job |
| GET | `/download/<source>/csv` | Descarga CSV |

## Notas

- Beauty Depot usa el scraper en `BEAUTY_DEPOT_DIR` (sin duplicar código).
- El deploy en Render de `beautydepot-scraper` sigue disponible por separado.
- Playwright requiere Chromium instalado localmente.
