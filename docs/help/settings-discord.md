# Settings: Discord

Where your reports go. Open Settings from the sidebar entry, **File → Settings...**, or `Ctrl+,`. Changes apply when you click **OK** or **Apply**.

## What's on this page

- **Enable Discord Bot** — the master switch for all Discord posting. Off grays out everything below; SparkyBot still processes fights and builds summaries, it just posts nothing.

### Webhooks

Up to three destinations (channels), each a name plus a webhook URL:

- **Destination 1/2/3 name** — a friendly label so you remember which channel it is (e.g. "Main WvW", "Fight Summaries", "Officers").
- **Destination 1/2/3 webhook** — the webhook URL for that channel (`https://discord.com/api/webhooks/...`). Create webhooks in Discord under **Server Settings → Integrations → Webhooks**.
- **Fight reports** — which destination individual fight posts go to.
- **Fight Summary** — which destination the end-of-run Combined Fight Log Summary goes to. **Same as fight reports** uses one channel for everything; pick another destination to send summaries somewhere else.
- **Bot name** — the display name on SparkyBot's Discord posts.

### Uploads

- **Max upload size** (MB) — fight logs larger than this are not attached to Discord posts (the report itself still posts).
- **Upload Large Files After Parsing** — attach a large log after parsing finishes instead of skipping it.

## Common problems

- **Nothing posts to Discord** — check **Enable Discord Bot** is ticked and Destination 1 has a valid webhook.
- **Summaries land in the wrong channel** — the **Fight Summary** drop-down decides that; set it to a destination whose webhook points at the right channel.
- **Webhook pasted but rejected** — copy the *whole* URL with Discord's **Copy Webhook URL** button; a token alone is missing the webhook ID.
- **Attachments missing on big fights** — raise **Max upload size** or tick **Upload Large Files After Parsing**. Discord's own upload limit still applies.
