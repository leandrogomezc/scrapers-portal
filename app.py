"""Unified Flask portal for Beauty Depot and Solís Comercial scrapers."""

import os
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from scrape_inventory import OUTPUT_PATH as SOLCOM_OUTPUT
from scrape_inventory import run_scrape as solcom_run_scrape

APP_DIR = Path(__file__).parent
OUTPUT_DIR = APP_DIR / "output"
BEAUTY_OUTPUT = OUTPUT_DIR / "beautydepot_productos.csv"

BEAUTY_DEPOT_DIR = Path(
    os.getenv("BEAUTY_DEPOT_DIR", r"c:\Users\leand\beautydepot-scraper")
)
if str(BEAUTY_DEPOT_DIR) not in sys.path:
    sys.path.insert(0, str(BEAUTY_DEPOT_DIR))

from scrape import run_scrape as beauty_run_scrape  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="/static")

SCRAPE_SECRET = os.environ.get("SCRAPE_SECRET", "")

_job_lock = threading.Lock()
_idle_job = {
    "status": "idle",
    "message": "",
    "rows": None,
    "started_at": None,
    "finished_at": None,
    "output_path": None,
}
_jobs: dict[str, dict] = {
    "beautydepot": dict(_idle_job),
    "solcom": dict(_idle_job),
}

SCRAPERS: dict[str, dict] = {
    "beautydepot": {
        "label": "Beauty Depot",
        "run": beauty_run_scrape,
        "output_path": BEAUTY_OUTPUT,
        "download_name": "beautydepot_productos.csv",
        "start_message": "Iniciando scrape de Beauty Depot...",
    },
    "solcom": {
        "label": "Solís Comercial",
        "run": solcom_run_scrape,
        "output_path": SOLCOM_OUTPUT,
        "download_name": "inventario.csv",
        "start_message": "Iniciando extracción de inventario...",
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_scraper(source: str) -> dict | None:
    return SCRAPERS.get(source)


def _update_job(source: str, **kwargs) -> None:
    with _job_lock:
        _jobs[source].update(kwargs)


def _get_job(source: str) -> dict:
    with _job_lock:
        return dict(_jobs[source])


def _is_authorized() -> bool:
    if not SCRAPE_SECRET:
        return True
    token = request.headers.get("X-Scrape-Token")
    if not token and request.is_json and request.json:
        token = request.json.get("token")
    if not token:
        token = request.form.get("token")
    return token == SCRAPE_SECRET


def _run_job(source: str) -> None:
    scraper = SCRAPERS[source]
    run_fn: Callable = scraper["run"]
    output_path: Path = scraper["output_path"]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def on_progress(message: str) -> None:
        _update_job(source, message=message)

    try:
        result = run_fn(on_progress=on_progress, output_path=output_path)
        _update_job(
            source,
            status="done",
            message=f"Completado: {result['rows']} productos exportados",
            rows=result["rows"],
            finished_at=_utc_now(),
            output_path=result["output_path"],
        )
    except Exception as exc:
        _update_job(
            source,
            status="error",
            message=str(exc),
            finished_at=_utc_now(),
        )


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/api/<source>/run")
def api_run(source: str):
    scraper = _get_scraper(source)
    if not scraper:
        return jsonify({"error": "Fuente desconocida"}), 404

    if not _is_authorized():
        return jsonify({"error": "Token inválido o faltante"}), 401

    with _job_lock:
        if _jobs[source]["status"] == "running":
            return jsonify({"error": f"Ya hay un scrape de {scraper['label']} en ejecución"}), 409

        _jobs[source].update(
            {
                "status": "running",
                "message": scraper["start_message"],
                "rows": None,
                "started_at": _utc_now(),
                "finished_at": None,
                "output_path": None,
            }
        )

    thread = threading.Thread(target=_run_job, args=(source,), daemon=True)
    thread.start()
    return jsonify({"status": "running", "source": source}), 202


@app.get("/api/<source>/status")
def api_status(source: str):
    if not _get_scraper(source):
        return jsonify({"error": "Fuente desconocida"}), 404
    return jsonify(_get_job(source))


@app.get("/download/<source>/csv")
def download_csv(source: str):
    scraper = _get_scraper(source)
    if not scraper:
        return jsonify({"error": "Fuente desconocida"}), 404

    output_path: Path = scraper["output_path"]
    if not output_path.exists():
        return jsonify({"error": "No hay CSV disponible. Ejecuta el scrape primero."}), 404

    return send_file(
        output_path,
        as_attachment=True,
        download_name=scraper["download_name"],
        mimetype="text/csv",
    )


if __name__ == "__main__":
    import webbrowser

    port = int(os.environ.get("PORT", 5050))
    url = f"http://127.0.0.1:{port}"
    print("=" * 50)
    print("  Portal de Scrapers")
    print("  Beauty Depot + Solís Comercial")
    print(f"  Abre en tu navegador: {url}")
    print("  Presiona Ctrl+C para detener el servidor")
    print("=" * 50)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
