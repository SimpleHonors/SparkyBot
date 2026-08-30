# Settings: Twitch

Posting fight summaries (and AI commentary, when enabled) to your Twitch chat while you stream.

## What's on this page

- **Enable Twitch Bot** — the master switch. Off grays out the group below.

### Twitch chat

- **Channel Name** — the Twitch channel messages are posted to (the part after `twitch.tv/`).
- **Bot Token** — the chat token for the account that speaks (starts with `oauth:`, hidden as you type). Get a free one at twitchtokengenerator.com — choose **Bot Chat Token**.
- **Use secure connection (TLS)** — encrypts your token in transit; leave it on. When it's off, a note warns that the token is sent in plaintext — only turn TLS off if secure connections fail because of a firewall or network restriction.
- **Test Connection** — connects with the current channel and token and reports the result on the spot.

## Common problems

- **"Enter a channel name and bot token first."** — fill in both fields, then click **Test Connection**.
- **Test fails** — usually a mistyped channel name or an expired/incorrect token. Generate a fresh Bot Chat Token and paste it again.
- **Bot posts as the wrong account** — the token decides who speaks. Generate the token while logged into the account you want the bot to use.
- **Nothing appears in chat during a stream** — check **Enable Twitch Bot** is on, and that fights are actually being posted (Home activity feed).
