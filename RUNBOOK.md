# Watchtower — Incident Response Runbook

This is the "In Case of Emergency" guide for the Watchtower URL shortener service.
Use this runbook whenever an alert fires or something feels wrong.

---

## Quick Reference

| What | Where |
|------|-------|
| Dashboard | `http://localhost:5001/dashboard` |
| Health check | `http://localhost:5001/health` |
| Live metrics | `http://localhost:5001/metrics` |
| Live logs | `http://localhost:5001/logs` |
| Start app | `~/.local/bin/uv run python run.py` |
| Start monitor | `~/.local/bin/uv run python monitor.py` |
| Start database | `docker start postgres` |

---

## Alert: 🔴 Service Down

**What it means:** The `/health` endpoint is not responding. The app is either crashed, restarting, or the machine is unreachable.

### Step 1 — Confirm the outage
```bash
curl http://localhost:5001/health
```
- Got `{"status": "ok"}` → false alarm, monitor may have had a brief network hiccup. No action needed.
- Got `Connection refused` or no response → app is down. Continue to Step 2.

### Step 2 — Check if the process is running
```bash
lsof -i :5001
```
- No output → process is dead. Go to Step 3.
- Process is listed → app is up but unhealthy. Check logs (Step 4).

### Step 3 — Check if the database is up
```bash
docker ps | grep postgres
```
- Not running → start it: `docker start postgres`, then restart the app.
- Running → database is fine. Restart the app anyway.

### Step 4 — Check the logs for the crash reason
```bash
curl http://localhost:5001/logs | python3 -m json.tool | grep -i error
```
Or open the dashboard → Logs tab and filter by ERROR.

Look for:
- `OperationalError` or `InterfaceError` → database connection problem
- `IntegrityError` → bad data was written (check the request that triggered it)
- `ImportError` or `ModuleNotFoundError` → dependency missing, run `~/.local/bin/uv sync`

### Step 5 — Restart the app
```bash
~/.local/bin/uv run python run.py
```
Verify recovery:
```bash
curl http://localhost:5001/health
# → {"status": "ok"}
```

---

## Alert: ⚠️ High Error Rate

**What it means:** More than 50% of requests in the last 2 minutes returned a 5xx error. Something is broken but the app is still running.

### Step 1 — Identify which endpoints are failing
```bash
curl http://localhost:5001/logs | python3 -c "
import json, sys
logs = json.load(sys.stdin)
errors = [l for l in logs if l.get('status', 0) >= 500]
for e in errors[-10:]:
    print(e.get('method'), e.get('path'), e.get('status'))
"
```

### Step 2 — Check if the database is the cause
```bash
docker ps | grep postgres
```
- Not running → `docker start postgres`. Error rate should drop within one monitor cycle.
- Running → the issue is in the app code or a bad request pattern.

### Step 3 — Look for the error in logs
```bash
curl http://localhost:5001/logs | python3 -c "
import json, sys
logs = json.load(sys.stdin)
for l in logs:
    if 'exception' in l:
        print(l['timestamp'], l['exception'][:300])
        print('---')
"
```

Common causes:
- `OperationalError: SSL connection` → database restarted, app needs to reconnect → restart app
- `IntegrityError` → a route is trying to insert duplicate or invalid data → check the request payload
- Unhandled exception in a specific route → fix the code and redeploy

### Step 4 — Verify recovery
Watch the monitor output. Once the error rate drops below 50%, the alert will stop firing and the next successful check will reset the cooldown.

---

## Database Is Down

```bash
# Check status
docker ps -a | grep postgres

# Start it
docker start postgres

# Verify
docker exec postgres psql -U postgres -d hackathon_db -c "SELECT 1;"
```

If the container doesn't exist at all:
```bash
docker run --name postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=hackathon_db \
  -p 5432:5432 -d postgres
```
Then restart the app — Peewee will reconnect automatically on the next request.

---

## Port Already in Use

```bash
# Find what's using port 5001
lsof -i :5001

# Kill it
kill -9 $(lsof -ti:5001)

# Then restart the app
~/.local/bin/uv run python run.py
```

---

## How to Read the Logs

Every log line is JSON. Key fields:

| Field | Meaning |
|-------|---------|
| `timestamp` | UTC time of the event |
| `level` | `INFO`, `WARNING`, or `ERROR` |
| `message` | `"request"` for HTTP traffic, free text for app events |
| `method` | HTTP method (GET, POST, …) |
| `path` | URL path |
| `status` | HTTP response code |
| `duration_ms` | How long the request took |
| `exception` | Full traceback (only present on errors) |

**Request log example:**
```json
{"timestamp": "2026-04-05T01:42:09Z", "level": "INFO", "message": "request",
 "method": "POST", "path": "/shorten", "status": 201, "duration_ms": 12.4}
```

**Error log example:**
```json
{"timestamp": "2026-04-05T01:42:09Z", "level": "ERROR", "message": "request",
 "method": "GET", "path": "/urls", "status": 500, "duration_ms": 3.1,
 "exception": "OperationalError: ..."}
```

---

## Severity Guide

| Signal | Severity | Action |
|--------|----------|--------|
| `/health` returning 200 | ✅ Normal | None |
| Error rate < 5% | ✅ Normal | None |
| Error rate 5–50% | ⚠️ Degraded | Investigate logs |
| Error rate > 50% | 🔴 Critical | Page on-call, follow High Error Rate runbook |
| `/health` not responding | 🔴 Critical | Follow Service Down runbook immediately |
| CPU > 90% sustained | ⚠️ Warning | Check for traffic spike or infinite loop |
| Memory > 90% | ⚠️ Warning | Restart app, investigate memory leak |

---

## Contacts

| Role | Name |
|------|------|
| On-call | @devs (Discord) |
| Alerts channel | #watchtower-alerts |
