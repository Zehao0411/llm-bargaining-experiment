from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass

from openai import OpenAI, RateLimitError


RATE_LIMIT_BACKOFF_SECONDS = [2, 5, 10, 20, 30]


@dataclass
class LLMResult:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class LLMClient:
    """Chat Completions client for a user-configured compatible endpoint."""

    def __init__(self, *, max_tokens: int = 500, timeout: float = 120.0) -> None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Set OPENAI_API_KEY in .env or the environment before running live trials")
        self.client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1",
            timeout=timeout,
        )
        self.max_tokens = max_tokens

    def close(self) -> None:
        self.client.close()

    def chat(self, *, model: str, messages: list[dict], temperature: float) -> LLMResult:
        start = time.monotonic()
        for delay in RATE_LIMIT_BACKOFF_SECONDS + [None]:
            try:
                response = self.client.chat.completions.create(
                    model=model, messages=messages, temperature=temperature, max_tokens=self.max_tokens,
                )
                break
            except RateLimitError:
                if delay is None:
                    raise
                time.sleep(delay + random.uniform(0, delay * 0.3))
        usage = response.usage
        return LLMResult(
            content=response.choices[0].message.content or "",
            input_tokens=(usage.prompt_tokens or 0) if usage else 0,
            output_tokens=(usage.completion_tokens or 0) if usage else 0,
            latency_ms=int((time.monotonic() - start) * 1000),
        )


def parse_json_object(text: str) -> dict:
    """Accept a JSON object, optionally fenced or surrounded by prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        result = json.loads(cleaned[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError("The model response must contain a JSON object")
    return result
