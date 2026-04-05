# Watchtower

A production-grade observability and incident response platform built on top of a URL shortener service. Watchtower monitors your application in real-time, fires intelligent alerts, injects chaos to prove resilience, and automatically heals itself when things go wrong.

**Built for the MLH Production Engineering Hackathon — Incident Response Quest Track.**

**Stack:** Flask, Peewee ORM, PostgreSQL, Discord Webhooks, psutil

---

## Architecture

Watchtower runs as **two independent processes** — so the monitoring plane survives even if the application crashes.

```
┌──────────────────────────────┐     ┌─────────────────────────────────────┐
│   Flask App (port 5001)      │     │   Monitor Process (port 5002)       │
│                              │     │                                     │
│  URL Shortener API           │◄────│  Health checks (every 15s)          │
│  /shorten, /urls, /<code>    │     │  Error rate detection               │
│                              │     │  SLO burn rate analysis              │
│  Observability API           │     │  Self-healing (auto-restart)        │
│  /health, /metrics, /logs    │     │                                     │
│  /slo, /incidents            │     │  Serves UI (survives app crash):    │
│  /error-classification       │     │   • Public Status Page (/)          │
│                              │     │   • Internal Dashboard (/dashboard) │
│  Chaos Engineering API       │     │   • Incident Runbook (/runbook)     │
│  /chaos/*                    │     │                                     │
│                              │     │  Discord Alert Dispatch             │
└──────────────────────────────┘     └─────────────────────────────────────┘
          ▲                                        │
          │              ┌───────────┐             │
          └──────────────│ PostgreSQL │◄────────────┘
                         └───────────┘
```

---

## Features

### Monitoring & Alerting
- **Health checks** — polls `/health` every 15s, detects service downtime and recovery
- **Error rate monitoring** — watches for 5xx spikes above 50% in a 2-minute window
- **SLO burn rate alerting** — calculates error budget consumption rate using Google SRE's 14.4x threshold
- **Discord webhook alerts** — sends rich embeds with role pings for Service Down, Recovery, High Error Rate, and Burn Rate Critical events
- **Request tracing** — every request gets a unique `X-Request-ID` header for correlation

### Chaos Engineering
Inject real faults from the dashboard Chaos tab and watch the system detect and respond:
- **Latency Injection** — adds configurable delay to all user-facing requests
- **Error Storm** — randomly returns 500 errors at a configurable rate
- **Database Kill** — severs the PostgreSQL connection and prevents reconnection
- **CPU Stress** — burns CPU cycles for a configurable duration
- **Process Kill** — sends SIGTERM to the Flask process (triggers self-healing)
- **Traffic Generator** — generates synthetic requests so chaos effects are visible in metrics

### Self-Healing
- Detects consecutive health check failures, then automatically restarts the Flask process
- Respects cooldown (30s) and max retry limits (5 attempts)
- Sends Discord alerts for each remediation attempt
- Fires a final "Self-Healing Exhausted" alert if all attempts fail
- Logs all remediation actions to a ring buffer visible on the dashboard

### Dashboard (password-protected)
- **Overview Tab** — live status banner, CPU/RAM/response time/error rate metric rings, SLO widget (uptime, budget, burn rate), incident history, recent failures, error classification
- **Data Tab** — top endpoints, active URLs, live JSON log feed, events table
- **Chaos Tab** — activate/deactivate experiments, real-time chaos status bar, traffic generator, remediation log
- All panels poll every 2s for near real-time updates

### Public Status Page
- GitHub-style 30-day uptime history bars per component (API Server, Database, URL Shortener)
- Live operational status with uptime percentage
- Incident timeline with detection/resolution times and duration
- Accessible at `/` on port 5002 — stays online even when the app is down

### Incident Response Runbook
- Interactive runbook at `/dashboard/runbook` with step-by-step procedures for every alert type
- Covers: Service Down, High Error Rate, Database Unreachable, Port Conflicts
- Includes a log reading guide and severity classification table

---

## Quick Start

### Prerequisites
- **Python 3.11+**
- **PostgreSQL** running locally
- **uv** — [install here](https://docs.astral.sh/uv/getting-started/installation/)

### Setup

```bash
# 1. Clone the repo
git clone <repo-url> && cd pe-watchtower

# 2. Install dependencies
uv sync

# 3. Create the database
createdb hackathon_db

# 4. Configure environment
cp .env.example .env
# Edit .env with your Discord webhook URL and DB credentials

# 5. Seed the database (optional)
uv run python seed.py
```

### Running

You need **two terminals**:

```bash
# Terminal 1 — Flask app (port 5001)
uv run python run.py

# Terminal 2 — Monitor + UI (port 5002)
uv run python monitor.py
```

### Access

| What | URL |
|------|-----|
| API Health Check | http://localhost:5001/health |
| Shorten a URL | `POST http://localhost:5001/shorten` with `{"url": "..."}` |
| Public Status Page | http://localhost:5002 |
| Internal Dashboard | http://localhost:5002/dashboard (password: `admin`) |
| Incident Runbook | http://localhost:5002/dashboard/runbook |

---

## Demo Script

This is the recommended sequence for demonstrating the full incident lifecycle:

1. Start both processes (`run.py` + `monitor.py`)
2. Open the dashboard at `localhost:5002/dashboard`
3. Go to the **Chaos** tab
4. Click **Generate Traffic** to create background load
5. Click **Error Storm** — watch the Overview tab light up:
   - Error rate ring spikes red
   - SLO burn rate climbs
   - Recent Failures fills with 500s
   - Discord fires a High Error Rate alert
6. Click **Kill Process** — watch self-healing kick in:
   - Status banner goes red
   - Discord fires Service Down alert
   - Remediation log shows restart attempts
   - Monitor auto-restarts Flask
   - Discord fires Service Recovered alert
   - Status banner goes green again
7. Open the public status page at `localhost:5002` — it stayed online the whole time

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_NAME` | `hackathon_db` | PostgreSQL database name |
| `DATABASE_HOST` | `localhost` | Database host |
| `DATABASE_PORT` | `5432` | Database port |
| `DATABASE_USER` | `postgres` | Database user |
| `DATABASE_PASSWORD` | `postgres` | Database password |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook for alerts |
| `DISCORD_ALERT_ROLE_ID` | — | Discord role ID to ping on alerts |
| `DASHBOARD_PASSWORD` | `admin` | Dashboard login password |
| `DASHBOARD_SECRET` | `dashboard-dev-secret-change-me` | Cookie signing secret |
| `MONITOR_INTERVAL` | `15` | Seconds between health checks |
| `MONITOR_PORT` | `5002` | Port for monitor UI |
| `APP_URL` | `http://localhost:5001` | Base URL of the Flask app |

---

## API Reference

### URL Shortener
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/shorten` | Create a short URL (`{"url": "...", "user_id": N}`) |
| `GET` | `/<code>` | Redirect to original URL |
| `GET` | `/urls` | List all URLs |
| `GET` | `/urls/<id>` | Get URL details |
| `PUT` | `/urls/<id>` | Update a URL |
| `DELETE` | `/urls/<id>` | Delete a URL |

### Observability
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check (pings DB) |
| `GET` | `/metrics` | CPU, RAM, process stats |
| `GET` | `/logs` | JSON log ring buffer |
| `GET` | `/slo` | 24h uptime SLO stats |
| `GET` | `/incidents` | Last 50 incidents |
| `GET` | `/uptime-history` | 30-day per-day uptime |
| `GET` | `/error-classification` | Categorized 500 errors |

### Chaos Engineering
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/chaos/status` | Current chaos state |
| `POST` | `/chaos/latency` | Inject latency (`{"delay_ms": 2000}`) |
| `POST` | `/chaos/error-rate` | Inject errors (`{"rate": 0.5}`) |
| `POST` | `/chaos/db-kill` | Kill DB connection |
| `POST` | `/chaos/cpu-stress` | Burn CPU (`{"duration_s": 30}`) |
| `POST` | `/chaos/crash` | Kill Flask process |
| `POST` | `/chaos/traffic` | Generate load (`{"duration_s": 60, "rps": 5}`) |
| `POST` | `/chaos/clear` | Stop all experiments |

---

## Project Structure

```
pe-watchtower/
├── app/
│   ├── __init__.py              # App factory, request tracing, health endpoint
│   ├── database.py              # Peewee DatabaseProxy, connection hooks
│   ├── logging_config.py        # JSON structured logging with ring buffer
│   ├── alerting.py              # Alert utilities
│   ├── models/
│   │   ├── url.py               # URL model
│   │   ├── user.py              # User model
│   │   ├── event.py             # Event model (URL lifecycle events)
│   │   └── incident.py          # Incident model (recorded by monitor)
│   ├── routes/
│   │   ├── urls.py              # URL shortener CRUD + redirect
│   │   ├── users.py             # User management
│   │   ├── events.py            # Event history
│   │   ├── observability.py     # /metrics, /logs, /slo, /incidents, /error-classification
│   │   ├── chaos.py             # Chaos engineering endpoints + middleware
│   │   └── dashboard.py         # Dashboard blueprint (placeholder)
│   └── templates/
│       ├── status.html          # Public status page
│       ├── dashboard.html       # Internal dashboard (Overview/Data/Chaos tabs)
│       ├── login.html           # Dashboard login
│       └── runbook.html         # Interactive incident runbook
├── monitor.py                   # Standalone monitor process (alerting, self-healing, UI server)
├── run.py                       # Flask app entry point
├── seed.py                      # Database seeder
├── RUNBOOK.md                   # Markdown incident response guide
├── pyproject.toml               # Dependencies (managed by uv)
└── .env.example                 # Environment variable template
```
