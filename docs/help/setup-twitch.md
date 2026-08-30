# Setup: Twitch Integration (Optional)

A setup-wizard screen. SparkyBot can post fight summaries (and AI commentary, if enabled) to your Twitch chat in real time while you stream. Completely optional — most people skip it.

## What's on this screen

- **Enable Twitch Bot** — the master switch for Twitch posting.
- **Channel Name** — your Twitch channel name (the part after `twitch.tv/`).
- **Bot Token** — a Twitch chat token for the account that will do the posting. It starts with `oauth:` and is hidden as you type.
- **Use secure connection (TLS)** — on by default; keeps your token encrypted on the way to Twitch. Leave it on unless connections fail on your network.
- **Get a free token at twitchtokengenerator.com** — a link to a free token generator. Select **Bot Chat Token** when generating; the token gives SparkyBot permission to send messages to your channel.
- **Skip Twitch setup for now** — move on without Twitch. You can set it up later in **Settings → Twitch**.

## Common problems

- **Messages never appear in chat** — check the channel name has no typos and the token starts with `oauth:`. You can verify both later with the **Test Connection** button in **Settings → Twitch**.
- **Token stopped working** — tokens can expire or be revoked. Generate a new one (Bot Chat Token) and paste it into **Settings → Twitch → Bot Token**.
- **Streaming from a different account than the bot** — that's fine: the **Channel Name** is where messages go; the **Bot Token** decides which account speaks.
