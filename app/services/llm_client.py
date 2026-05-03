import json
import os
import time
from typing import Any

import httpx
import structlog

from app.config import settings

log = structlog.get_logger()


def _detect_llm_provider() -> str:
    """Pick provider from LLM_PROVIDER / env keys / sensible defaults (local Ollama last)."""
    explicit = (
        os.getenv("MODEL_PROVIDER")
        or os.getenv("LLM_PROVIDER")
        or getattr(settings, "llm_provider", "")
        or ""
    ).strip().lower()
    if explicit in ("openai", "ollama", "anthropic", "together"):
        return explicit

    if str(os.getenv("USE_OLLAMA", "")).strip().lower() in ("1", "true", "yes", "on"):
        return "ollama"

    env_together = str(os.getenv("USE_TOGETHER", "")).strip().lower()
    if env_together in ("1", "true", "yes", "on") or settings.use_together:
        return "together"

    openai_key = (os.getenv("OPENAI_API_KEY") or settings.openai_api_key or "").strip()
    if openai_key:
        return "openai"

    anth_key = (os.getenv("ANTHROPIC_API_KEY") or settings.anthropic_api_key or "").strip()
    if anth_key:
        return "anthropic"

    together_key = (os.getenv("TOGETHER_API_KEY") or settings.together_api_key or "").strip()
    if together_key:
        return "together"

    return "ollama"


class LLMClient:
    """Async LLM: Anthropic, Together.ai, OpenAI-compatible HTTP, or Ollama."""

    def __init__(self) -> None:
        self.provider = _detect_llm_provider()
        self._http: httpx.AsyncClient | None = None
        self._anthropic: Any = None
        self._together_headers: dict[str, str] | None = None
        self._openai_headers: dict[str, str] | None = None
        self._ollama_checked = False

        # Model override (single env across providers)
        env_model = (os.getenv("LLM_MODEL") or "").strip()

        self.together_base_url = os.getenv("TOGETHER_BASE_URL") or settings.together_base_url
        self.together_model = env_model or os.getenv("TOGETHER_MODEL") or settings.together_model

        self.ollama_base = (os.getenv("OLLAMA_BASE_URL") or settings.ollama_base_url).rstrip("/")
        self.ollama_model = env_model or os.getenv("OLLAMA_MODEL") or settings.ollama_model

        self.openai_base = (os.getenv("OPENAI_BASE_URL") or settings.openai_base_url).rstrip("/")
        self.openai_model = env_model or os.getenv("OPENAI_MODEL") or settings.openai_model

        if self.provider == "together":
            api_key = (os.getenv("TOGETHER_API_KEY") or settings.together_api_key or "").strip()
            if not api_key:
                log.critical("LLM_CLIENT_INIT_ERROR", reason="TOGETHER_API_KEY is missing or empty")
                raise RuntimeError(
                    "TOGETHER_API_KEY is missing. Set it in .env or set LLM_PROVIDER to ollama/openai/anthropic."
                )
            # Together is OpenAI-compatible. Default base_url should be https://api.together.ai/v1
            self._together_headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
            log.info("LLM_CLIENT_INIT_SUCCESS", provider="together", model=self.together_model)

        elif self.provider == "openai":
            api_key = (os.getenv("OPENAI_API_KEY") or settings.openai_api_key or "").strip()
            if not api_key:
                log.critical("LLM_CLIENT_INIT_ERROR", reason="OPENAI_API_KEY missing")
                raise RuntimeError(
                    "OPENAI_API_KEY missing. Set it in .env or choose another LLM_PROVIDER "
                    "(ollama, anthropic, together)."
                )
            self._openai_headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
            log.info("LLM_CLIENT_INIT_SUCCESS", provider="openai", model=self.openai_model, base=self.openai_base)

        elif self.provider == "ollama":
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0))
            log.info(
                "LLM_CLIENT_INIT_SUCCESS",
                provider="ollama",
                model=self.ollama_model,
                base_url=self.ollama_base,
            )

        elif self.provider == "anthropic":
            api_key = (os.getenv("ANTHROPIC_API_KEY") or settings.anthropic_api_key or "").strip()
            if not api_key:
                log.critical("LLM_CLIENT_INIT_ERROR", reason="ANTHROPIC_API_KEY is missing or empty")
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is missing. Set it in .env or set LLM_PROVIDER=openai|ollama|together."
                )
            from anthropic import AsyncAnthropic

            self._anthropic = AsyncAnthropic(api_key=api_key)
            log.info("LLM_CLIENT_INIT_SUCCESS", provider="anthropic", model=settings.llm_default_model)

        else:
            raise RuntimeError(f"Unknown LLM provider: {self.provider}")

    async def _ensure_ollama_healthy(self) -> None:
        if self._ollama_checked or not self._http:
            return
        url = f"{self.ollama_base}/api/tags"
        log.info("LLM OLLAMA HEALTH CHECK", url=url)
        try:
            r = await self._http.get(url, timeout=3.0)
            r.raise_for_status()
        except Exception as e:
            log.error("LLM OLLAMA HEALTH FAILED", error=str(e), url=url)
            raise RuntimeError(f"Ollama not reachable at {self.ollama_base}: {e}") from e
        self._ollama_checked = True
        log.info("LLM OLLAMA HEALTH OK", url=url)

    async def _complete_together_chat(
        self,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> tuple[str, dict]:
        """Together uses OpenAI-compatible POST /v1/chat/completions."""
        assert self._http and self._together_headers
        base = self.together_base_url.rstrip("/")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        log.debug("LLM_COMPLETE_START", provider="together", model=model)
        r = await self._http.post(f"{base}/v1/chat/completions", headers=self._together_headers, json=payload)
        r.raise_for_status()
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("Together response missing choices")
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
        usage_raw = data.get("usage") or {}
        usage = {
            "input_tokens": usage_raw.get("prompt_tokens"),
            "output_tokens": usage_raw.get("completion_tokens"),
            "model": model,
            "duration_ms": 0,
        }
        return str(text), usage

    async def _complete_openai(
        self,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> tuple[str, dict]:
        assert self._http and self._openai_headers
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        log.debug("LLM_COMPLETE_START", provider="openai", model=model)
        r = await self._http.post(
            f"{self.openai_base}/chat/completions",
            headers=self._openai_headers,
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("OpenAI response missing choices")
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
        usage = data.get("usage") or {}
        duration_ms = 0
        return str(text), {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "model": model,
            "duration_ms": duration_ms,
        }

    async def _complete_ollama(
        self,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, dict]:
        assert self._http
        await self._ensure_ollama_healthy()

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        log.debug("LLM_COMPLETE_START", provider="ollama", model=model)
        r = await self._http.post(f"{self.ollama_base}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        text = ""
        if isinstance(data.get("message"), dict):
            text = data["message"].get("content") or ""
        usage = {
            "input_tokens": data.get("prompt_eval_count"),
            "output_tokens": data.get("eval_count"),
            "model": model,
            "duration_ms": 0,
        }
        return str(text), usage

    async def complete(
        self,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        use_cache: bool = True,
        _json_mode: bool = False,
    ) -> tuple[str, dict]:
        """Returns (content_text, usage_dict)."""
        start = time.monotonic()
        max_t = max_tokens or settings.llm_max_tokens
        temp = settings.llm_temperature if temperature is None else temperature

        if self.provider == "together":
            target_model = model or self.together_model
            text, usage = await self._complete_together_chat(
                system, user, target_model, max_t, temp, json_mode=_json_mode
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            usage["duration_ms"] = duration_ms
            log.debug("LLM_COMPLETE_SUCCESS", provider="together", model=target_model, duration_ms=duration_ms)
            return text, usage

        if self.provider == "openai":
            target_model = model or self.openai_model
            text, usage = await self._complete_openai(
                system, user, target_model, max_t, temp, json_mode=_json_mode
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            usage["duration_ms"] = duration_ms
            log.debug("LLM_COMPLETE_SUCCESS", provider="openai", model=target_model, duration_ms=duration_ms)
            return text, usage

        if self.provider == "ollama":
            target_model = model or self.ollama_model
            text, usage = await self._complete_ollama(system, user, target_model, max_t, temp)
            duration_ms = int((time.monotonic() - start) * 1000)
            usage["duration_ms"] = duration_ms
            log.debug("LLM_COMPLETE_SUCCESS", provider="ollama", model=target_model, duration_ms=duration_ms)
            return text, usage

        assert self._anthropic is not None
        target_model = model or settings.llm_default_model
        log.debug("LLM_COMPLETE_START", model=target_model, use_cache=use_cache)

        system_blocks: list[dict] = []
        if use_cache and len(system) > 1024:
            system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        else:
            system_blocks = [{"type": "text", "text": system}]

        response = await self._anthropic.messages.create(
            model=target_model,
            max_tokens=max_t,
            temperature=temp,
            system=system_blocks,
            messages=[{"role": "user", "content": user}],
        )

        duration_ms = int((time.monotonic() - start) * 1000)
        text = response.content[0].text
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "model": target_model,
            "duration_ms": duration_ms,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
            "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
        }
        log.debug("LLM_COMPLETE_SUCCESS", model=target_model, duration_ms=duration_ms, **usage)
        return text, usage

    async def complete_json(
        self,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict, dict]:
        """Like complete() but parses and returns a JSON dict."""
        suffix = "\n\nRespond ONLY with valid JSON. No markdown, no explanation."
        json_user = user + suffix

        if self.provider in ("openai", "together"):
            text, usage = await self.complete(
                system=system,
                user=json_user,
                model=model,
                max_tokens=max_tokens,
                _json_mode=True,
            )
        else:
            text, usage = await self.complete(
                system=system,
                user=json_user,
                model=model,
                max_tokens=max_tokens,
                _json_mode=False,
            )
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return json.loads(cleaned), usage

    async def complete_arbiter(self, system: str, user: str) -> tuple[str, dict]:
        """Use the more powerful Arbiter model when supported."""
        if self.provider == "anthropic":
            return await self.complete(
                system=system,
                user=user,
                model=settings.llm_arbiter_model,
            )
        if self.provider == "together":
            return await self.complete(
                system=system,
                user=user,
                model=self.together_model,
            )
        return await self.complete(system=system, user=user)

    async def complete_arbiter_json(self, system: str, user: str) -> tuple[dict, dict]:
        if self.provider == "anthropic":
            return await self.complete_json(
                system=system,
                user=user,
                model=settings.llm_arbiter_model,
            )
        if self.provider == "together":
            return await self.complete_json(
                system=system,
                user=user,
                model=self.together_model,
            )
        return await self.complete_json(system=system, user=user)


class _LLMClientProxy:
    """Lazy singleton so importing the app does not require API keys until first LLM call."""

    _inner: LLMClient | None = None

    def _ensure(self) -> LLMClient:
        if _LLMClientProxy._inner is None:
            _LLMClientProxy._inner = LLMClient()
        return _LLMClientProxy._inner

    async def complete(self, *args: Any, **kwargs: Any) -> tuple[str, dict]:
        return await self._ensure().complete(*args, **kwargs)

    async def complete_json(self, *args: Any, **kwargs: Any) -> tuple[dict, dict]:
        return await self._ensure().complete_json(*args, **kwargs)

    async def complete_arbiter(self, *args: Any, **kwargs: Any) -> tuple[str, dict]:
        return await self._ensure().complete_arbiter(*args, **kwargs)

    async def complete_arbiter_json(self, *args: Any, **kwargs: Any) -> tuple[dict, dict]:
        return await self._ensure().complete_arbiter_json(*args, **kwargs)


llm_client = _LLMClientProxy()
