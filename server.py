"""
Unit Tagger - Flask backend
Proxies tr-engine API and writes unit aliases to CSV files.
"""

import csv
import os
import re
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static")

# ── Configuration ─────────────────────────────────────────────────────────────

TR_ENGINE_BASE = "http://localhost:8080/api/v1"

DB_DSN = "host=127.0.0.1 port=5433 dbname=trengine user=trengine password=trengine"

CSV_DIR = Path("/home/brent/docker/trunk-recorder/configs")

# Map system_id → CSV filename
SYSTEM_CSV_MAP = {
    1: "pscunits.csv",   # pscsite4 (file-watch)
    4: "pscunits.csv",   # pscsite4 (trunk-recorder)
    2: "ipscunits.csv",  # ipscpend (file-watch)
    3: "ipscunits.csv",  # ipscand  (file-watch)
    5: "ipscunits.csv",  # ipscpend/ipscand (trunk-recorder)
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def csv_path(system_id: int) -> Path | None:
    fname = SYSTEM_CSV_MAP.get(system_id)
    return CSV_DIR / fname if fname else None


def read_csv(path: Path) -> dict[str, str]:
    """Return {unit_id_str: alias} from CSV."""
    entries = {}
    if not path.exists():
        return entries
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                uid = row[0].strip()
                alias = row[1].strip()
                entries[uid] = alias
    return entries


def write_alias(path: Path, unit_id: str, alias: str):
    """Append a new alias line to the CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([unit_id, alias])


def update_alias(path: Path, unit_id: str, alias: str):
    """Update an existing line in the CSV in-place."""
    if not path.exists():
        write_alias(path, unit_id, alias)
        return
    rows = []
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if row and row[0].strip() == unit_id:
                rows.append([unit_id, alias])
            else:
                rows.append(row)
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def get_call_units(call_id: int) -> list[dict]:
    """Get units for a call by joining unit_events to calls on call_num."""
    with psycopg2.connect(DB_DSN) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT ue.unit_rid AS unit_id, ue.unit_alpha_tag AS alpha_tag
                FROM unit_events ue
                JOIN calls c ON ue.call_num = c.call_num AND ue.system_id = c.system_id
                WHERE c.call_id = %s
                  AND ue.unit_rid > 0
                  AND ue.event_type IN ('call', 'end', 'join', 'on')
                ORDER BY ue.unit_rid
            """, (call_id,))
            return [dict(row) for row in cur.fetchall()]


def tr_get(path: str, params: dict = None):
    r = requests.get(f"{TR_ENGINE_BASE}{path}", params=params, timeout=10)
    return r.status_code, r.json()


def tr_patch(path: str, body: dict):
    r = requests.patch(f"{TR_ENGINE_BASE}{path}", json=body, timeout=10)
    return r.status_code, r.json()


# ── API Routes ────────────────────────────────────────────────────────────────

@app.route("/api/calls")
def list_calls():
    """Proxy recent calls from tr-engine."""
    params = {
        "limit": request.args.get("limit", 50),
        "offset": request.args.get("offset", 0),
    }
    for key in ("system_id", "site_id", "tgid"):
        if request.args.get(key):
            params[key] = request.args[key]
    status, data = tr_get("/calls", params)
    return jsonify(data), status


@app.route("/api/calls/<int:call_id>")
def get_call(call_id):
    status, data = tr_get(f"/calls/{call_id}")
    return jsonify(data), status


@app.route("/api/calls/<int:call_id>/transmissions")
def get_transmissions(call_id):
    status, data = tr_get(f"/calls/{call_id}/transmissions")
    return jsonify(data), status


@app.route("/api/calls/<int:call_id>/audio")
def stream_audio(call_id):
    """Proxy audio stream from tr-engine."""
    from flask import Response
    r = requests.get(f"{TR_ENGINE_BASE}/calls/{call_id}/audio", stream=True, timeout=30)
    headers = {}
    for h in ("Content-Length", "Accept-Ranges", "Content-Range"):
        if h in r.headers:
            headers[h] = r.headers[h]
    return Response(
        r.iter_content(chunk_size=8192),
        status=r.status_code,
        content_type=r.headers.get("Content-Type", "audio/mp4"),
        headers=headers,
    )


@app.route("/api/units/<path:unit_id>/check")
def check_unit(unit_id):
    """Check if a unit_id already exists in the relevant CSV."""
    # unit_id format: "system_id:unit_id" e.g. "1:924003"
    parts = unit_id.split(":")
    if len(parts) != 2:
        return jsonify({"error": "Invalid unit ID format"}), 400
    try:
        system_id = int(parts[0])
        raw_unit_id = parts[1]
    except ValueError:
        return jsonify({"error": "Invalid system_id"}), 400

    path = csv_path(system_id)
    if path is None:
        return jsonify({"error": f"No CSV mapped for system_id {system_id}"}), 404

    entries = read_csv(path)
    existing = entries.get(raw_unit_id)
    return jsonify({
        "unit_id": raw_unit_id,
        "system_id": system_id,
        "csv_file": path.name,
        "exists": existing is not None,
        "current_alias": existing,
    })


@app.route("/api/units/<path:unit_id>/alias", methods=["POST"])
def save_alias(unit_id):
    """
    Write alias to CSV and PATCH tr-engine.
    Body: { "alias": "Engine 1", "overwrite": true/false }
    """
    parts = unit_id.split(":")
    if len(parts) != 2:
        return jsonify({"error": "Invalid unit ID format"}), 400
    try:
        system_id = int(parts[0])
        raw_unit_id = parts[1]
    except ValueError:
        return jsonify({"error": "Invalid system_id"}), 400

    body = request.get_json()
    alias = (body or {}).get("alias", "").strip()
    overwrite = (body or {}).get("overwrite", False)

    if not alias:
        return jsonify({"error": "Alias cannot be empty"}), 400

    path = csv_path(system_id)
    if path is None:
        return jsonify({"error": f"No CSV mapped for system_id {system_id}"}), 404

    entries = read_csv(path)
    already_exists = raw_unit_id in entries

    if already_exists and not overwrite:
        return jsonify({
            "conflict": True,
            "current_alias": entries[raw_unit_id],
            "csv_file": path.name,
        }), 409

    # Write CSV
    if already_exists:
        update_alias(path, raw_unit_id, alias)
    else:
        write_alias(path, raw_unit_id, alias)

    # PATCH tr-engine
    tr_status, tr_data = tr_patch(f"/units/{unit_id}", {
        "alpha_tag": alias,
        "alpha_tag_source": "manual",
    })

    return jsonify({
        "success": True,
        "unit_id": raw_unit_id,
        "system_id": system_id,
        "alias": alias,
        "csv_file": path.name,
        "tr_engine_status": tr_status,
        "tr_engine_response": tr_data,
    })


@app.route("/api/calls/<int:call_id>/units")
def get_call_units_route(call_id):
    """Return units active on a call via direct DB query."""
    try:
        units = get_call_units(call_id)
        return jsonify({"units": units})
    except Exception as e:
        return jsonify({"error": str(e), "units": []}), 500


# ── Static / SPA ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
