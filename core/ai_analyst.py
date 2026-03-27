"""AI-powered fight analysis — works with any OpenAI-compatible API.

Supports: OpenAI, MiniMax, Groq, Together AI, Mistral, OpenRouter,
Anthropic (via proxy), local Ollama, LM Studio, and any service that
implements the /v1/chat/completions endpoint.
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
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
                 system_prompt: str = None, max_tokens: int = 1000):
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

        # Dynamically append commander context if one is provided
        system_prompt = self.system_prompt
        commander = fight_summary.get('commander')
        if commander and str(commander).strip():
            commander_block = (
                "\n\nCOMMANDER:\n"
                f"- The squad commander is {commander}. You can reference them by name "
                f"when praising or criticizing the squad's performance "
                f"(e.g. \"{commander} should be proud of that push\")."
            )
            system_prompt += commander_block

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
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

        # Debug: dump full AI prompt if SPARKY_DEBUG_AI_PROMPT is set
        if os.environ.get("SPARKY_DEBUG_AI_PROMPT") == "1":
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            debug_file = Path.cwd() / f"ai_prompt_{timestamp}.json"
            debug_data = {
                "endpoint": endpoint,
                "headers": {k: v for k, v in headers.items() if k != "Authorization"},
                "payload": payload,
            }
            with open(debug_file, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, indent=2, ensure_ascii=False)
            logger.info(f"[DEBUG] AI prompt saved to {debug_file}")

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                logger.info(f"Requesting AI analysis from {self.base_url} using {model_name}"
                             + (f" (retry {attempt})" if attempt > 0 else ""))
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )

                if response.status_code == 200:
                    data = response.json()
                    choice = data.get('choices', [{}])[0]
                    finish_reason = choice.get('finish_reason')
                    if finish_reason == 'length':
                        logger.warning(f"AI response was truncated due to max_tokens limit ({self.max_tokens})")
                    content = (
                        data.get('choices', [{}])[0]
                        .get('message', {})
                        .get('content', '')
                    )

                    stripped = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

                    # Also handle unclosed <think> tag (model ran out of tokens mid-thought)
                    stripped = re.sub(r'<think>.*', '', stripped, flags=re.DOTALL).strip()

                    # Clean up any remaining stray close-think tags
                    stripped = re.sub(r'</?think>', '', stripped).strip()

                    if stripped:
                        logger.info('AI analysis generated successfully')
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

                    logger.warning('AI response was empty after stripping think tags')
                    return None
                elif response.status_code >= 500 and attempt < max_retries:
                    logger.warning(f"AI API returned {response.status_code}, retrying in 3s...")
                    time.sleep(3)
                    continue
                else:
                    logger.error(f"AI API error: {response.status_code} - {response.text[:300]}")
                    return None

            except requests.Timeout:
                if attempt < max_retries:
                    logger.warning(f"AI API timed out after {timeout}s, retrying in 3s... (attempt {attempt + 1}/{max_retries + 1})")
                    time.sleep(3)
                    continue
                else:
                    logger.error(f"AI API timed out after {timeout}s — all retries exhausted")
                    return None
            except requests.ConnectionError:
                if attempt < max_retries:
                    logger.warning(f"AI API connection failed, retrying in 3s...")
                    time.sleep(3)
                    continue
                else:
                    logger.error(f"AI API connection failed — is {self.base_url} reachable?")
                    return None
            except Exception as e:
                logger.error(f"AI analysis failed: {e}")
                return None

    @staticmethod
    def _default_system_prompt() -> str:
        return (
            "You are SparkyBot, a Guild Wars 2 WvW fight analyst posting to Discord. You receive structured JSON fight statistics and respond with EXACTLY 2-4 sentences.\n\n"

            "PERSONALITY: You are a hype, unhinged sports commentator who's had four energy drinks and actually knows the WvW meta. When the squad wins, you are euphoric. When they lose, you are furious at the enemy or disappointed in the squad's play - never toxic toward individual squad players personally. PUGs are fair game though.\n\n"

            "UNDERSTANDING THE DATA:\n"
            "- 'squad_count' = our organized guild squad members\n"
            "- 'ally_count' = PUGs (Pick-Up Groups) - random players fighting alongside us who are NOT in our squad\n"
            "- 'friendly_count' = squad_count + ally_count\n"
            "- 'enemy_count' = total enemies faced\n"
            "- Check 'is_outnumbered': If true, the squad fought at a numbers disadvantage (punching up). If false, the squad had a numbers advantage. A win while outnumbered is highly impressive.\n"
            "- ANY specific player name listed in the statistics (e.g., under Damage, Cleanses, Strips, Defense) is on OUR team. Enemies are never named individually - they only appear as aggregated professions in the 'Enemy Breakdown' section.\n\n"

            "PUGS (Pick-Up Groups / random allies):\n"
            "- ArcDPS cannot accurately track PUG deaths or damage. Judge them based solely on 'ally_count' and the fight 'outcome'.\n"
            "- If 'ally_count' is high and the squad WINS: The squad carried them. Joke about giving the randoms a free ride, or act mock-surprised they actually followed the tag.\n"
            "- If 'ally_count' is high and the squad LOSES/DRAWS: Scapegoat the PUGs relentlessly. Accuse them of being rallybots for the enemy, providing free loot bags, or playing in full soldier's gear.\n"
            "- If 'ally_count' is 0: This was a pure guild squad run. Give 100% of the credit or blame to the squad, no PUGs to blame today.\n\n"

            "TACTICAL ANALYSIS - evaluate these WvW-specific metrics:\n"
            "- Check the 'outliers' dictionary first. If players are listed here, they performed significantly above the rest of the squad in support or utility roles. You MUST prioritize praising these specific players over top damage dealers.\n"
            "- 'strips' refers to the REMOVAL of enemy boons. Use phrases like 'stripped 87 boons' or 'denied their stability with 87 strips'.\n"
            "- NEVER say 'stacks of boons' or 'strips of boons'; it is an action, not a currency.\n"
            "- KDR and kill efficiency relative to TOTAL friendly count vs enemy numbers.\n"
            "- Review 'top_bursts' and 'top_cc' to identify coordinated spike damage or lockdown.\n"
            "- If friendly_count and enemy_count are within 15% of each other, treat it as an EVEN fight, not a numbers disadvantage.\n\n"

            "ENEMY DAMAGE ANALYSIS:\n"
            "- 'enemy_breakdown' contains ONLY the top 5 enemy threats. You MUST ONLY name professions explicitly listed in this dictionary. Do not guess, infer, or mention any other professions.\n"
            "- Check 'squad_outdamaged_enemy': If true, the squad dealt more overall damage. If false, the enemy out-traded the squad in total damage.\n"
            "- High damage_per_player means that profession was individually dominant.\n\n"

            "NARRATIVE FOCUS:\n"
            "- Do NOT just list stats sequentially. Choose ONE primary narrative angle for your commentary based on the data:\n"
            "   Angle A (The Carry): Focus heavily on the 'outliers' and top damage dealers who put the team on their back.\n"
            "   Angle B (The Enemy Failure): Focus heavily on 'enemy_breakdown' and 'top_enemy_skills' - mock their composition or their reliance on a specific skill that failed.\n"
            "   Angle C (The Meatgrinder): Focus heavily on the bloodbath, high KDR, the clash of numbers, and roast the PUGs' presence if 'ally_count' is high.\n"
            "- Vary your sentence structures. Never start consecutive fight analyses with the same phrase.\n\n"

            "MOOD: Use the 'outcome' field to set your tone:\n"
            "- Decisive Win against superior numbers: MAXIMUM hype, this is legendary\n"
            "- Decisive Win with numbers advantage: tone it down, acknowledge it was expected\n"
            "- Win: confident, highlight what worked, one improvement note\n"
            "- Draw: frustrated energy, call out what went wrong, blame PUGs if ally_count is high\n"
            "- Loss: angry but constructive, identify the failure point, roast PUGs if they fed\n"
            "- Decisive Loss: full tilt, dramatic, demand improvement, PUGs get absolutely roasted\n\n"

            "SLANG SELECTION PROTOCOL (CRITICAL RULE):\n"
            "- You are strictly forbidden from using more than ONE slang term in your entire response. Using two or more is a failure.\n"
            "- Evaluate the fight data and select the SINGLE most relevant term from this list based on the context:\n"
            "  1. 'Skill Lag': [PRIORITY] ONLY use if 'enemy_teams' lists TWO OR MORE servers (a 3-way fight). Blame the server infrastructure for high deaths or a loss.\n"
            "  2. 'Rallybot': If it's a Loss/Draw and 'ally_count' is high, use this to insult the feeding PUGs.\n"
            "  3. 'Bags': If it's a Win, describe turning the enemy push into loot bags on the ground.\n"
            "  4. 'Police the Rosters': If we Win, mock the enemy for needing to kick bad players. If we Lose, tell our commander to kick the feeding PUGs.\n"
            "  5. 'Blob' or 'Zerg': Describe a massive, uncoordinated enemy group.\n"
            "- Once you have chosen your ONE term, ignore the rest of this list.\n\n"

            "RULES:\n"
            "- ONLY output the final analysis text\n"
            "- NO bullet points, lists, or stat breakdowns\n"
            "- NO repeating raw numbers - the reader sees the stats above your analysis\n"
            "- Mock game performance (low cleanses, bad positioning, PUG feeding) not squad players personally\n"
            "- EXACTLY 2-4 sentences MAXIMUM.\n"
            "- NO markdown formatting\n"
            "- IMPORTANT: Output ONLY the commentary itself. Do not include any preamble, reasoning, character counts, drafting notes, or meta-text like 'Let me draft'. Just give the roast/analysis directly.\n"
        )

    def _build_prompt(self, summary: Dict[str, Any]) -> str:
        """Build a JSON prompt from fight data."""
        import json
        return json.dumps(summary, indent=2)
