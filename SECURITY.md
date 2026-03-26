# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 1.6.1   | ✅ Current release  |
| 1.6.0   | ⚠️ Critical fixes only |
| 1.5.x   | ⚠️ Critical fixes only |
| < 1.5   | ❌ No longer supported |

## Reporting a Vulnerability

If you discover a security vulnerability in SparkyBot, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please use one of the following methods:

- **GitHub Private Vulnerability Reporting:** Use the [Security Advisories](https://github.com/SimpleHonors/SparkyBot/security/advisories/new) page to report privately.
- **Direct Contact:** Reach out via Discord to **SimpleHonors** on the SparkyBot Discord server.

### What to Include

- A description of the vulnerability and its potential impact
- Steps to reproduce the issue
- The version of SparkyBot affected
- Any relevant log output (with sensitive data like API keys or tokens redacted)

### What to Expect

- **Acknowledgment** within 48 hours of your report
- **Status update** within 7 days with an assessment and remediation timeline
- **Credit** in the release notes (unless you prefer to remain anonymous)

## Scope

The following are in scope for security reports:

- **API key / token exposure** — any scenario where Discord webhook URLs, Twitch OAuth tokens, or AI provider API keys could be leaked through logs, error messages, config files, or network traffic
- **Config file security** — `config.properties` stores sensitive credentials in plaintext; reports about unauthorized access vectors are welcome
- **Self-updater integrity** — vulnerabilities in the auto-update mechanism (e.g., man-in-the-middle, unsigned downloads, malicious zip extraction)
- **Code injection** — any path where user-supplied data (file names, log content, AI responses) could lead to code execution
- **Dependency vulnerabilities** — critical CVEs in Python packages listed in `requirements.txt`

The following are out of scope:

- **ArcDPS, GW2 Elite Insights, or Guild Wars 2** — these are third-party tools; report issues to their respective maintainers
- **AI provider security** — vulnerabilities in OpenAI, MiniMax, Groq, or other LLM provider APIs should be reported to those providers
- **Social engineering or phishing** — attacks requiring user deception beyond normal software usage
- **Denial of service via log flooding** — SparkyBot processes files as they arrive; overwhelming it with files is expected behavior in high-volume WvW sessions

## Security Considerations

### Credentials Storage

SparkyBot stores the following sensitive data in `config.properties` (plaintext):

- Discord webhook URLs
- Twitch OAuth token
- AI provider API key

Users should protect this file with appropriate filesystem permissions. SparkyBot does not transmit these credentials to any server other than their intended destination (Discord API, Twitch IRC, configured AI provider).

### Network Communication

SparkyBot communicates with the following external services:

| Service | Purpose | Protocol |
|---------|---------|----------|
| Discord API (`discord.com`) | Posting fight reports | HTTPS |
| Twitch IRC (`irc.chat.twitch.tv`) | Posting chat messages | TLS (port 6697, default) or plaintext (port 6667, optional) |
| AI Provider (user-configured) | Fight commentary | HTTPS |
| GitHub API (`api.github.com`) | Update checks | HTTPS |
| GitHub Releases (`github.com`) | Downloading updates | HTTPS |

Note: Twitch IRC defaults to TLS on port 6697, encrypting the OAuth token in transit. Users can disable TLS in Settings → Messaging if they experience connection issues behind restrictive firewalls, which falls back to plaintext on port 6667. A warning is displayed in the GUI when TLS is disabled.

### Auto-Updater

The self-updater downloads release zips from GitHub over HTTPS. Downloads are validated by checking that the zip contains expected files before extraction. The updater only overwrites files that differ in content from the zip (verified by hash), and protects `config.properties` and the `GW2EI/` directory from modification.
