# unit-tagger

A lightweight tagging UI and Flask backend that sits alongside [tr-engine](https://github.com/lumenprima/tr-engine) to let you assign human-readable aliases to radio unit IDs captured by [trunk-recorder](https://github.com/TrunkRecorder/trunk-recorder).

## What it does

- Displays recent calls from tr-engine with talkgroup and unit details
- Lets you click any untagged unit and assign a label (e.g. "Engine 1", "Dispatch")
- Writes aliases back to trunk-recorder's `unitTagsFile` CSV files so tags survive restarts
- PATCHes tr-engine in real time so aliases appear immediately without restarting anything

## Architecture

```
Browser (index.html)
    │
    ▼
Flask (server.py :5000)
    ├── GET  /api/calls              → proxy → tr-engine :8080/api/v1/calls
    ├── GET  /api/calls/:id/audio    → proxy → tr-engine :8080/api/v1/calls/:id/audio
    ├── GET  /api/calls/:id/units    → direct psycopg2 query (see note below)
    ├── POST /api/units/:id/alias    → writes CSV + PATCH tr-engine
    └── GET  /api/units/:id/check    → reads CSV to detect existing alias
```

### Why the direct DB query for units?

tr-engine's `call_transmissions` table is populated from `src_list` in the MQTT `call_end` message, but trunk-recorder's MQTT plugin does **not** include `srcList` in its payload. As a result, `call_transmissions` is always empty and the `/calls/:id/transmissions` endpoint returns nothing useful.

The workaround: `unit_events` is populated correctly (trunk-recorder publishes unit grant/join/end events on a separate MQTT topic). Units are correlated to a call by joining `unit_events` to `calls` on `(call_num, system_id)`:

```sql
SELECT DISTINCT ue.unit_rid AS unit_id, ue.unit_alpha_tag AS alpha_tag
FROM unit_events ue
JOIN calls c ON ue.call_num = c.call_num AND ue.system_id = c.system_id
WHERE c.call_id = $1
  AND ue.unit_rid > 0
  AND ue.event_type IN ('call', 'end', 'join', 'on')
ORDER BY ue.unit_rid
```

## Setup

### Prerequisites

- tr-engine running (default: `http://localhost:8080`)
- PostgreSQL accessible (tr-engine's DB)
- Python 3.11+
- `pip install flask requests psycopg2-binary`

### Configuration

Edit the constants at the top of `server.py`:

```python
TR_ENGINE_BASE = "http://localhost:8080/api/v1"

DB_DSN = "host=127.0.0.1 port=5432 dbname=trengine user=trengine password=trengine"

CSV_DIR = Path("/path/to/trunk-recorder/configs")

SYSTEM_CSV_MAP = {
    1: "pscunits.csv",
    3: "ipscunits.csv",
}
```

`SYSTEM_CSV_MAP` maps tr-engine `system_id` values to the `unitTagsFile` CSV referenced in your trunk-recorder `config.json`.

### trunk-recorder config requirements

Each system in `config.json` needs:

```json
"unitTagsFile": "/app/yourfile.csv",
"unitTagsMode": "user"
```

`unitTagsMode: "user"` tells trunk-recorder to use the CSV as the authoritative source rather than overwriting it with OTA data.

### Running

```bash
python3 server.py
```

Open `http://<host>:5000` in a browser.

## CSV format

Standard trunk-recorder unit tags format — one unit per line:

```
924003,Engine 1
924004,Engine 2
```

The file is read on every request and written atomically on save, so trunk-recorder picks up new tags on its next reload cycle without a restart.
