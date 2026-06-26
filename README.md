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

Reglas por fila del maestro:

| Columna | Relacionado con el scrape | Sin relación |
|---------|---------------------------|--------------|
| `Punto Digital` | Cantidad real del inventario Solcom | `0` |

El inventario se relaciona en dos pasos:

1. Por `SKU` exacto entre el maestro y el scrape.
2. Si no hay match por `SKU`, por **nombre + `Atributos`** contra el inventario scrapeado (mismo emparejador flexible que los costos).

Cuando una fila se relaciona por nombre y su columna `SKU` está **vacía**, se escribe el `SKU` del scrape para dejarla relacionada a futuro (si ya tiene `SKU`, no se modifica). Si un SKU aparece más de una vez en el scrape, se suman las cantidades.

### Actualización de costo (lista pegada)

En la tarjeta de Solís Comercial hay un campo de texto **opcional** para pegar una lista de costos con el formato:

```
SAMSUNG
$ 130…..A16 (128_4) DS
$ 94…..A07 (64_4) DS
```

Reglas:

- Cada línea `$ <costo> … <nombre>` actualiza la columna `Costo` del maestro. El valor pegado está en **dólares** y se multiplica por **37.1** antes de escribirse (ej: `340` → `12614.00`).
- Si la columna `Precio` queda con margen bruto menor al **12%** contra el `Costo`, se sube automáticamente a `round(Costo / 0.88)`. Ej: `Costo=12614.00` requiere `Precio=14334`.
- El emparejamiento es flexible: se compara el **nombre + especificaciones** (almacenamiento, RAM, conectividad WIFI/LTE, tamaño en MM), no por coincidencia exacta. Ej: `A07 (64_4) DS` empareja con un producto cuyo nombre o columna `Atributos` indique 64 GB de almacenamiento y 4 GB de RAM.
- El nombre del texto pegado solo se usa para relacionar productos; no se modifica el nombre del maestro.
- Solo se actualizan filas **que ya existen** en el maestro. Si un producto de la lista no existe en el maestro, **se ignora** (no se agrega).
- Si el match es ambiguo (varios candidatos igual de buenos), no se aplica y se reporta como ignorado.
- El maestro debe incluir las columnas `Costo`, `Precio` y una columna de nombre (`Nombre del Producto`, `Nombre`, `Descripción` o `Producto`). Opcionalmente una columna `Atributos` para specs fuera del nombre.
- Si el campo se deja vacío, la generación funciona igual que antes (solo actualiza `Punto Digital`).

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
