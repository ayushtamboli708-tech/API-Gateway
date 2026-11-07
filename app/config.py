from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central, environment-driven configuration for the gateway.

    Every value can be overridden with a `GATEWAY_`-prefixed environment
    variable, for example:

        GATEWAY_REDIS_URL=redis://redis:6379/0
        GATEWAY_BACKENDS={"users":"http://users:9001"}
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="GATEWAY_",
        extra="ignore",
    )

    app_name: str = "Multi-Tenant API Gateway"

    # --- Redis ---------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout: float = 1.0
    redis_connect_timeout: float = 1.0

    # --- Downstream services -------------------------------------------
    # JSON object, e.g. {"users": "http://localhost:9001"}
    backends: dict[str, str] = Field(
        default_factory=lambda: {
            "users": "http://localhost:9001",
            "orders": "http://localhost:9002",
        }
    )

    @field_validator("backends")
    @classmethod
    def _validate_backends(cls, value: dict[str, str]) -> dict[str, str]:
        """Reject backend URLs that could be used for SSRF or credential leaks."""
        for name, url in value.items():
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ValueError(
                    f"Backend {name!r} must be an absolute http(s) URL"
                )
            if parts.username or parts.password:
                raise ValueError(
                    f"Backend {name!r} must not embed credentials"
                )
            if parts.query or parts.fragment:
                raise ValueError(
                    f"Backend {name!r} must not include a query or fragment"
                )
        return value

    # --- Proxy timeouts -------------------------------------------------
    proxy_connect_timeout: float = 2.0
    proxy_read_timeout: float = 3.0
    proxy_write_timeout: float = 2.0
    proxy_pool_timeout: float = 2.0

    # Hard ceiling for one client request, including retries and backoff.
    proxy_total_timeout: float = 10.0

    proxy_max_connections: int = 100
    proxy_max_keepalive_connections: int = 20

    # Largest request body the gateway will buffer (10 MiB by default).
    max_request_body_bytes: int = 10 * 1024 * 1024

    # --- Retries --------------------------------------------------------
    retry_max_attempts: int = 2
    retry_base_delay: float = 0.5

    # --- Circuit breaker ------------------------------------------------
    breaker_failure_threshold: int = 5
    breaker_recovery_timeout: float = 30.0
    breaker_half_open_timeout: float = 5.0

    # --- Metering -------------------------------------------------------
    rate_limit_window_seconds: int = 60
    quota_ttl_seconds: int = 60 * 60 * 24 * 31

    # --- Authentication abuse protection --------------------------------
    # Invalid API keys are counted per client IP; valid traffic is never
    # affected. 429 is returned once the limit is exceeded.
    auth_failure_limit: int = 20
    auth_failure_window_seconds: int = 60

    # --- Host allow-list (empty/"*" disables the check) ------------------
    allowed_hosts: list[str] = Field(default_factory=lambda: ["*"])

    # --- CORS (disabled by default; set GATEWAY_CORS_ORIGINS as JSON) ---
    cors_origins: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings object."""
    return Settings()
