# Settings: AI Commentary

The AI connection and how the commentary is written. This page exists only while **Settings → Application → AI features** is on.

## What's on this page — AI service

- **Provider** — the AI service to use (cloud services like Google Gemini, OpenAI, Groq, OpenRouter, or local programs like Ollama and LM Studio). Picking one fills in the address and model list.
- **Model** — which model writes the commentary. Pick from the list or type a name.
- **API Base URL** — the service's web address; filled automatically for known providers. **Refresh Models** next to it fetches that provider's live model list.
- **API Key** — your key for the service (hidden as you type). Leave blank for local models.
- **Max Tokens** — a cap on the commentary's length.
- **API Timeout** (seconds) — how long to wait for the AI before giving up on a fight's commentary.
- **Disable Thinking / Reasoning Mode** — some models "think" silently first and can spend their whole word budget on it, leaving truncated commentary. Tick this if responses are cut off. Affects models like Kimi, DeepSeek, Gemini, and reasoning-capable models routed through OpenRouter.
- **System Prompt** — the AI's personality:
  - **Default (SparkyBot Analyst)** — SparkyBot builds the prompt dynamically each call with vocabulary dice rolls, pre-computed fight analysis, and variety tracking.
  - **Custom** — your own prompt is used as-is for the system message. Fight data, pre-analysis, and vocabulary are still supplied with each request. **Edit System Prompt...** opens a bigger editor; leave it blank to use the built-in default.
- **Test Connection** — sends a sample fight and shows the commentary. It also probes thinking mode both ways and auto-applies the best Max Tokens / thinking settings. If both modes work, a **Choose reasoning mode** dialog lets you pick, with an **Apply** button.

How often the AI uses catchphrases lives on the [Vocabulary](settings-vocabulary.md) page; reading commentary aloud lives on the [Voice](settings-voice.md) page.

## Common problems

- **"Enter a Base URL first"** — pick a Provider or fill in the address before refreshing models.
- **"No models found — type a model name manually"** — the provider didn't return a list; type the model's name from its documentation.
- **Commentary cut off mid-sentence** — run **Test Connection** to auto-fix, or tick **Disable Thinking / Reasoning Mode** and/or raise **Max Tokens**.
- **Commentary missing on some fights** — the AI took longer than **API Timeout**; raise it, or use a faster model.
- **Custom prompt ignored** — the mode must be set to **Custom**; on **Default (SparkyBot Analyst)** your text isn't used.
