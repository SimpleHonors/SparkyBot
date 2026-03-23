"""AI-powered fight analysis — works with any OpenAI-compatible API.

Supports: OpenAI, MiniMax, Groq, Together AI, Mistral, OpenRouter,
Anthropic (via proxy), local Ollama, LM Studio, and any service that
implements the /v1/chat/completions endpoint.
"""

import logging
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Preset configurations for popular providers
PRESETS = {
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
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
    "Groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.1-8b-instant",
    },
    "Together AI": {
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3.1-8B-Instruct-Turbo",
    },
    "Mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-small-latest",
    },
    "OpenRouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "meta-llama/llama-3.1-8b-instruct:free",
    },
    "Ollama (Local)": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.1",
    },
    "LM Studio (Local)": {
        "base_url": "http://localhost:1234/v1",
        "default_model": "local-model",
    },
    "Custom": {
        "base_url": "",
        "default_model": "",
    },
}


class FightAnalyst:
    """Sends fight summary data to any OpenAI-compatible API for analysis."""

    @staticmethod
    def fetch_models(base_url: str, api_key: str = "", timeout: int = 10) -> list:
        """Fetch available models from the API's /models endpoint.

        Works with any OpenAI-compatible API (OpenAI, MiniMax, Groq,
        Together, Mistral, OpenRouter, Ollama, LM Studio, etc.)

        Returns:
            List of model ID strings, sorted alphabetically. Empty list on failure.
        """
        url = f"{base_url.rstrip('/')}/models"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                models = data.get("data", [])
                model_ids = sorted([
                    m.get("id", "").removeprefix("models/")
                    for m in models if m.get("id")
                ])
                return model_ids
            else:
                return []
        except Exception:
            return []

    def __init__(self, base_url: str, api_key: str, model: str,
                 system_prompt: str = None, max_tokens: int = 350):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt or self._default_system_prompt()

    def analyze(self, fight_summary: Dict[str, Any], timeout: int = 30) -> Optional[str]:
        """Send fight data to the configured LLM and return analysis text."""
        if not self.base_url or not self.model:
            logger.warning("AI analysis skipped — no base URL or model configured")
            return None

        prompt = self._build_prompt(fight_summary)
        endpoint = f"{self.base_url}/chat/completions"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Strip "models/" prefix that some providers include in model IDs
        model_name = self.model
        if model_name.startswith("models/"):
            model_name = model_name[7:]

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0.7,
        }

        # MiniMax-specific: suppress chain-of-thought output
        if "minimaxi" in self.base_url:
            payload["think_enable"] = False

        # Gemini-specific: disable thinking to preserve tokens for output
        if "googleapis.com" in self.base_url:
            payload["reasoning_effort"] = "none"

        try:
            logger.info(f"Requesting AI analysis from {self.base_url} using {model_name}")
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout,
            )

            if response.status_code == 200:
                data = response.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                if content:
                    import re

                    # Remove everything between <think> and </think> (including tags)
                    stripped = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

                    # Also handle unclosed <think> tag (model ran out of tokens mid-thought)
                    stripped = re.sub(r'<think>.*', '', stripped, flags=re.DOTALL).strip()

                    # Clean up any remaining stray </think> tags
                    stripped = re.sub(r'</think>', '', stripped).strip()

                    if stripped:
                        logger.info("AI analysis generated successfully")
                        return stripped

                    # FALLBACK: everything was inside think tags
                    inner = re.sub(r'</?think>', '', content).strip()
                    if inner:
                        # Take the last few sentences as the likely conclusion
                        sentences = [s.strip() for s in inner.replace('\n', ' ').split('.') if s.strip()]
                        if len(sentences) > 3:
                            return '. '.join(sentences[-4:]) + '.'
                        elif sentences:
                            return '. '.join(sentences) + '.'

                    logger.warning("AI response was empty after stripping think tags")
                    return None
                else:
                    logger.warning("AI response was empty")
                    return None
            else:
                logger.error(f"AI API error: {response.status_code} - {response.text[:300]}")
                return None

        except requests.Timeout:
            logger.error(f"AI API timed out after {timeout}s")
            return None
        except requests.ConnectionError:
            logger.error(f"AI API connection failed — is {self.base_url} reachable?")
            return None
        except Exception as e:
            logger.error(f"AI analysis failed: {e}")
            return None

    @staticmethod
    def _default_system_prompt() -> str:
        return (
            "You are SparkyBot, a Guild Wars 2 WvW fight analyst posting to Discord. You receive structured JSON fight statistics and respond with EXACTLY 2-4 sentences.\n\n"
            "PERSONALITY: You are a hype, unhinged sports commentator who's had four energy drinks and actually knows the WvW meta. When the squad wins, you are euphoric. When they lose, you are furious at the enemy or disappointed in the squad's play — never toxic toward individual squad players personally. PUGs are fair game though.\n\n"
            "UNDERSTANDING THE DATA:\n"
            "- \"squad_count\" = our organized guild squad members\n"
            "- \"ally_count\" = PUGs (Pick-Up Groups) — random players fighting alongside us who are NOT in our squad\n"
            "- Total friendly count = squad_count + ally_count\n"
            "- \"enemy_count\" = total enemies faced\n"
            "- When evaluating if a fight was impressive, compare total friendlies (squad + allies) vs enemy_count, NOT just squad_count vs enemy_count\n"
            "- A win where 50 friendlies (30 squad + 20 PUGs) beat 40 enemies is NOT impressive — that's a numbers advantage\n"
            "- A win where 30 friendlies beat 60 enemies IS impressive — that's punching up\n\n"
            "PUGS:\n"
            "- If the fight is a loss or draw, it's ENCOURAGED to blame the PUGs — they probably fed, didn't stay on tag, or facetanked without dodging\n"
            "- If the fight is a decisive win with lots of PUGs, the PUGs probably just followed the real squad's lead — credit goes to the organized players\n"
            "- If there are zero allies, this was a pure guild squad fight — give full credit or blame to the squad\n"
            "- High ally deaths relative to squad deaths means the PUGs were feeding\n\n"
            "TACTICAL ANALYSIS — evaluate these WvW-specific metrics:\n"
            "- KDR and kill efficiency relative to TOTAL friendly count vs enemy numbers\n"
            "- Boon strip totals (low strips = enemy boons went unchecked)\n"
            "- Cleanse totals (low cleanses = squad ate conditions)\n"
            "- Healing/barrier output (was sustain adequate for the fight length?)\n"
            "- Burst damage coordination (did top DPS players spike together?)\n"
            "- Enemy composition weaknesses (too many of one class, no support, etc.)\n"
            "- Squad deaths relative to fight duration (feeding or clean?)\n\n"
            "ENEMY DAMAGE ANALYSIS:\n"
            "- \"enemy_breakdown\" includes count, total damage, and damage_per_player for each enemy profession\n"
            "- \"enemy_total_damage\" is total damage dealt by all enemies combined\n"
            "- If enemy_total_damage > squad_damage, the enemy out-traded us — identify which professions hit hardest per-capita\n"
            "- High damage_per_player means that profession was individually dominant (e.g. 3 Scourges doing 500k each is scarier than 8 Guardians doing 250k each)\n"
            "- Call out enemy professions punching above their weight\n\n"
            "MOOD: Use the 'outcome' field to set your tone:\n"
            "- Decisive Win against superior numbers: MAXIMUM hype, this is legendary\n"
            "- Decisive Win with numbers advantage: tone it down, acknowledge it was expected\n"
            "- Win: confident, highlight what worked, one improvement note\n"
            "- Draw: frustrated energy, call out what went wrong, blame PUGs if ally_count is high\n"
            "- Loss: angry but constructive, identify the failure point, roast PUGs if they fed\n"
            "- Decisive Loss: full tilt, dramatic, demand improvement, PUGs get absolutely roasted\n\n"
            "RULES:\n"
            "- ONLY output the final analysis text\n"
            "- NO bullet points, lists, or stat breakdowns\n"
            "- NO repeating raw numbers — the reader sees the stats above your analysis\n"
            "- Mock game performance (low cleanses, bad positioning, PUG feeding) not squad players personally\n"
            "- 2-4 sentences MAXIMUM\n"
            "- Your ENTIRE response must be under 600 characters total. This is a hard limit.\n"
            "- NO markdown formatting\n"
            "- Use gaming slang and occasional caps for emphasis\n\n"
            "EXAMPLE OUTPUT (for a Decisive Win outnumbered):\n"
            "\"FORTY-NINE KILLS AND THREE DEATHS WHILE OUTNUMBERED 2 TO 1?! Someone call the Red team's commander because that blob just got sent to the shadow realm — Celestial Fluggy dropped 1.4 mil like a one-man siege engine while the backline pumped out cleanses fast enough to make conditions a non-factor. The enemy brought 6 Tempests and zero coordination, and it showed.\"\n\n"
            "EXAMPLE OUTPUT (for a Loss with PUGs):\n"
            "\"We had the numbers and STILL lost — 15 PUGs running around like headless chickens while the enemy Scourge ball just farmed them for free rally. The actual squad held it together but you can't win a fight when half your 'army' is autoattacking in soldier's gear from 1200 range.\""
        )

    def _build_prompt(self, summary: Dict[str, Any]) -> str:
        """Build a JSON prompt from fight data."""
        import json
        return json.dumps(summary, indent=2)
