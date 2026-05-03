from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.config import settings

log = structlog.get_logger()


def _now() -> float:
    return time.time()


def _env(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        raw = getattr(settings, name.lower(), None)
    if raw is None:
        raw = default
    return str(raw).strip()


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def _is_placeholder_key(raw: str) -> bool:
    if not isinstance(raw, str):
        return True
    key = raw.strip()
    if not key:
        return True

    lower = key.lower()
    placeholder_markers = [
        "your_",
        "your ",
        "placeholder",
        "change",
        "replace",
        "changeme",
        "dummy",
        "test",
        "<",
        ">",
        "xxx",
        "****",
        "...",
    ]
    if any(marker in lower for marker in placeholder_markers):
        return True
    return False


def _is_valid_api_key(raw: str, provider: str) -> bool:
    if _is_placeholder_key(raw):
        return False
    key = str(raw).strip()
    if provider == "openai":
        return key.startswith("sk-") and len(key) >= 30 and " " not in key
    if provider == "gemini":
        return key.startswith("AIza") and len(key) >= 20 and " " not in key
    return bool(key)


async def _discover_gemini_models(api_key: str) -> list[str]:
    """Discover available Gemini models that support generateContent."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                models = [
                    m['name'].replace("models/", "")
                    for m in data.get('models', [])
                    if 'generateContent' in m.get('supportedGenerationMethods', [])
                ]
                return models
            else:
                log.warning("GEMINI_MODEL_DISCOVERY_FAILED", status=r.status_code, error=r.text[:200])
                return []
    except Exception as e:
        log.warning("GEMINI_MODEL_DISCOVERY_ERROR", error=str(e))
        return []


def _select_gemini_model(available: list[str], requested: str) -> str:
    """Select best available Gemini model."""
    preferred = ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash-latest"]
    for p in preferred:
        if p in available:
            return p
    if requested in available:
        return requested
    if available:
        return available[0]
    return requested  # fallback to requested even if not available


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    value: dict[str, Any]


_CACHE: dict[str, _CacheEntry] = {}
_CACHE_TTL_S = int(_env("LLM_CACHE_TTL_SECONDS", "600") or "600")  # 10 min default

# Max output tokens: heuristic fallbacks handle truncation gracefully.
# 200 tokens is the configured default; agents pass max_tokens override for LLM-based analysis.
_MAX_OUTPUT_TOKENS = int(_env("LLM_MAX_OUTPUT_TOKENS", "200") or "200")

# Strict global rate control (avoid bursts).
# NOTE: This is the ONLY semaphore in the call chain — llm_safety_wrapper must NOT add its own.
_CONCURRENCY = int(_env("LLM_CONCURRENCY", "1") or "1")
_SEMAPHORE = asyncio.Semaphore(max(1, _CONCURRENCY))
_MIN_DELAY_S = float(_env("LLM_MIN_DELAY_SECONDS", "1.5") or "1.5")
_last_call_ts: float = 0.0
_delay_lock = asyncio.Lock()


async def _rate_gate() -> None:
    global _last_call_ts
    async with _delay_lock:
        wait = (_last_call_ts + _MIN_DELAY_S) - _now()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_ts = _now()


def _extract_claim_summary_from_prompt(prompt: str) -> str:
    """
    Best-effort token reduction without changing agent logic.
    If prompt contains claim JSON, extract only a small set of relevant fields.
    Keeps full prompt if no large JSON detected.
    """
    if not prompt:
        return prompt

    # Heuristic: find the biggest JSON object and parse it.
    start = prompt.find("{")
    end = prompt.rfind("}")
    if 0 <= start < end:
        candidate = prompt[start : end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict) and len(obj) > 5:
                # Only compress large claim objects, not small nested ones
                keep_keys = [
                    "claim_number",
                    "claim_type",
                    "provider_name",
                    "provider_id",
                    "service_date",
                    "icd_codes",
                    "cpt_codes",
                    "diagnosis_description",
                    "procedure_description",
                    "billed_amount",
                    "requested_amount",
                    "in_network",
                    "prior_auth_number",
                    "plan_id",
                    "claimant_name",
                    "policy_number",
                ]
                slim = {k: obj.get(k) for k in keep_keys if k in obj}
                if slim:
                    # Replace the JSON blob in the prompt with the slim version
                    slim_str = json.dumps(slim, ensure_ascii=False, default=str)
                    return prompt[:start] + slim_str + prompt[end + 1:]
        except Exception:
            pass

    # Fallback: keep only the tail of the prompt to reduce tokens.
    if len(prompt) > 3500:
        return prompt[-3500:]
    return prompt


def _clean_json_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if len(lines) > 2 and lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
    
    # Simple repair: find first { and last }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if 0 <= start < end:
        cleaned = cleaned[start:end+1]
        
    return cleaned


async def _call_openai_chat(
    messages: list[dict[str, str]],
    model: str,
    max_tokens: int,
    json_mode: bool,
) -> str:
    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing")

    base = _env("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        r = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise ValueError("OpenAI response missing content")
        return content


async def _call_gemini(
    messages: list[dict[str, str]],
    model: str,
    max_tokens: int,
    json_mode: bool,
) -> str:
    api_key = _env("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing")

    # Strip "models/" prefix if present
    model = model.lstrip("models/")

    # Convert OpenAI messages → one string to minimize tokens.
    sys = ""
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "")
        c = m.get("content", "")
        if role == "system" and not sys:
            sys = c
        else:
            parts.append(c)
    prompt = "\n\n".join([p for p in ([sys] + parts) if p]).strip()

    # REST API (no heavy SDK deps, avoids protobuf conflicts)
    # Endpoint: https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=...
    base = _env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")
    url = f"{base}/v1beta/models/{model}:generateContent?key={api_key}"
    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": max_tokens,
        },
    }
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        r = await client.post(url, json=payload)
        if r.status_code == 429:
            raise RuntimeError(f"429 Too Many Requests: {r.text[:200]}")
        if r.status_code == 404:
            # Model not found, try to discover and retry once
            log.warning("GEMINI_MODEL_404", model=model, url=url)
            available_models = await _discover_gemini_models(api_key)
            if available_models:
                new_model = _select_gemini_model(available_models, model)
                if new_model != model:
                    log.info("GEMINI_MODEL_RETRY", old_model=model, new_model=new_model)
                    # Retry with new model
                    new_url = f"{base}/v1beta/models/{new_model}:generateContent?key={api_key}"
                    r = await client.post(new_url, json=payload)
                    if r.status_code == 429:
                        raise RuntimeError(f"429 Too Many Requests: {r.text[:200]}")
                    r.raise_for_status()
                    data = r.json()
                else:
                    r.raise_for_status()
            else:
                r.raise_for_status()
        else:
            r.raise_for_status()
        data = r.json()
        # candidates[0].content.parts[0].text
        cands = data.get("candidates") or []
        if not cands:
            # Check for prompt feedback block
            pf = data.get("promptFeedback", {})
            block = pf.get("blockReason", "unknown")
            raise ValueError(f"Gemini response missing candidates (blockReason={block})")
        content = cands[0].get("content") or {}
        parts_list = content.get("parts") or []
        if not parts_list:
            raise ValueError("Gemini response missing parts")
        text = parts_list[0].get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Gemini response missing text")
        return text


async def call_llm(
    prompt: str,
    *,
    json_mode: bool = True,
    max_tokens: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """
    Unified LLM call:
      Primary: Gemini (if GEMINI_API_KEY set)
      Fallback: OpenAI (if OPENAI_API_KEY set)

    Stable behavior:
    - global semaphore (LLM_CONCURRENCY, default 1) — ONLY semaphore in the call chain
    - minimum delay between calls (LLM_MIN_DELAY_SECONDS, default 1.0s)
    - exponential backoff on 429 / rate-limit
    - caching identical prompts (LLM_CACHE_TTL_SECONDS, default 600s)
    - max_tokens raised to allow real JSON responses (default 800)
    """
    prompt_compact = _extract_claim_summary_from_prompt(prompt)

    model_gemini = model or _env("GEMINI_MODEL", "gemini-1.5-flash")
    model_openai = _env("OPENAI_MODEL", _env("LLM_MODEL", "gpt-4o-mini") or "gpt-4o-mini")

    # Cap tokens: min 100, max 1500 for agent JSON, never exceeds API limits
    token_limit = max_tokens if max_tokens is not None else _MAX_OUTPUT_TOKENS
    token_limit = max(100, min(1500, int(token_limit)))

    messages = [
        {"role": "system", "content": "Return only valid JSON. Be concise." if json_mode else "Be concise."},
        {"role": "user", "content": prompt_compact},
    ]

    cache_key = _sha(
        json.dumps(
            {
                "m": messages,
                "j": json_mode,
                "kg": model_gemini,
                "ko": model_openai,
                "t": token_limit,
            },
            sort_keys=True,
        )
    )
    ent = _CACHE.get(cache_key)
    if ent and ent.expires_at > _now():
        log.debug("LLM_CACHE_HIT", key=cache_key[:8])
        return ent.value

    async with _SEMAPHORE:
        await _rate_gate()

        gemini_available = _is_valid_api_key(_env("GEMINI_API_KEY"), "gemini")
        openai_available = _is_valid_api_key(_env("OPENAI_API_KEY"), "openai")
        model_provider = _env("MODEL_PROVIDER", "gemini").lower()

        # Discover and select Gemini model
        selected_gemini_model = model_gemini
        if gemini_available:
            available_models = await _discover_gemini_models(_env("GEMINI_API_KEY"))
            selected_gemini_model = _select_gemini_model(available_models, model_gemini)
            log.info("GEMINI_MODEL_SELECTED", requested=model_gemini, selected=selected_gemini_model, available_count=len(available_models))

        log.info(
            "LLM_PROVIDER_CHECK",
            gemini_available=gemini_available,
            openai_available=openai_available,
            model_provider=model_provider,
            gemini_model=selected_gemini_model,
            openai_model=model_openai,
            token_limit=token_limit,
        )

        # Build provider list based on MODEL_PROVIDER
        provider_order = ["gemini", "openai"] if model_provider == "gemini" else ["openai", "gemini"]
        providers: list[tuple[str, Any]] = []
        for p in provider_order:
            if p == "gemini" and gemini_available:
                async def _gemini_call(_m=messages, _mdl=selected_gemini_model, _t=token_limit, _j=json_mode):
                    return await _call_gemini(_m, _mdl, _t, _j)
                providers.append(("gemini", _gemini_call))
            elif p == "openai" and openai_available:
                async def _openai_call(_m=messages, _mdl=model_openai, _t=token_limit, _j=json_mode):
                    return await _call_openai_chat(_m, _mdl, _t, _j)
                providers.append(("openai", _openai_call))

        if not providers:
            reason = "No valid Gemini or OpenAI API key configured"
            log.error("LLM CALL FAILED: no providers", reason=reason)
            return {"status": "LLM_FAILED", "reason": reason}

        last_err: str | None = None
        for provider, fn in providers:
            for attempt in range(3):
                try:
                    text = await fn()
                    result: Any = text
                    if json_mode:
                        cleaned = _clean_json_text(text)
                        try:
                            result = json.loads(cleaned)
                        except json.JSONDecodeError:
                            # Retry once with additional cleaning
                            cleaned = _clean_json_text(cleaned)
                            result = json.loads(cleaned)
                    out = {"status": "SUCCESS", "result": result, "provider": provider}
                    _CACHE[cache_key] = _CacheEntry(expires_at=_now() + _CACHE_TTL_S, value=out)
                    log.info("LLM_CALL_SUCCESS", provider=provider, attempt=attempt + 1)
                    return out
                except Exception as e:
                    last_err = f"{provider}:{type(e).__name__}:{e}"
                    msg = str(e)
                    is_rate_limited = any(
                        token in msg for token in ("429", "Too Many Requests", "RateLimit", "rate limit", "quota")
                    )
                    if is_rate_limited and attempt < 2:
                        wait_s = 2 ** (attempt + 1)
                        import random
                        wait_s += random.uniform(0.0, 0.5)
                        if "Retry-After:" in msg:
                            try:
                                after = msg.split("Retry-After:")[1].split()[0]
                                wait_s = max(wait_s, float(after))
                            except Exception:
                                pass
                        log.warning("LLM_RATE_LIMITED", provider=provider, attempt=attempt + 1, retry_in=round(wait_s, 2))
                        await asyncio.sleep(wait_s)
                        continue
                    log.warning("LLM_PROVIDER_FAILED", provider=provider, attempt=attempt + 1, error=str(e))
                    break  # Try next provider

    log.error("LLM CALL FAILED: all providers exhausted", error=last_err)
    return {"status": "LLM_FAILED", "reason": "LLM unavailable", "detail": last_err}
