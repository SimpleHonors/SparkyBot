# Setup: Voice / Text-to-Speech

A setup-wizard screen that appears only if you opted into AI commentary. SparkyBot can read the AI fight commentary out loud. Audio can play through your speakers and/or be attached to the Discord post as an inline audio player.

## What's on this screen

- **Play AI commentary through speakers** — speak each commentary on this computer as it's written.
- **Attach audio to Discord post** — include the spoken commentary as an audio file on the Discord post, so guildmates can play it.
- **Provider** — which voice service to use:
  - **edge** — free Microsoft neural voices, no key needed (recommended).
  - **elevenlabs** — premium-quality voices; requires a paid ElevenLabs API key.
  - **local** — your own speech server (OpenAI-compatible), for private, free voice cloning.

### Edge fields (shown when "edge" is selected)

- **Voice** — the voice name (default `en-GB-RyanNeural`). Pick from the list or type one.
- **Refresh Voices** — downloads the current list of English voices to choose from.

### ElevenLabs fields (shown when "elevenlabs" is selected)

- **API Key** — from your ElevenLabs account (link on the page).
- **Voice ID** — the ID of the voice you want; browse voices at the ElevenLabs voice library (link on the page).

### Local server fields (shown when "local" is selected)

- **Server URL** — the address of your speech server, for example `http://127.0.0.1:5820`.
- **Voice** — pick or type a voice the server offers; **Refresh** fetches the list.
- **Add Voice...** — upload a short, clean recording (at least 3 seconds; WAV, MP3, M4A, or FLAC). The server adds it as a voice you can select. Only use a voice you own or have permission to clone.

### Try it

- **Test Voice** — generates a short sample line with the current settings. If **Play AI commentary through speakers** is ticked it plays out loud; otherwise the status just confirms the provider works.
- **Skip voice setup for now** — continue without voice; finish later in **Settings → Voice**.

Note: voice requires AI Fight Commentary — the audio is generated from the commentary text.

## Common problems

- **"Audio generation failed. Check provider settings and logs."** — for edge, check your internet connection; for elevenlabs, check the API key and voice ID; for local, make sure the server is running at the URL you entered.
- **"Set the local server URL first."** — fill in **Server URL** before refreshing voices or adding one.
- **"Could not add voice: …"** — the recording was rejected. Use a clean recording of at least 3 seconds in WAV, MP3, M4A, or FLAC, and give the voice a simple name (letters, digits, `. _ -`).
- **Test works but you hear nothing** — tick **Play AI commentary through speakers**; without it the test only generates the audio silently.
- **No audio on Discord mobile** — Discord's mobile apps don't play attached audio inline; this is a Discord limitation, not a SparkyBot setting.
