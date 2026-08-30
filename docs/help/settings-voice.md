# Settings: Voice

Reading the AI fight commentary out loud. This page exists only while **Settings → Application → AI features** is on.

## What's on this page

- **Play AI commentary through speakers** — the master switch for local playback. Off grays out the groups below.

### Playback

- **Attach audio file to Discord post** — include the spoken commentary on the Discord post as a playable audio file. Note: Discord mobile does not support inline audio playback for file attachments.
- **Volume** (%) — local playback volume (0 = muted, 100 = full).

### Voice service

- **Provider** — **edge** (free Microsoft neural voices, no key, online), **elevenlabs** (highest quality, API key required), or **local** (self-hosted OpenAI-compatible speech server — voice cloning, free, private).

Shown for **edge**:
- **Edge Voice** — the voice name (e.g. `en-GB-RyanNeural`); **Refresh Voices** fetches the list.

Shown for **elevenlabs**:
- **API Key** and **Voice ID** — from your ElevenLabs account; browse voices at elevenlabs.io/app/voice-library.
- **Model** — which ElevenLabs speech model to use.
- **Stability** (%) — low = widest emotional range, high = most consistent (monotone at extremes). 30–40% suits a commentator voice.
- **Similarity** (%) — how closely output sticks to the original voice recording; very high can reproduce recording artifacts. 70–80% recommended.
- **Style** (%) — amplifies the voice's characteristic style; non-zero adds a little latency. 10–20% for dramatic delivery, 0% for neutral.
- **Use Speaker Boost** — extra similarity to the original speaker at a minor latency cost; generally keep on.
- **Speed** — speech rate multiplier (0.7 slowest to 1.2 fastest; 1.0 normal). 1.05–1.15 suits an energetic delivery.

Shown for **local**:
- **Server URL** — your speech server's address (e.g. `http://127.0.0.1:5820`).
- **Voice** — pick or type a voice; **Refresh** fetches the server's list.
- **Upload Sample...** — upload a short (3+ seconds) clean recording; it becomes a pickable voice cloned at generation time. Only upload voices you own or have consent to clone.

### Test

- **Test TTS** — generates (and plays) a sample line with the current settings; the status line reports the result.

## Common problems

- **No sound at all** — check **Play AI commentary through speakers** is on and **Volume** isn't 0; then check Windows' own volume mixer.
- **"Failed to fetch voices: …"** — for edge, an internet hiccup; for local, wrong **Server URL** or the server isn't running.
- **"Set the local server URL first."** — fill in **Server URL** before refreshing or uploading.
- **Audio attached but silent on phones** — Discord mobile can't play attached audio inline; listeners need the desktop app or browser.
- **Voice sounds flat or unstable (ElevenLabs)** — start from the recommended ranges above: Stability 30–40%, Similarity 70–80%, Style 10–20%.
