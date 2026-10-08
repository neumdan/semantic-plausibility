# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Server-owned model aliases for OpenAI-compatible chat APIs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class ModelEndpoint:
    alias: str
    base_url: str
    model: str
    api_key_env: str = ""
    temperature: float = 0.0
    max_tokens: int = 1024
    timeout_seconds: int = 120
    reasoning_effort: str = ""
    response_mode: str = "json_schema"
    enable_thinking: bool | None = None

    def __post_init__(self) -> None:
        if (
            any(not isinstance(value, str) for value in (self.alias, self.base_url, self.model, self.api_key_env))
            or not self.alias or not self.model
            or type(self.temperature) not in {int, float}
            or not math.isfinite(self.temperature)
            or not 0 <= self.temperature <= 2
            or type(self.max_tokens) is not int
            or not 64 <= self.max_tokens <= 8192
            or type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 1800
            or not isinstance(self.reasoning_effort, str) or self.reasoning_effort not in {"", "none", "low", "medium", "high"}
            or not isinstance(self.response_mode, str) or self.response_mode not in {"json_schema", "json_object"}
            or self.enable_thinking is not None and type(self.enable_thinking) is not bool
        ):
            raise ValueError("Invalid model temperature, token budget, timeout, reasoning effort or response mode")


DEFAULT_MODEL = ModelEndpoint("local", "http://127.0.0.1:11434/v1", "ministral-3:3b")


def allowed_endpoint(url: str) -> bool:
    parsed = urlparse(url)
    internal_hosts = {
        host.strip().lower() for host in os.getenv("SQC_ALLOWED_HTTP_HOSTS", "").split(",") if host.strip()
    }
    return (
        bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and (
            parsed.scheme == "https"
            or parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", *internal_hosts}
        )
    )


def select_model(alias: str = "local", config: Path | None = None) -> tuple[ModelEndpoint, str]:
    """A request selects an alias; only deployment config chooses its URL."""
    local = ModelEndpoint(
        DEFAULT_MODEL.alias,
        os.getenv("OLLAMA_BASE_URL") or os.getenv("LMSTUDIO_BASE_URL", DEFAULT_MODEL.base_url),
        os.getenv("OLLAMA_MODEL") or os.getenv("LMSTUDIO_MODEL", DEFAULT_MODEL.model),
    )
    models = {local.alias: local}
    if config is not None:
        payload = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise ValueError("Model config must contain a models array")
        for row in payload["models"]:
            if not isinstance(row, dict):
                raise ValueError("Model entries must be objects")
            try:
                endpoint = ModelEndpoint(**row)
            except TypeError:
                raise ValueError("Unsupported or missing model configuration fields") from None
            if not endpoint.alias or not endpoint.model or not allowed_endpoint(endpoint.base_url):
                raise ValueError("Model endpoints require an alias, model and HTTPS or loopback URL")
            models[endpoint.alias] = endpoint
    if alias not in models:
        raise ValueError(f"Unknown model alias: {alias}")
    endpoint = models[alias]
    if not allowed_endpoint(endpoint.base_url):
        raise ValueError("Model endpoint must use HTTPS or loopback HTTP")
    key = os.getenv(endpoint.api_key_env, "") if endpoint.api_key_env else ""
    if endpoint.api_key_env and not key:
        raise ValueError(f"Missing API key environment variable: {endpoint.api_key_env}")
    return endpoint, key
