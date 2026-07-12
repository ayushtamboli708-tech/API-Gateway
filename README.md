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
| 🏢 **Multi-Tenancy** | Tenant metadata (plan, quota, limits) isolated in Redis Hashes |
| 📊 **Quota Management** | Atomic monthly usage tracking (`HINCRBY`), returns `403` on breach |
| 🚦 **Rate Limiting** | Sliding-window limiter in Redis, returns `429` on burst traffic |
| 🔄 **Reverse Proxy** | Async reverse proxy via HTTPX with configurable connect/read timeouts |
| 🔁 **Idempotent Retries** | Exponential backoff, restricted to safe methods (GET/HEAD/OPTIONS) |
| ⚡ **Circuit Breaker** | Closed → Open → Half-Open state machine to fail fast on downstream outages |
| 🔍 **Distributed Tracing** | `X-Request-ID` generated/propagated across all logs |
| 🧾 **Structured Logging** | Async, zero-latency usage + security audit logs via `BackgroundTasks` |

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
[Quota Check] → monthly usage (Redis, atomic)
  │
  ▼
[Rate Limiter] → sliding window (Redis)
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
Response (200 / 403 / 429 / 503 / 504)
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
│   │   └── background_logging.py  # Usage + audit log tasks
│   ├── middleware/
│   │   └── request_id.py           # X-Request-ID injection
│   ├── proxy/
│   │   ├── forwarder.py            # HTTPX reverse proxy + timeouts
│   │   └── retry.py                # Retry + backoff + breaker integration
│   └── config.py                   # Environment-driven settings
├── scripts/
│   └── seed_tenants.py             # Redis tenant seeding script
├── tests/
│   ├── locustfile.py               # Load testing
│   └── test_gateway.py             # Unit/integration tests
├── .env
├── .gitignore
├── requirements.txt
├── docker-compose.yml
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
```

### Run Redis

```bash
docker run -d --name gateway-redis -p 6379:6379 redis:7-alpine
```

### Seed Tenant Data

```bash
python scripts/seed_tenants.py
```

### Run the Gateway

```bash
uvicorn app.main:app --reload --port 8000
```

### Test a Request

```bash
curl -i http://localhost:8000/api/v1/users/test \
  -H "X-API-Key: key-abc123"
```

---

## 🖼️ Screenshots

### Architecture
![Architecture](pictures\architecture.png)

### Locust Load Test
![Locust Results](pictures\locus-stats.png)
![Locust Charts](pictures\locus-charts.png)

### Structured Logs
**Usage log (successful request):**
![Usage Log](pictures\successfull.png)
![Usage Log](pictures\success-terminal.png)

**Audit log (blocked request — missing API key):**
![Audit Log](pictures\unsuccessfull.png)
![Audit Log](pictures\unsuccess-terminal.png)

---

## ⚡ Performance

*Measured locally on a single developer machine, single gateway process, local backend.*

| Metric | Result |
|---|---|
| Load test tool | Locust, 20 concurrent users |
| Total requests | 1,275 |
| Failures | 0 |
| Median latency | 130 ms |
| 95th percentile | 210 ms |
| 99th percentile | 2,400 ms |
| Average latency | 175 ms |
| Sustained throughput | ~98 RPS |
| Request tracing | Consistent `X-Request-ID` across gateway + backend logs |
| Background logging overhead | Negligible (async, non-blocking) |
| Circuit breaker | Verified Closed → Open → Half-Open → Closed cycle under induced backend failure |

> Note: an earlier run under a misconfigured test (invalid API key on all requests) showed a ~96% failure rate — this was a test-setup issue, not a gateway fault, and is a good example of the gateway correctly rejecting unauthenticated load rather than passing it through.

---

## 🧪 Load Testing

```bash
pip install locust
locust -f tests/locustfile.py --host=http://localhost:8000
```

Open `http://localhost:8089` to configure concurrent users and monitor latency, error rates, and circuit breaker behavior under load.

---

## 📡 API Behavior Reference

| Scenario | Response |
|---|---|
| Missing/invalid API key | `401 Unauthorized` |
| Monthly quota exceeded | `403 Forbidden` |
| Rate limit exceeded | `429 Too Many Requests` |
| Downstream circuit open | `503 Service Unavailable` |
| Downstream timeout after retries | `504 Gateway Timeout` |
| Successful proxy | `200 OK` (+ backend response) |

Every response includes an `X-Request-ID` header for tracing across services.

---

## ⚠️ Known Limitations & Future Work

- **Redis is a single point of failure** for tenant lookup, quotas, and rate limiting — fail-open vs. fail-closed behavior is not yet enforced.
- **Circuit breaker state is in-memory per process** — running multiple gateway instances requires moving this into Redis, or explicitly accepting independent per-instance tripping.
- **API key auth has no rotation, hashing, or revocation** — acceptable for this project's scope, flagged for future hardening.
- **Tenant onboarding is manual** via `scripts/seed_tenants.py` — no admin API yet.
- **No Redis Cluster/Sentinel** — sufficient for local/single-instance use, not for horizontal scaling.

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## 🏷️ Topics

`api-gateway` `fastapi` `redis` `python` `microservices` `reverse-proxy` `distributed-systems` `backend` `rate-limiter` `circuit-breaker` `httpx`

---

## 🙋 Author

Built by [Your Name] to explore the core building blocks of modern API Gateways — authentication, multi-tenancy, traffic management, resiliency, and observability — using FastAPI, Redis, and HTTPX.
