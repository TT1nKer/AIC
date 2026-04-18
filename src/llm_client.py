"""
llm_client — DeepSeek chat completions wrapper (stdlib-only).

Loads DEEPSEEK_API_KEY from <project>/.env.
Forces JSON object output. 30s timeout. 1 retry on transient failure.
"""

from __future__ import annotations
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT = 30
MAX_RETRIES = 1


class LLMError(Exception):
    pass


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_PATH.exists():
        raise LLMError(f"missing {ENV_PATH}")
    for line in ENV_PATH.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY") or _load_env().get("DEEPSEEK_API_KEY")
    if not key:
        raise LLMError("DEEPSEEK_API_KEY not found")
    return key


def chat_json(
    system: str,
    user: str,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> dict:
    """Call DeepSeek with JSON response format; return parsed dict."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }

    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(DEEPSEEK_URL, data=data, headers=headers, method="POST")
        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
                raw = resp.read().decode("utf-8")
            payload = json.loads(raw)
            content = payload["choices"][0]["message"]["content"]
            return json.loads(content)
        except urllib.error.HTTPError as e:
            body_err = e.read().decode("utf-8", errors="replace")[:500]
            if e.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
                last_err = LLMError(f"HTTP {e.code}: {body_err}")
                continue
            raise LLMError(f"HTTP {e.code}: {body_err}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
                last_err = LLMError(str(e))
                continue
            raise LLMError(str(e)) from e
        except (KeyError, json.JSONDecodeError) as e:
            raise LLMError(f"bad response shape: {e}") from e

    raise last_err or LLMError("unknown error")
