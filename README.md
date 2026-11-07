# 🛡️ Multi-Tenant Distributed API Gateway

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-async-teal)
![Redis](https://img.shields.io/badge/Redis-state%20store-red)
![License](https://img.shields.io/badge/License-MIT-green)

An async, production-style API Gateway built with **FastAPI**, **Redis**, and **HTTPX** — providing centralized authentication, per-tenant rate limiting/quotas, request tracing, resilient reverse-proxying, and structured observability at the edge of a microservices architecture.

Each core capability (authentication, quotas, rate limiting, retries, circuit breaking, tracing, and logging) was implemented manually to understand how modern API gateways work internally.

---

## 🤔 Why This Project?

Modern backend systems rarely expose services directly to clients. An API Gateway centralizes authentication, request routing, traffic policies, resiliency, and observability before requests ever reach downstream services.

This project explores how those mechanisms — the same ones found in tools like Kong, Envoy, or Traefik — can be implemented manually using FastAPI, Redis, and HTTPX, in order to understand what's actually happening underneath a production gateway.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🔑 **API Key Authentication** | Resolves `X-API-Key` to a tenant profile via Redis |
| 🛡️ **Security Hardening** | Security headers, host allow-list, auth-failure throttling, SSRF/path-escape guards |
| 🏢 **Multi-Tenancy** | Tenant metadata (plan, quota, limits) isolated in Redis Hashes |
| 📊 **Quota Management** | Atomic check-and-reserve monthly usage (Lua), refunds non-billable requests, returns `403` on breach |
| 🚦 **Rate Limiting** | Sliding-window limiter in Redis, returns `429` on burst traffic |
| 🔄 **Reverse Proxy** | Async reverse proxy via HTTPX with configurable connect/read timeouts |
| 🔁 **Idempotent Retries** | Exponential backoff, restricted to safe methods (GET/HEAD/OPTIONS) |
| ⚡ **Circuit Breaker** | Closed → Open → Half-Open state machine to fail fast on downstream outages |
| 🔍 **Distributed Tracing** | `X-Request-ID` generated/propagated across all logs |
| 🧾 **Structured Logging** | Async, zero-latency usage + security audit logs via `BackgroundTasks` |
| 📈 **Metrics** | Prometheus text endpoint with counters, uptime, and breaker state |

---

## 🏗️ Architecture

```
Client
  │
  ▼
[Request ID Middleware] → generates/propagates X-Request-ID
  │
  ▼
[Auth Layer] → API Key → Tenant lookup (Redis)
  │
  ▼
[Rate Limiter] → sliding window (Redis, rejected requests are not counted)
  │
  ▼
[Quota Check] → monthly usage (Redis Lua, reserve + refund)
  │
  ▼
[Circuit Breaker] → fail fast if downstream is OPEN
  │
  ▼
[Reverse Proxy] → HTTPX forward with timeouts + retries
  │
  ▼
[Background Tasks] → usage analytics + audit logging
  │
  ▼
Response (200 / 401 / 403 / 404 / 413 / 429 / 500 / 502 / 503 / 504)
```

---

## 🧰 Tech Stack

- **FastAPI** — async web framework
- **HTTPX** — async HTTP client for proxying
- **Redis** — atomic, low-latency state store for tenants, quotas, rate limits
- **Uvicorn** — ASGI server
- **Locust** — load testing

---

## 📁 Project Structure

```
api-gateway/
├── app/
│   ├── main.py                     # App entrypoint & route wiring
│   ├── auth/
│   │   └── tenant_auth.py          # API key → tenant resolution
│   ├── core/
│   │   ├── redis_client.py         # Redis connection
│   │   ├── quota.py                # Monthly quota enforcement
│   │   ├── rate_limiter.py         # Sliding-window rate limiting
│   │   ├── circuit_breaker.py      # Breaker state machine
│   │   ├── logger.py               # Structured JSON logger
│   │   ├── metrics.py              # Prometheus counters + breaker gauges
│   │   └── background_logging.py  # Usage + audit log tasks
│   ├── middleware/
│   │   ├── request_id.py           # X-Request-ID injection
│   │   └── security_headers.py     # Security + cache headers
│   ├── models.py                   # Pydantic response schemas
│   ├── proxy/
│   │   ├── forwarder.py            # HTTPX reverse proxy + timeouts
│   │   └── retry.py                # Retry + backoff + breaker integration
│   └── config.py                   # Environment-driven settings
├── scripts/
│   ├── seed_tenants.py             # Development tenant seeding
│   └── seed_loadtest.py            # High-limit tenant for Locust
├── tests/
│   ├── conftest.py                 # Fake Redis + breaker fixtures
│   ├── locustfile.py               # Load testing
│   └── test_gateway.py             # Unit/integration tests
├── .env.example
├── .github/workflows/ci.yml       # Test CI
├── .gitignore
├── Dockerfile
├── .dockerignore
├── requirements.txt
├── requirements-dev.txt
├── docker-compose.yml
├── LICENSE
└── README.md
```

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Redis (local or Docker)

### Installation

```bash
git clone https://github.com/<your-username>/api-gateway.git
cd api-gateway

python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt

# For tests and load testing:
pip install -r requirements-dev.txt
```

### Run Redis

```bash
docker compose up -d redis
```

### Seed Tenant Data

```bash
python -m scripts.seed_tenants
# or: python scripts/seed_tenants.py
```

### Configuration

All settings are environment-driven and use the `GATEWAY_` prefix. Copy
`.env.example` to `.env` and adjust:

| Variable | Default | Purpose |
|---|---|---|
| `GATEWAY_REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `GATEWAY_BACKENDS` | `{"users":"http://localhost:9001","orders":"http://localhost:9002"}` | Service routing table |
| `GATEWAY_PROXY_TOTAL_TIMEOUT` | `10.0` | Hard deadline per client request, including retries |
| `GATEWAY_MAX_REQUEST_BODY_BYTES` | `10485760` | Maximum buffered request body (10 MiB) |
| `GATEWAY_RETRY_MAX_ATTEMPTS` | `2` | Retries for idempotent methods |
| `GATEWAY_BREAKER_FAILURE_THRESHOLD` | `5` | Failures before the circuit opens |
| `GATEWAY_BREAKER_RECOVERY_TIMEOUT` | `30.0` | Seconds before a half-open probe |
| `GATEWAY_AUTH_FAILURE_LIMIT` | `20` | Invalid API keys allowed per IP before `429` |
| `GATEWAY_AUTH_FAILURE_WINDOW_SECONDS` | `60` | Sliding window for auth-failure counting |
| `GATEWAY_ALLOWED_HOSTS` | `["*"]` | JSON array of accepted `Host` headers; `["*"]` disables the check |
| `GATEWAY_CORS_ORIGINS` | `[]` | JSON array; empty disables CORS |

### Run the Gateway

```bash
uvicorn app.main:app --reload --port 8000
```

### Run demo downstream services

The gateway routes `users` to port `9001` and `orders` to `9002` by default.
Start the bundled demo backend twice:

```bash
uvicorn app.backend:app --port 9001
uvicorn app.backend:app --port 9002
```

### Run everything with Docker

```bash
docker compose up -d --build
docker compose exec gateway python -m scripts.seed_tenants
```

The compose file points `host.docker.internal` at the host, so run the demo
backends on the host as shown above (or override `GATEWAY_BACKENDS`).

### Test a Request

```bash
curl -i http://localhost:8000/api/v1/users/test \
  -H "X-API-Key: key-abc123"
```

### Run the tests

```bash
pytest
ruff check app tests scripts
pip-audit -r requirements.txt
```

---

## 🖼️ Screenshots

### 🏗️ Architecture

![Architecture](pictures/architecture.png)

---

### 📈 Locust Load Test

**Statistics**

![Locust Results](pictures/locus-stats.png)

**Performance Charts**

![Locust Charts](pictures/locus-charts.png)

---

### ✅ Successful Request

![Successful Request](pictures/successfull.png)

**Structured Usage Log**

![Usage Log](pictures/success-terminal.png)

---

### ❌ Unauthorized Request

![Unauthorized Request](pictures/unsuccessfull.png)

**Structured Audit Log**

![Audit Log](pictures/unsuccess-terminal.png)

## ⚡ Performance

*Measured with Locust on a single developer laptop, with the gateway running in
Docker and the demo backend on the host. These numbers are indicative, not a
production benchmark (load generator and gateway share the same CPU).*

**Test machine**

| Component | Specification |
|---|---|
| Machine | Lenovo 83DV |
| CPU | 12th Gen Intel Core i5-12450HX — 8 cores / 12 threads, 2.4 GHz base |
| RAM | 16 GB (15.7 GB usable) |
| OS | Windows 11 Pro, build 26200, 64-bit |
| Runtime | Single Uvicorn process in Docker Desktop; Redis 7 in Docker; backend on host |

**Latest verification run (Nov 2026)**

| Metric | Result |
|---|---|
| Load test tool | Locust 2.44.4, 20 concurrent users, spawn rate 5/s, 30 s |
| Client wait time | 10–100 ms between requests |
| Total requests | 6,110 |
| Failures | 0 (0.00%) |
| Median latency | 34 ms |
| 95th percentile | 74 ms |
| 99th percentile | 250 ms |
| Average latency | 35 ms |
| Sustained throughput | 207 RPS |

The screenshots above are from an earlier 20-user run on the same laptop
(1,275 requests, p95 210 ms, ~98 RPS); the gateway has since been optimised to
stream responses and reject unknown services before metering.

> Note: an earlier run under a misconfigured test (invalid API key on all requests) showed a ~96% failure rate — this was a test-setup issue, not a gateway fault, and is a good example of the gateway correctly rejecting unauthenticated load rather than passing it through.

---

## 🧪 Testing

```bash
pytest
```

The suite uses `fakeredis` (with Lua support via `lupa`) and covers:

- rejected requests do not grow the rate-limit window
- quota reservations are capped and refunded for non-billable responses
- HALF_OPEN allows only one probe
- backend 5xx responses open the circuit
- 502/504 retries are limited to idempotent methods
- the gateway strips `X-API-Key` before proxying
- compressed response bodies are decoded without leaving `Content-Encoding`
- unknown services are rejected before consuming rate limit/quota
- auth failures return `401` and are throttled per IP
- repeated invalid keys return `429` while valid keys stay unaffected
- security headers are present on responses
- absolute URLs, backslashes, and base-path escapes are rejected by the proxy
- backend URLs are validated (no credentials, query, or fragment)
- `X-Request-ID` is validated and echoed

---

## 📈 Observability

- `GET /health` — liveness probe
- `GET /ready` — readiness probe (pings Redis)
- `GET /metrics` — Prometheus text exposition with request, failure, rate-limit, quota, retry, circuit-breaker, and uptime metrics

All request logs are JSON lines containing `request_id`, `tenant_id`, `method`, `path`, and `status_code` where available.

### API documentation

FastAPI generates an OpenAPI 3.1 schema with Swagger UI at `/docs`, ReDoc at
`/redoc`, and the raw schema at `/openapi.json`. Configuration is modelled with
`pydantic-settings`, and the health/readiness responses use Pydantic models.
The catch-all proxy route is intentionally hidden from the schema because it
forwards arbitrary downstream payloads.

---

## 🔒 Security

- **API-key authentication** — `X-API-Key` is resolved to a Redis tenant hash;
  missing/invalid keys return `401`, Redis outages return `503`.
- **Auth-failure throttling** — invalid attempts are counted per client IP in
  Redis (`GATEWAY_AUTH_FAILURE_LIMIT` per
  `GATEWAY_AUTH_FAILURE_WINDOW_SECONDS`). Exceeding the budget returns `429`
  with `Retry-After`; valid keys are never throttled and reset the counter.
- **SSRF and path-escape guards** — backend URLs must be absolute `http(s)` with
  no embedded credentials, query, or fragment (validated at startup). Request
  paths are resolved with `urljoin`, then the scheme/host/port must still match
  the configured backend and the path cannot escape a configured base path.
  Backslashes and percent-encoded dots are rejected.
- **Header hygiene** — `X-API-Key` and hop-by-hop headers are stripped before
  proxying; `X-Forwarded-For` is derived from the transport peer and never
  trusted from the client; `X-Request-ID` is validated against
  `[A-Za-z0-9._:-]{1,128}`.
- **Request limits** — request bodies are capped at 10 MiB (`413`); responses
  are streamed rather than buffered.
- **Security headers** — `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Opener-Policy: same-origin`, `Cache-Control: no-store` for
  probes/metrics, and HSTS when the request arrives over HTTPS.
- **Host allow-list** — optional `TrustedHostMiddleware` via
  `GATEWAY_ALLOWED_HOSTS`, which mitigates host-header injection.
- **Error hygiene** — clients receive a generic
  `{"detail": ..., "request_id": ...}` body; tracebacks stay in server logs.
- **Dependencies** — pinned and audited with
  `pip-audit -r requirements.txt` (no known vulnerabilities as of Nov 2026).

Operational notes: terminate TLS at a trusted edge proxy, keep Redis on a
private network, store API keys in a secret manager, and rotate them. The demo
keys in `scripts/seed_*.py` are for local development only.

---

## 🧪 Load Testing

```bash
locust -f tests/locustfile.py --host=http://localhost:8000
```

Open `http://localhost:8089` to configure concurrent users and monitor latency, error rates, and circuit breaker behavior under load.

---

## 📡 API Behavior Reference

| Scenario | Response |
|---|---|
| Missing/invalid API key | `401 Unauthorized` |
| Repeated invalid API keys | `429 Too Many Requests` (+ `Retry-After`) |
| Monthly quota exceeded | `403 Forbidden` |
| Rate limit exceeded | `429 Too Many Requests` |
| Unknown service | `404 Not Found` |
| Downstream circuit open | `503 Service Unavailable` |
| Redis unavailable | `503 Service Unavailable` |
| Backend connection failure | `502 Bad Gateway` |
| Downstream timeout after retries | `504 Gateway Timeout` |
| Request body too large | `413 Payload Too Large` |
| Successful proxy | `200 OK` (+ backend response) |

Every response includes an `X-Request-ID` header. Successful responses also
include `X-RateLimit-*` and (when billable) `X-Quota-*` headers; `429`/`503`
responses include `Retry-After` where applicable.

---

## ⚠️ Known Limitations & Future Work

- **Redis is a single point of failure** for tenant lookup, quotas, and rate limiting. Failures currently fail closed with `503`; a configurable fail-open mode is not implemented.
- **Circuit breaker state is in-memory per process** — running multiple gateway instances requires moving this into Redis, or explicitly accepting independent per-instance tripping.
- **Request bodies are buffered in memory** up to `GATEWAY_MAX_REQUEST_BODY_BYTES` (10 MiB by default); responses are streamed back to the client. Streaming request uploads is future work.
- **API key auth has no rotation, hashing, or revocation** — keys are stored in plaintext Redis keys and act as long-lived bearer credentials.
- **Auth-failure throttling keys on the transport peer address** — behind a trusted load balancer every client may appear as the proxy IP unless proxy trust is configured; consider wiring this to a verified client-IP source in production.
- **Tenant onboarding is manual** via `scripts/seed_tenants.py` — no admin API yet.
- **No Redis Cluster/Sentinel** — sufficient for local/single-instance use, not for horizontal scaling.
- **Metrics are in-process counters** — they reset on restart and are not aggregated across gateway instances.

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## 🏷️ Topics

`api-gateway` `fastapi` `redis` `python` `microservices` `reverse-proxy` `distributed-systems` `backend` `rate-limiter` `circuit-breaker` `httpx`

---

## 🙋 Author

Built by [Your Name] to explore the core building blocks of modern API Gateways — authentication, multi-tenancy, traffic management, resiliency, and observability — using FastAPI, Redis, and HTTPX.
