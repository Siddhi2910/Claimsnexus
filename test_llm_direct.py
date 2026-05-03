"""
Quick diagnostic script to test Gemini and OpenAI LLM connectivity.
Run with: python test_llm_direct.py
"""
import asyncio
import os
import sys

# Load .env before imports
from dotenv import load_dotenv
load_dotenv()

async def test_gemini_direct():
    import httpx
    api_key = os.getenv("GEMINI_API_KEY", "")
    model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    if not api_key:
        print("❌ GEMINI_API_KEY not set")
        return False

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": "Return JSON only: {\"ping\": \"ok\"}"}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 50,
            "responseMimeType": "application/json",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            r = await client.post(url, json=payload)
            print(f"Gemini status: {r.status_code}, model: {model}")
            if r.status_code == 200:
                data = r.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                print(f"✅ Gemini OK: {text}")
                return True
            else:
                print(f"❌ Gemini error: {r.text[:300]}")
                return False
    except Exception as e:
        print(f"❌ Gemini exception: {type(e).__name__}: {e}")
        return False


async def test_openai():
    import httpx
    api_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        print("❌ OPENAI_API_KEY not set")
        return False

    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": 'Return JSON: {"ok": true, "verdict": "APPROVE"}'}],
        "max_tokens": 50,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            r = await client.post(url, headers=headers, json=payload)
            print(f"OpenAI status: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                text = data["choices"][0]["message"]["content"]
                print(f"✅ OpenAI OK: {text}")
                return True
            else:
                print(f"❌ OpenAI error: {r.text[:300]}")
                return False
    except Exception as e:
        print(f"❌ OpenAI exception: {e}")
        return False


async def test_unified():
    """Test via the unified_llm module."""
    sys.path.insert(0, ".")
    from app.services.unified_llm import call_llm
    try:
        result = await asyncio.wait_for(
            call_llm(
                'Return exactly this JSON: {"verdict": "APPROVE", "score": 0.1, "confidence": 0.9}',
                json_mode=True,
                max_tokens=100,
            ),
            timeout=30.0,
        )
        print(f"\nUnified LLM result: status={result.get('status')}, provider={result.get('provider')}")
        print(f"Result: {result.get('result')}")
        return result.get("status") == "SUCCESS"
    except Exception as e:
        print(f"❌ Unified LLM exception: {e}")
        return False


async def main():
    print("=" * 60)
    print("ClaimsNexus LLM Diagnostic")
    print("=" * 60)
    print(f"GEMINI_API_KEY set: {bool(os.getenv('GEMINI_API_KEY'))}")
    print(f"GEMINI_MODEL: {os.getenv('GEMINI_MODEL', 'not set')}")
    print(f"OPENAI_API_KEY set: {bool(os.getenv('OPENAI_API_KEY'))}")
    print(f"MODEL_PROVIDER: {os.getenv('MODEL_PROVIDER', 'not set')}")
    print()

    print("--- Testing Gemini direct ---")
    g_ok = await test_gemini_direct()

    print("\n--- Testing OpenAI direct ---")
    o_ok = await test_openai()

    print("\n--- Testing via unified_llm ---")
    u_ok = await test_unified()

    print("\n" + "=" * 60)
    print(f"Summary: Gemini={'✅' if g_ok else '❌'}, OpenAI={'✅' if o_ok else '❌'}, Unified={'✅' if u_ok else '❌'}")
    if g_ok or o_ok:
        print("✅ At least one LLM provider is working")
    else:
        print("❌ NO LLM provider is working! Agents will use fallback verdicts.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
