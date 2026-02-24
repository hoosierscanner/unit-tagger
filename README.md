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

### Mapping systems to CSV files

`SYSTEM_CSV_MAP` connects tr-engine's `system_id` to the `unitTagsFile` CSV defined in your trunk-recorder `config.json`. You need both sides to agree on the same file.

**Step 1 — find your system_id in tr-engine:**

```bash
curl -s http://localhost:8080/api/v1/systems | jq '.[] | {id, short_name}'
```

Example output:
```json
{ "id": 1, "short_name": "pscsite4" }
{ "id": 3, "short_name": "ipscsite2" }
```

**Step 2 — match each system to a CSV in trunk-recorder `config.json`:**

```json
{
  "shortName": "pscsite4",
  "unitTagsFile": "/app/pscunits.csv",
  "unitTagsMode": "user"
}
```

**Step 3 — add the mapping to `SYSTEM_CSV_MAP` in `server.py`:**

```python
CSV_DIR = Path("/path/to/trunk-recorder/configs")  # directory containing the CSVs

SYSTEM_CSV_MAP = {
    1: "pscunits.csv",   # tr-engine system_id 1 = pscsite4
    3: "ipscunits.csv",  # tr-engine system_id 3 = ipscsite2
}
```

The key is the tr-engine `system_id` integer. The value is the filename (not full path) of the CSV inside `CSV_DIR`. Multiple `system_id` values can share the same CSV file — useful when two systems (e.g. multiSite P25 sites) share the same unit population.

`unitTagsMode: "user"` in trunk-recorder tells it to treat the CSV as authoritative and not overwrite it with OTA unit data.

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
