# VoxOS

VoxOS is a voice-first, multilingual, permissioned autonomous desktop AI agent.
It converts text or voice requests into a planned sequence of registered tools,
checks each action through a risk engine, and exposes the workflow through a
FastAPI API and a lightweight dashboard.

## Architecture

- `voxos/api`: FastAPI routes, authentication dependencies, and WebSocket events.
- `voxos/core`: task orchestration, permissions, risk classification, events, and persistence.
- `voxos/agents`: listener, strategist, executor, and overseer pipeline stages.
- `voxos/tools`: filesystem, shell, browser, desktop, memory, and code tools.
- `apps/frontend`: static dashboard served separately from the API.
- PostgreSQL stores task/audit data; Redis is used for event distribution when available.

High-risk actions require approval. Critical shell/filesystem patterns are blocked.
Production API and WebSocket routes require a Bearer JWT; development mode keeps
the local dashboard convenient while still enforcing tool permissions.

## Quick start

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows:    .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cp .env.example .env  # PowerShell: Copy-Item .env.example .env
python -m voxos
```

API: `http://localhost:8000`
OpenAPI docs (development): `http://localhost:8000/docs`
Health: `http://localhost:8000/health`

For a no-Docker setup, use a managed PostgreSQL provider such as Neon and a
managed Redis provider such as Upstash. Set `POSTGRES_URL` to the provider's
PostgreSQL connection string and `REDIS_URL` to the provider's native Redis
TCP/TLS connection string. The Upstash REST URL/token pair is for an HTTP Redis
client and is not used by VoxOS's Redis Pub/Sub event bus. The API can still
start in development when Redis is unavailable; cross-process events are then
disabled.

Run the database migration once after configuring the cloud PostgreSQL URL:

```bash
python -m alembic upgrade head
```

The Docker Compose file remains available only as an optional local development
alternative.

## Tests and checks

```bash
pytest tests/ -q
ruff check voxos tests
mypy voxos
```

Use `make dev`, `make test`, or `make docker-up` if GNU Make is available.

## Configuration

`.env.example` lists the supported settings. Keep `.env` out of version control.
Before production deployment, set strong `JARVIS_SECRET_KEY` and `JWT_SECRET_KEY`
values, provide an LLM key, configure PostgreSQL/Redis, and set `JARVIS_ENV=production`.
