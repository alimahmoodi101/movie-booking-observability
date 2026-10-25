# Movie Booking - Observability Assignment

A small FastAPI movie ticket booking service instrumented with Prometheus metrics and JSON logs shipped to the ELK stack.

## Problem & users
Moviegoers need to browse showtimes, hold seats while they decide, and confirm a booking before the hold expires. This project builds that flow and observes it end-to-end: application health (latency, failures) and business activity (bookings, cancellations, seats currently held).

## Stack
- App: Python + FastAPI + SQLite
- Metrics: Prometheus + Grafana + Node Exporter
- Logs: Filebeat + Elasticsearch + Kibana

## Start everything
```bash
docker compose up --build -d
```
Wait ~30s for Elasticsearch to be ready, then check:
```bash
docker compose ps
Invoke-WebRequest -Uri http://localhost:8000/healthz -UseBasicParsing
```

| Tool       | URL                          | Login       |
|------------|------------------------------|-------------|
| API        | http://localhost:8000/docs   | -           |
| Prometheus | http://localhost:9090        | -           |
| Grafana    | http://localhost:3000        | admin/admin |
| Kibana     | http://localhost:5601        | -           |

Grafana auto-loads the Prometheus datasource and a starter dashboard ("Movie Booking - Overview").

## Try it (Windows PowerShell)
```powershell
# List movies and a showtime
Invoke-WebRequest -Uri http://localhost:8000/movies -UseBasicParsing
Invoke-WebRequest -Uri http://localhost:8000/showtimes/1 -UseBasicParsing

# Hold seats 1 and 2 for showtime 1
Invoke-WebRequest -Uri http://localhost:8000/showtimes/1/hold -Method POST -ContentType "application/json" -Body '{"seat_ids": [1, 2], "user_id": "alice"}' -UseBasicParsing

# Confirm the booking
Invoke-WebRequest -Uri http://localhost:8000/bookings/confirm -Method POST -ContentType "application/json" -Body '{"showtime_id": 1, "seat_ids": [1, 2], "user_id": "alice"}' -UseBasicParsing

# Cancel booking 1
Invoke-WebRequest -Uri http://localhost:8000/bookings/1/cancel -Method POST -UseBasicParsing
```

Or just use the Swagger UI at http://localhost:8000/docs which works on any OS with no commands needed.

Raw Prometheus metrics: `Invoke-WebRequest -Uri http://localhost:8000/metrics -UseBasicParsing`

## Kibana setup (first time only)
1. Go to Kibana -> Stack Management -> Data Views
2. Create a data view matching `filebeat-*`, time field `@timestamp`
3. Go to Discover to search logs, e.g. `severity: "ERROR"` or `message: "booking confirmed"`

## Part E.1 - Reproduce a problem (database timeout)
1. **Baseline:** with the stack running, hit `/movies` a few times and note p95 latency near zero in Grafana
2. **Predict:** introducing the fault should raise p95 for `/movies` to around 1 second and ERROR logs should appear in Kibana
3. **Inject the fault:** in `app/main.py`, modify the `/movies` endpoint to add `time.sleep(0.6)` and raise `HTTPException(status_code=500)`, then rebuild:
```powershell
docker compose up -d --build
```
4. **Run load:**
```powershell
1..15 | ForEach-Object { try { Invoke-WebRequest -Uri "http://localhost:8000/movies" -UseBasicParsing | Out-Null } catch {}; Start-Sleep -Milliseconds 500 }
```
5. **Observe:** Grafana shows `/movies` p95 spike to ~1s. Kibana search `message: "database connection timeout"` shows ERROR logs with unique request_ids
6. **Recover:** revert the `/movies` endpoint, rebuild, repeat load, confirm p95 drops back to zero and no new ERROR logs appear

## Part E.2 - Cardinality explosion
Hit the demo endpoint 100 times:
```powershell
1..100 | ForEach-Object { Invoke-WebRequest -Uri "http://localhost:8000/demo/cardinality-bad" -Method POST -UseBasicParsing | Out-Null }
```
In Prometheus, query `count(demo_requests_total)` and watch it reach ~200 unique series.

To show the fix: remove the `request_id` label from `DEMO_REQUESTS_TOTAL` in `metrics.py`, restart the app, repeat the query. Series count drops to 1.

Note: removing the label does not delete already-stored history in Prometheus; old series just stop receiving new samples.

## Cleanup
```powershell
docker compose down       # stop everything, keep volumes
docker compose down -v    # stop everything AND delete all data
```

## Notes
- Filebeat uses the `ndjson` parser to handle Docker's json-file log envelope wrapping the app's inner JSON log line
- Holds expire after 120 seconds and are released automatically by a background task
- Node Exporter collects host-level CPU, memory, disk and network metrics under the instance label `movie-booking-host`
