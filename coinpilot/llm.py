"""Minimal client for any OpenAI-compatible chat completions endpoint."""

from __future__ import annotations

import json
import os

from . import http


class LLMError(RuntimeError):
    """Raised when the model cannot be reached or returns unusable output."""


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def parse_json_text(text: str) -> dict:
    cleaned = _strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMError(f"model did not return JSON: {cleaned[:200]}")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned malformed JSON: {exc}") from exc


class LLM:
    def __init__(self, config: dict):
        settings = config.get("llm", {})
        self.enabled = bool(settings.get("enabled"))
        self.base_url = (settings.get("base_url") or "").rstrip("/")
        self.model = settings.get("model") or ""
        self.temperature = float(settings.get("temperature") or 0.2)
        self.timeout = int(settings.get("timeout_seconds") or 60)
        self.key = (os.environ.get(settings.get("api_key_env") or "OPENAI_API_KEY") or "").strip()

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.base_url) and bool(self.model)

    def chat_json(self, system: str, user: str) -> dict:
        if not self.available:
            raise LLMError("LLM is disabled or not configured")
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        try:
            payload = http.post_json(
                f"{self.base_url}/chat/completions", body, headers=headers, timeout=self.timeout, retries=1
            )
        except http.HttpError as exc:
            raise LLMError(str(exc)) from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected completion shape: {str(payload)[:200]}") from exc
        return parse_json_text(content or "")
