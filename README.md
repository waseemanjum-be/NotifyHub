# NotifyHub (FastAPI + Motor/Mongo)

NotifyHub is a production-ready notification service built with **FastAPI**, **Motor (async MongoDB)**, config-driven provider integrations (EMAIL/SMS/PUSH), delivery tracking (QUEUED → SENT → DELIVERED → READ), retries with exponential backoff, idempotency, caching (LRU/Memcached), and a separate delivery worker.

---

## Contents

- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [Run Locally (venv)](#run-locally-venv)
- [Run Using pyproject.toml (packaged)](#run-using-pyprojecttoml-packaged)
- [Run with Docker Compose](#run-with-docker-compose)
- [Production Run (recommended)](#production-run-recommended)
- [Dev Seeder](#dev-seeder)
- [Worker](#worker)
- [Postman](#postman)
- [Notes](#notes)

---

## Prerequisites

- Python **3.13**
- MongoDB (local) or Docker
- (Optional) Memcached

---

## Configuration

Configuration is loaded from environment variables (and `.env` if present).

### Required
- `ENV`  
  - `dev` enables dev-only seeding script behavior
- `MONGODB_URI`  
  - Local: `mongodb://localhost:27017`  
  - Docker: `mongodb://mongo:27017`
- `MONGODB_DB`  
  - Example: `notifyhub`

### Providers (config-driven)
Set base URLs and API keys per channel. **No mock logic exists in code**—switching providers is config-only.

- `EMAIL_PROVIDER_BASE_URL`
- `EMAIL_PROVIDER_API_KEY`
- `SMS_PROVIDER_BASE_URL`
- `SMS_PROVIDER_API_KEY`
- `PUSH_PROVIDER_BASE_URL`
- `PUSH_PROVIDER_API_KEY`
- `PROVIDER_TIMEOUT_MS` (default 5000)
- `PROVIDER_RETRYABLE_STATUS_CODES` (comma-separated)  
  Example: `408,429,500,502,503,504`

### Provider callbacks (optional)
If set, NotifyHub will require a matching header for callback endpoint:
- `PROVIDER_CALLBACK_TOKEN`

Header name:
- `X-Provider-Token: <token>`

### Caching
- `CACHE_BACKEND` = `none | lru | memcache`
- `CACHE_TTL_SECONDS` = seconds for cached lookups (users/templates)

Memcache settings (only used if `CACHE_BACKEND=memcache`):
- `MEMCACHE_HOST`
- `MEMCACHE_PORT`
- `MEMCACHE_TIMEOUT_MS`

### CORS
- `CORS_ORIGINS` comma-separated  
  Example: `http://localhost:3000,http://127.0.0.1:3000`

---

## Run Locally (venv)

### 1) Create `.env`
Create a `.env` file in the project root:

```env
ENV=dev
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=notifyhub

CACHE_BACKEND=lru
CACHE_TTL_SECONDS=300

CORS_ORIGINS=http://localhost:3000

# Providers (set your own URLs)
EMAIL_PROVIDER_BASE_URL=http://localhost:9001
EMAIL_PROVIDER_API_KEY=
SMS_PROVIDER_BASE_URL=http://localhost:9002
SMS_PROVIDER_API_KEY=
PUSH_PROVIDER_BASE_URL=http://localhost:9003
PUSH_PROVIDER_API_KEY=

PROVIDER_TIMEOUT_MS=5000
PROVIDER_RETRYABLE_STATUS_CODES=408,429,500,502,503,504

# Optional callback protection
PROVIDER_CALLBACK_TOKEN=
```

### 2) Install dependencies
If using `requirements.txt`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### 3) Start the API
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 4) Verify health
```bash
curl http://localhost:8000/health
```

---

## Run Using pyproject.toml (packaged)

This is useful if you want a clean install workflow (editable or wheel-based).

### 1) Editable install (recommended for dev)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

### 2) Run API
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3) Build a wheel (optional)
```bash
pip install -U build
python -m build
```

Artifacts appear in `dist/`.

### 4) Install from wheel (deployment-style)
```bash
pip install dist/notifyhub-0.1.0-py3-none-any.whl
```

Then run as usual:
```bash
gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000 --workers 2
```

---

## Run with Docker Compose

### 1) Ensure `.env` exists
For Docker, set:
```env
MONGODB_URI=mongodb://mongo:27017
```

### 2) Start services
```bash
docker compose up --build
```

This runs:
- `mongo`
- `api`
- `worker`

### Optional: enable Memcached
```bash
docker compose --profile memcache up --build
```

Also set:
```env
CACHE_BACKEND=memcache
MEMCACHE_HOST=memcache
MEMCACHE_PORT=11211
```

### Verify
```bash
curl http://localhost:8000/health
```

---

## Production Run (recommended)

### Recommended process model
- Run **API** as a web service (gunicorn + uvicorn workers).
- Run **Worker** as a separate process/service using the same image/codebase.

### API (production command)
```bash
gunicorn -k uvicorn.workers.UvicornWorker app.main:app \
  --bind 0.0.0.0:8000 \
  --workers 2 \
  --timeout 30 \
  --graceful-timeout 30
```

### Worker (production command)
```bash
python -m app.workers.delivery_worker
```

### Production configuration notes
- Use `CACHE_BACKEND=memcache` when running multiple API/worker instances.
- Ensure MongoDB is reachable and stable.
- Configure provider base URLs to point at real provider services.
- Keep provider simulation (if any) **external** and selected only by config.

---

## Dev Seeder

A dev-only seeder exists at:

- `app/scripts/seed_dev_data.py`

It **only runs when `ENV=dev`**.

### Seed dev data
```bash
export ENV=dev
export MONGODB_URI="mongodb://localhost:27017"
export MONGODB_DB="notifyhub"
python app/scripts/seed_dev_data.py
```

---

## Worker

The worker is the delivery engine:
- Claims due work atomically from Mongo
- Calls configured providers
- Writes `delivery_attempts`
- Updates per-channel status
- Retries with exponential backoff

### Run worker locally
```bash
python -m app.workers.delivery_worker
```

---

## Postman

Import the provided Postman collection JSON into Postman.

Important:
- `user_id` and `template_id` must match what exists in your DB:
  - If you use seeded data: set `userId=user_001`, `templateId=tpl_001`
  - If you use Mongo `_id`: pass the raw 24-char hex string (not `ObjectId(...)`)

---

## Notes

- Do **not** install the standalone `bson` package; it conflicts with PyMongo’s built-in bson.
- Provider mocking (if used in local dev) must be an **external service** and configured through `*_PROVIDER_BASE_URL`.
- Index bootstrap runs safely on startup (idempotent).