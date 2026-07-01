"""AI provider presets and model discovery."""

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# Preset configurations for popular providers, alphabetized by name.
PRESETS = {
    "Anthropic (Claude)": {
        # Anthropic's OpenAI-compatibility endpoint: same /chat/completions
        # shape as every other provider here, authenticated with your Anthropic
        # API key as a Bearer token. Not the native Messages API.
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-haiku-4-5",  # cheap + fast for quick roasts
        "models": [
            "claude-haiku-4-5",
            "claude-sonnet-5",
            "claude-sonnet-4-6",
            "claude-opus-4-8",
            "claude-opus-4-7",
        ],
    },
    "Custom": {
        "base_url": "",
        "default_model": "",
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",  # cheap + fast for quick roasts
        "models": [
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        ],
    },
    "Google Gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.5-flash",
        "models": [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
        ],
    },
    "Groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.1-8b-instant",
    },
    "LM Studio (Local)": {
        "base_url": "http://localhost:1234/v1",
        "default_model": "local-model",
    },
    "MiniMax": {
        "base_url": "https://api.minimaxi.chat/v1",
        "default_model": "MiniMax-M2.7",
        "models": [
            "MiniMax-M2.7",
            "MiniMax-M2.7-highspeed",
            "MiniMax-M2.5",
            "MiniMax-M2.5-highspeed",
            "MiniMax-M2.1",
            "MiniMax-M2.1-highspeed",
            "MiniMax-M2",
            "MiniMax-Text-01",
        ],
    },
    "Mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-small-latest",
    },
    "Ollama (Local)": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.1",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    "OpenRouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "meta-llama/llama-3.1-8b-instruct:free",
    },
    "Together AI": {
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3.1-8B-Instruct-Turbo",
    },
    "Xiaomi MiMo": {
        # Supports both api-key and Authorization: Bearer auth; SparkyBot
        # always sends Bearer, which the platform accepts.
        "base_url": "https://api.xiaomimimo.com/v1",
        "default_model": "mimo-v2.5-pro",
    },
}


def fetch_models(base_url: str, api_key: str = "") -> list:
    """Fetch available models from an OpenAI-compatible /v1/models endpoint.

    Returns a list of model ID strings, or an empty list on failure.
    """
    if not base_url:
        return []

    # Normalize trailing slash
    url = base_url.rstrip("/") + "/models"

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.warning("Model list request failed: HTTP %s", resp.status_code)
            return []

        data = resp.json()
        # OpenAI-style response: {"object": "list", "data": [{"id": "..."}, ...]}
        models = []
        for item in data.get("data", []):
            model_id = item.get("id")
            if model_id:
                models.append(model_id)

        if models:
            logger.info("Fetched %d models from %s", len(models), base_url)
        return models

    except requests.RequestException as exc:
        logger.warning("Model list request failed: %s", exc)
        return []
