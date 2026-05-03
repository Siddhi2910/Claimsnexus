"""
Safe LLM call wrapper with timeout, retry, and comprehensive error handling.
This is the SINGLE SOURCE OF TRUTH for all LLM calls across the system.

NOTE: Concurrency/rate-limiting is handled ENTIRELY inside unified_llm.py (_SEMAPHORE + _rate_gate).
      Do NOT add another semaphore here — that causes a deadlock when LLM_CONCURRENCY=1.
"""

import asyncio
import json
import structlog
from typing import Any
from app.services.unified_llm import call_llm

log = structlog.get_logger()

# Robust settings for real API usage
LLM_TIMEOUT_SECONDS = 30
LLM_MAX_RETRIES = 2          # 2 retries (total 3 attempts)
LLM_MAX_ATTEMPTS = 1 + LLM_MAX_RETRIES
LLM_RETRY_DELAY_S = 1.5


async def safe_llm_call(
    prompt: str,
    system: str,
    use_json: bool = False,
    max_tokens: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """
    Safe LLM call with timeout, retry, and comprehensive error handling.

    Args:
        prompt: User/question prompt
        system: System prompt
        use_json: If True, expect JSON response and parse it
        max_tokens: Override default max_tokens
        model: Override default model selection

    Returns:
        Structured dict:
        {
            "status": "SUCCESS" | "LLM_FAILED",
            "result": <parsed_json or string>,
            "error": <str or None>,
            "reason": <str>,
            "retries_used": <int>,
            "duration_ms": <int>,
        }
    """
    import time
    import random
    start_time = time.time()
    retries_used = 0
    last_error = None

    log.info(
        "LLM CALL START",
        use_json=use_json,
        model=model,
        timeout_seconds=LLM_TIMEOUT_SECONDS,
        max_attempts=LLM_MAX_ATTEMPTS,
    )

    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        try:
            retries_used = attempt - 1

            # Merge system + user into one blob for unified_llm which handles Gemini REST format
            user_blob = f"{system}\n\n{prompt}".strip()

            # unified_llm.call_llm owns all rate-limiting, semaphore, and provider selection.
            resp = await asyncio.wait_for(
                call_llm(
                    user_blob,
                    json_mode=use_json,
                    max_tokens=max_tokens,
                    model=model,
                ),
                timeout=LLM_TIMEOUT_SECONDS,
            )

            if resp.get("status") != "SUCCESS":
                raise RuntimeError(resp.get("reason", "LLM unavailable"))

            duration_ms = int((time.time() - start_time) * 1000)
            log.info(
                "LLM CALL SUCCESS",
                attempt=attempt,
                duration_ms=duration_ms,
                use_json=use_json,
                provider=resp.get("provider"),
            )
            return {
                "status": "SUCCESS",
                "result": resp.get("result"),
                "error": None,
                "reason": "LLM call succeeded",
                "retries_used": retries_used,
                "duration_ms": duration_ms,
            }

        except asyncio.TimeoutError:
            last_error = "Timeout exceeded"
            log.warning("LLM CALL TIMEOUT", attempt=attempt)
        except Exception as e:
            last_error = str(e)
            log.warning("LLM CALL ERROR", attempt=attempt, error=last_error)

        if attempt < LLM_MAX_ATTEMPTS:
            # Exponential backoff with jitter
            delay = LLM_RETRY_DELAY_S * (2 ** retries_used)
            delay += random.uniform(0.0, 0.5)
            # Respect explicit Retry-After if found in error string
            if "Retry-After: " in str(last_error):
                try:
                    parts = str(last_error).split("Retry-After: ")
                    explicit_delay = float(parts[1].split()[0])
                    delay = max(delay, explicit_delay)
                except Exception:
                    pass
            log.info("LLM CALL RETRYING", next_attempt=attempt + 1, delay_s=round(delay, 2))
            await asyncio.sleep(delay)

    duration_ms = int((time.time() - start_time) * 1000)
    log.error("LLM CALL FAILED COMPLETELY", duration_ms=duration_ms, retries=retries_used, last_error=last_error)
    return {
        "status": "LLM_FAILED",
        "result": None,
        "error": last_error,
        "reason": f"All {LLM_MAX_ATTEMPTS} attempts failed",
        "retries_used": retries_used,
        "duration_ms": duration_ms,
    }


async def safe_llm_call_messages(prompt_messages: list[dict]) -> dict[str, Any]:
    """
    OpenAI-format safe call wrapper.

    Required by system: accepts a list of messages like:
      [{"role":"system","content":"..."},{"role":"user","content":"..."}]

    Returns:
      {"status":"SUCCESS","result":<content_or_json>} or {"status":"LLM_FAILED","reason":"LLM unavailable"}
    """
    system = ""
    user_parts: list[str] = []
    for m in prompt_messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "")).strip().lower()
        content = m.get("content", "")
        if content is None:
            continue
        text = str(content)
        if role == "system" and not system:
            system = text
        else:
            user_parts.append(text)
    user = "\n\n".join(user_parts).strip()

    r = await safe_llm_call(prompt=user, system=system, use_json=False)
    if r.get("status") != "SUCCESS":
        return {"status": "LLM_FAILED", "reason": "LLM unavailable"}
    return {"status": "SUCCESS", "result": r.get("result")}


async def safe_arbiter_llm_call(
    prompt: str,
    system: str,
    use_json: bool = False,
) -> dict[str, Any]:
    """
    Special variant for arbiter agent (uses same unified LLM, same reliability guarantees).

    Returns same structured dict as safe_llm_call.
    """
    import time
    start_time = time.time()
    retries_used = 0
    last_error = None

    log.info(
        "ARBITER_LLM_CALL_START",
        use_json=use_json,
        timeout_seconds=LLM_TIMEOUT_SECONDS,
        max_retries=LLM_MAX_RETRIES,
    )

    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        try:
            retries_used = attempt - 1
            user_blob = f"{system}\n\n{prompt}".strip()
            resp = await asyncio.wait_for(
                call_llm(
                    user_blob,
                    json_mode=use_json,
                ),
                timeout=LLM_TIMEOUT_SECONDS,
            )
            if resp.get("status") != "SUCCESS":
                raise RuntimeError(resp.get("reason", "LLM unavailable"))

            duration_ms = int((time.time() - start_time) * 1000)
            log.info("ARBITER_LLM_CALL_SUCCESS", attempt=attempt, duration_ms=duration_ms, provider=resp.get("provider"))
            return {
                "status": "SUCCESS",
                "result": resp.get("result"),
                "error": None,
                "reason": "Arbiter LLM call succeeded",
                "retries_used": retries_used,
                "duration_ms": duration_ms,
            }

        except json.JSONDecodeError as e:
            last_error = f"Arbiter JSON parse error: {str(e)}"
            log.warning("ARBITER_LLM_JSON_ERROR", attempt=attempt, error=str(e))
            if attempt < LLM_MAX_ATTEMPTS:
                await asyncio.sleep(LLM_RETRY_DELAY_S)
                continue

        except Exception as e:
            last_error = f"Arbiter LLM error: {str(e)}"
            log.error(
                "ARBITER_LLM_EXCEPTION",
                attempt=attempt,
                error_type=type(e).__name__,
                error=str(e),
            )
            if attempt < LLM_MAX_ATTEMPTS:
                await asyncio.sleep(LLM_RETRY_DELAY_S)
                continue

    duration_ms = int((time.time() - start_time) * 1000)

    log.error(
        "ARBITER_LLM_CALL_FAILED",
        reason=last_error,
        retries_used=retries_used,
    )

    return {
        "status": "LLM_FAILED",
        "result": None,
        "error": last_error,
        "reason": "Arbiter LLM unavailable",
        "retries_used": retries_used,
        "duration_ms": duration_ms,
    }
