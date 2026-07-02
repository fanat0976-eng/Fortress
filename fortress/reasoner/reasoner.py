"""LLM Reasoner — two-tier: fast filter + deep analysis."""

import asyncio
import json
import logging
import re
import time
from typing import Optional

import httpx

from fortress.core.event_bus import Event
from fortress.core.context import ContextManager
from fortress.core.rules import Action

logger = logging.getLogger("fortress.reasoner")

# Whitelist of action types the LLM is allowed to generate
LLM_ALLOWED_ACTIONS = {"move", "copy", "delete", "notify", "log"}


def _sanitize_payload(payload: dict, max_len: int = 200) -> str:
    """Sanitize event payload for LLM prompt — remove potential injection."""
    text = json.dumps(payload, ensure_ascii=False)[:max_len]
    # Strip instruction-like patterns
    text = re.sub(r'(?i)(ignore|disregard|forget|override|system|prompt|instruction)', '[FILTERED]', text)
    return text


class CircuitBreaker:
    """Prevent cascading failures when LLM is down."""

    def __init__(self, failures: int = 3, cooldown: float = 300.0):
        self.max_failures = failures
        self.cooldown = cooldown
        self._failures = 0
        self._last_failure = 0.0
        self._is_open = False

    def is_open(self) -> bool:
        """Check if circuit is open. Safe to call — no side effects."""
        if self._is_open and time.time() - self._last_failure > self.cooldown:
            self._is_open = False
            self._failures = 0
            logger.info("Circuit breaker closed — retrying")
        return self._is_open

    def record_success(self) -> None:
        self._failures = 0
        self._is_open = False

    def record_failure(self) -> None:
        self._failures += 1
        self._last_failure = time.time()
        if self._failures >= self.max_failures:
            self._is_open = True
            logger.warning(f"Circuit breaker OPEN after {self._failures} failures")


class LLMClient:
    """Async Ollama HTTP client with connection pooling."""

    def __init__(self, model: str, ollama_url: str = "http://127.0.0.1:11434", timeout: int = 30):
        self.model = model
        self.base_url = ollama_url
        self._client = httpx.AsyncClient(timeout=timeout)

    async def chat(self, messages: list[dict], max_tokens: int = 500) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        resp = await self._client.post(f"{self.base_url}/v1/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def close(self) -> None:
        await self._client.aclose()


def _parse_llm_json(text: str) -> dict | None:
    """Extract JSON from LLM response — handles markdown code blocks."""
    text = text.strip()
    # Try direct JSON first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from code block
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    return None


class Reasoner:
    """Two-tier LLM reasoning: fast filter + deep analysis."""

    def __init__(self, config, context: ContextManager):
        from fortress.core.config import LLMConfig
        llm_cfg: LLMConfig = config.llm
        self.fast = LLMClient(llm_cfg.fast_model, llm_cfg.ollama_url, llm_cfg.fast_timeout)
        self.deep = LLMClient(llm_cfg.deep_model, llm_cfg.ollama_url, llm_cfg.deep_timeout)
        self.context = context
        self._circuit_breaker = CircuitBreaker(failures=3, cooldown=300)
        self._max_tokens = llm_cfg.max_tokens

    async def should_act(self, event: Event) -> bool:
        if self._circuit_breaker.is_open():
            return False

        context = await self.context.get_recent(n=10)
        payload = _sanitize_payload(event.payload)

        prompt = f"""You are a fast event filter. Decide if this event needs action.

Event: {event.type}
Source: {event.source}
Payload: {payload}

Recent context:
{context}

Answer with EXACTLY one word: "yes" or "no"."""

        try:
            t0 = time.time()
            response = await self.fast.chat([{"role": "user", "content": prompt}], max_tokens=5)
            latency = (time.time() - t0) * 1000
            logger.debug(f"Fast filter: {response.strip()} ({latency:.0f}ms)")
            self._circuit_breaker.record_success()

            # Exact match — not substring
            word = response.strip().lower().split()[0] if response.strip() else ""
            return word == "yes"
        except Exception as e:
            self._circuit_breaker.record_failure()
            logger.error(f"Fast filter error: {e}")
            return False

    async def decide_action(self, event: Event) -> Optional[Action]:
        if self._circuit_breaker.is_open():
            return None

        context = await self.context.get_recent(n=20)
        summary = await self.context.get_event_summary(hours=1)
        payload = _sanitize_payload(event.payload, max_len=500)

        prompt = f"""You are an autonomous AI agent. Analyze this event and decide what action to take.

Event: {event.type}
Source: {event.source}
Payload: {payload}

Recent decisions:
{context}

Event summary:
{summary}

Respond with JSON. Allowed types: {', '.join(LLM_ALLOWED_ACTIONS)}, none.
Example: {{"type": "notify", "message": "text"}}
If no action needed: {{"type": "none"}}"""

        try:
            t0 = time.time()
            response = await self.deep.chat([{"role": "user", "content": prompt}], max_tokens=self._max_tokens)
            latency = (time.time() - t0) * 1000
            self._circuit_breaker.record_success()
            logger.debug(f"Deep analysis: {response.strip()[:100]} ({latency:.0f}ms)")

            data = _parse_llm_json(response)
            if data is None:
                # LLM responded but returned non-JSON — not a connection failure
                logger.warning(f"LLM returned non-JSON for {event.type}")
                return Action(type="log", params={"message": f"LLM returned non-JSON for {event.type}"})

            action_type = data.get("type", "none")
            if action_type == "none":
                return None

            # Validate action type against whitelist
            if action_type not in LLM_ALLOWED_ACTIONS:
                logger.warning(f"LLM returned blocked action type: {action_type}")
                return Action(type="log", params={"message": f"Blocked action type: {action_type}"})

            return Action(type=action_type, params={k: v for k, v in data.items() if k != "type"})

        except Exception as e:
            self._circuit_breaker.record_failure()
            logger.error(f"Deep analysis error: {e}")
            return None

    async def close(self) -> None:
        await self.fast.close()
        await self.deep.close()
