"""Service settings, read from environment variables with the ``AI_SERVICE_`` prefix."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # No env_file on purpose: Compose and Helm pass the environment; a stray dotenv file
    # must never change how the service runs.
    model_config = SettingsConfigDict(env_prefix="AI_SERVICE_", frozen=True)

    postgres_host: str = "postgres"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    # Internal listener of the Go Gateway (gateway.yaml, :8081).
    gateway_host: str = "gateway"
    gateway_port: int = Field(default=8081, ge=1, le=65535)
    # Redis is optional (spec 9.3): unset means no query cache and no readiness check.
    redis_host: str | None = None
    redis_port: int = Field(default=6379, ge=1, le=65535)
    readiness_timeout_s: float = Field(default=1.0, gt=0, le=10)
