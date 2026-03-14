"""
nakedtrader.ai.ollama — Ollama client met model routing per taaktype.

Graceful degradation: alle functies retourneren None bij fouten.
Het trading systeem draait normaal door als Ollama offline is.
"""

import json
import logging
from urllib.request import urlopen, Request
from urllib.error import URLError

log = logging.getLogger(__name__)

# ── Model routing per taaktype ─────────────────────────────
TASK_MODELS = {
    "sentiment": "phi3:mini",       # snelst — classificatie
    "reasoning": "qwen2.5:7b",      # gestructureerd — signal reasoning
    "narrative": "qwen2.5:7b",      # risk report → prose
    "report":    "qwen2.5:14b",     # kwaliteit — langere output
}

_ollama_url = "http://localhost:11434"
_timeout = 30  # seconden — trading loops mogen niet blokkeren


def configure(url: str = "http://localhost:11434", timeout: int = 30):
    """Stel Ollama URL en timeout in."""
    global _ollama_url, _timeout
    _ollama_url = url.rstrip("/")
    _timeout = timeout


def available() -> bool:
    """Check of Ollama bereikbaar is."""
    try:
        resp = urlopen(f"{_ollama_url}/api/version", timeout=3)
        return resp.status == 200
    except Exception:
        return False


def list_models() -> list[str]:
    """Retourneer namen van beschikbare modellen."""
    try:
        resp = urlopen(f"{_ollama_url}/api/tags", timeout=5)
        data = json.loads(resp.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def generate(
    prompt: str,
    task: str = "reasoning",
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 500,
    model: str = None,
) -> str | None:
    """Genereer tekst via Ollama. Retourneert None bij fout.

    Args:
        prompt: De user prompt.
        task: Taaktype voor model routing ("sentiment", "reasoning", "narrative", "report").
        system: Optioneel systeemprompt.
        temperature: Creativiteit (0.0-1.0). Laag = deterministisch.
        max_tokens: Maximale output lengte.
        model: Override model (negeert task routing).
    """
    chosen_model = model or TASK_MODELS.get(task, "qwen2.5:7b")

    payload = {
        "model": chosen_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    if system:
        payload["system"] = system

    try:
        body = json.dumps(payload).encode()
        req = Request(
            f"{_ollama_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urlopen(req, timeout=_timeout)
        data = json.loads(resp.read())
        response_text = data.get("response", "").strip()
        if response_text:
            log.debug("Ollama [%s] response (%d chars)", chosen_model, len(response_text))
            return response_text
        return None
    except URLError as e:
        log.debug("Ollama niet bereikbaar: %s", e)
        return None
    except Exception as e:
        log.warning("Ollama fout [%s]: %s", chosen_model, e)
        return None


def classify_sentiment(text: str) -> dict | None:
    """Classificeer sentiment: positive/negative/neutral + score.

    Returns:
        {"sentiment": "positive|negative|neutral", "score": 0.0-1.0, "reason": "..."}
        of None bij fout.
    """
    from nakedtrader.ai.prompts import SENTIMENT_SYSTEM

    raw = generate(text, task="sentiment", system=SENTIMENT_SYSTEM, temperature=0.1, max_tokens=200)
    if not raw:
        return None

    # Parse JSON uit response (model kan extra tekst toevoegen)
    try:
        # Zoek eerste { ... } blok
        start = raw.index("{")
        end = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        log.debug("Sentiment JSON parse mislukt: %s", raw[:200])
        return None
