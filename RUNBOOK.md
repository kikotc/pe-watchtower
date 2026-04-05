# Watchtower — Incident Response Runbook

This is the "In Case of Emergency" guide for the Watchtower URL shortener service.
Use this runbook whenever an alert fires or something feels wrong.

---

## Alerting Latency & Response Objectives

| Metric | Value | Detail |
|--------|-------|--------|
| **Check interval** | 15 seconds | Monitor polls `/health` and `/logs` every 15s |
| **Worst-case detection latency** | 15 seconds | An incident occurring right after a check is detected on the next cycle |
| **Typical detection latency** | ~7.5 seconds | Average half-interval |
| **Discord alert dispatch** | < 1 second | Webhook POST after detection |
| **Total alerting latency** | < 16 seconds | Well within the 5-minute response objective |
| **Self-healing trigger** | ~30 seconds | After 2 consecutive failed checks (2 x 15s) |
| **Alert cooldown** | 5 minutes | Same alert type suppressed for 300s to prevent spam |
| **Self-healing cooldown** | 30 seconds | Between restart attempts |
| **Max restart attempts** | 5 | Before manual intervention alert fires |

The monitor process runs independently of the Flask application on a separate port (5002), ensuring alerting continues even during complete application outages.

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

## Alert: 🔥 SLO Burn Rate Critical

**What it means:** Error budget is being consumed faster than 14.4x the expected rate (Google SRE threshold). At this pace, the monthly SLO budget will be exhausted in under 2 days.

### Step 1 — Check the dashboard SLO widget
Open the dashboard Overview tab. Look at the Burn Rate (1h) and Burn Rate (6h) values. If only 1h is spiking but 6h is low, it may be a brief incident. If both are high, it's a sustained problem.

### Step 2 — Identify the error source
Check the Error Classification panel and Recent Failures panel on the dashboard. Look for patterns:
- All errors from one endpoint → that route is broken
- Mix of errors → systemic issue (database, dependency)

### Step 3 — Fix and verify
Fix the root cause then watch the SLO widget. Burn rate should drop below 1.0x once errors stop.

---

## Alert: 🔧 Auto-Remediation / Self-Healing

**What it means:** The monitor detected the Flask app was down for 2+ consecutive checks and automatically attempted to restart it.

### How it works
1. After 2 consecutive failed health checks (~30s), monitor spawns a new Flask process
2. Waits 30 seconds between attempts (cooldown)
3. Tries up to 5 times before giving up and firing a "Self-Healing Exhausted" alert
4. Output from the restarted process goes to `logs/self-heal.log`

### If self-healing succeeded
No action needed. Check the Remediation Log on the Chaos tab to see what happened. A Recovery alert will fire on Discord.

### If self-healing exhausted (5 failed attempts)
Manual intervention required:
```bash
# Check what's preventing startup
cat logs/self-heal.log

# Common causes:
# - Port 5001 still in use by zombie process
lsof -i :5001
kill -9 $(lsof -ti:5001)

# - Database down
docker start postgres

# - Dependency issue
uv sync

# Then manually restart
uv run python run.py
```

---

## Chaos Engineering

The Chaos tab on the dashboard lets you inject faults to test monitoring and self-healing. Available experiments:

| Experiment | What it does | Detected by |
|------------|-------------|-------------|
| Latency Injection | Adds 2s delay to user-facing requests | Response Time metric ring, p99 charts |
| Error Storm | Returns 500 on ~50% of user-facing requests | Error Rate alert, SLO burn rate, Recent Failures |
| Database Kill | Severs DB connection, prevents reconnect | Health check (degraded), Error Classification |
| CPU Stress | Burns CPU cycles | CPU metric ring |
| Kill Process | Sends SIGTERM to Flask | Service Down alert, Self-Healing |

**Important:** Use "Generate Traffic" first so there are requests for chaos to affect. Observability endpoints (`/health`, `/metrics`, `/logs`) are excluded from chaos to keep monitoring working.

### Clearing experiments
Click "Deactivate" on individual experiments or "Clear All" to stop everything.

---

## Contacts

| Role | Name |
|------|------|
| On-call | @devs (Discord) |
| Alerts channel | #watchtower-alerts |
