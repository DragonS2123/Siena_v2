# Nucleares API Probe

Phase 0 probe for the Nucleares game simulation API/webserver.

This is read-only. It does not write game values, does not add Siena backend
endpoints, and does not touch the frontend.

## Run

From the repository root:

```powershell
python scripts/probe_nucleares.py
```

Defaults are tuned for the observed Nucleares webserver behavior:

- host order: `localhost`, `[::1]`, `127.0.0.1`
- port order: `8785`, `8786`, `8787`, `8080`, `8000`

Optional:

```powershell
python scripts/probe_nucleares.py --limit 50
python scripts/probe_nucleares.py --host localhost --port 8785 --limit 100
```

## What It Tries

1. Uses read-only HTTP discovery as the primary path.
2. `GET /` on each host/port.
3. If the root page is HTML and contains links/text like
   `?variable=AMBIENT_TEMPERATURE`, extracts variable names with:

   ```text
   variable=([A-Z0-9_]+)
   ```

4. Reads up to `--limit` variables with `GET /?variable=NAME`.
5. Stores values as strings. It does not assume numeric values.
6. Imports NuCon if it is already installed and reports whether a readable
   `get_all()`/`state()`/`status()` style method was found. NuCon failure is
   not a probe failure when HTTP discovery works.

No dependencies are installed automatically. If NuCon is missing, the script
prints a clear suggestion instead of a stacktrace.

## Output

The probe prints:

- whether Nucleares was reachable
- selected host/port/base URL
- whether Nucleares appears bound to IPv6 localhost rather than IPv4
- parameter count
- sampled count
- 10-20 sample keys, preferring interesting telemetry-looking names
- interesting values when present, for example `AMBIENT_TEMPERATURE`,
  `CORE_TEMPERATURE`, `CORE_PRESSURE`, `CONDENSER_TEMPERATURE`,
  `ALARMS_ACTIVE`, or `AO_AGENT_STATUS`
- whether writable parameters are exposed (`false`; this probe is read-only)
- snapshot path

Snapshot is written to:

```text
storage/game/nucleares_snapshot.json
```

If Nucleares is not reachable, the snapshot still records the attempted
host/ports and the clear unreachable status.

If the webserver is reachable but no `?variable=NAME` entries are found, the
snapshot uses `discovery="root_reachable_no_variables"` and stores the first
500 characters of the root page as `root_preview`.

## Next Step

If the probe finds stable readable telemetry keys, the next integration pass
can add a Siena Game Bridge read-only endpoint that serves a normalized subset
of this snapshot. Write support should remain out of scope until the readable
contract is understood and explicitly approved.
