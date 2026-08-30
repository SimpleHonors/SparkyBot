# Setup: Discord Webhook

A setup-wizard screen. A webhook is a special web address that lets SparkyBot post messages into one of your Discord server's channels — no bot account needed. This screen is where you paste it.

To create one in Discord: **Server Settings → Integrations → Webhooks → New Webhook**, pick the channel, then **Copy Webhook URL**.

## What's on this screen

- **Webhook URL** — paste the address here. It looks like `https://discord.com/api/webhooks/...`. SparkyBot tidies up common paste mistakes (extra spaces, an ID/token pair instead of a full URL) automatically.
- **Skip Discord setup for now** — tick this to finish setup without Discord. SparkyBot still processes fights and builds summaries; nothing is posted anywhere until you add a webhook later in **Settings → Discord**.

If you loaded a guild setup file on the Welcome screen, this field is already filled in for you.

## Common problems

- **"Discord Webhook Needed"** — the field is empty and Skip isn't ticked. Paste the webhook from your guild admin, or tick **Skip Discord setup for now**.
- **"Incomplete Discord Webhook"** — you pasted only the token part. In Discord, open **Server Settings → Integrations → Webhooks**, choose your webhook, and click **Copy Webhook URL** to get the whole thing.
- **"Discord Destination Needed"** (after importing from another log tool) — the imported tool didn't include complete Discord routing. Paste one webhook so SparkyBot can post both individual fights and the end-of-session summary; you can split them into separate channels later in **Settings → Discord**.
- **Posts go to the wrong channel** — the channel is chosen when the webhook is created in Discord. Make a webhook for the right channel and paste that URL instead.
