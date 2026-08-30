# Setup: AI Fight Commentary

A setup-wizard screen that appears only if you chose **Yes — set up AI commentary** on the [Want AI commentary?](setup-ai-optin.md) screen. It connects SparkyBot to an AI service that writes entertaining commentary after each battle. Works with cloud services (OpenAI, Google Gemini, Groq, and others) or programs running on your own computer (Ollama, LM Studio).

**Quick Start (recommended):** the easiest free option is Google Gemini. Create a free API key (link on the page), select Gemini as the provider, paste the key, and you're done.

## What's on this screen

- **Provider** — pick the AI service you want. Choosing one fills in the web address and model list for you.
- **Base URL** — the service's web address. Filled automatically for known providers; only change it for a self-hosted or unusual setup.
- **API Key** — the key from your provider. Hidden as you type. Leave blank for local programs like Ollama, which don't need one.
- **Model** — which AI model to use. Pick from the list or type a name.
- **Refresh** (next to Model) — fetches the live model list from your provider, using the Base URL and API Key above. If the service can't be reached, the provider's built-in list is used.
- **Max Tokens** — a cap on how long each commentary can be. The default is fine; **Test Connection** adjusts it automatically when needed.
- **Disable Thinking / Reasoning Mode** — some models "think" silently before answering and can burn their whole word budget doing it, leaving cut-off commentary. Tick this if responses come out truncated. **Test Connection** works this out for you automatically.
- **Test Connection** — sends a sample fight to your AI and shows the result. It also probes how your model handles thinking mode and quietly applies the best settings. If the model works well both ways, a small **Choose reasoning mode** dialog lets you pick; click **Apply** to use your choice.
- **Get API Key links** — shortcuts to the key pages for Google Gemini (free tier), OpenAI, Groq (free tier), OpenRouter, and a download link for Ollama (local, free, no key needed).
- **Skip AI setup for now** — continue without finishing this; you can complete it later in **Settings → AI Commentary**.

## Common problems

- **"Enter a Base URL and Model first."** — pick a Provider (which fills the Base URL) and choose a Model, then test again.
- **Test fails with an authentication error** — the API key is wrong, expired, or for a different service. Create a fresh key at the provider link on the page and paste it again.
- **"No models found — type a model name manually"** — the provider didn't answer the model-list request. You can still type the model name yourself (copy it from the provider's documentation).
- **Commentary comes out cut off mid-sentence** — run **Test Connection** so the reasoning probe can fix it, or tick **Disable Thinking / Reasoning Mode** yourself.
- **Local model (Ollama / LM Studio) not found** — make sure the program is running before you test, and leave the API Key blank.
