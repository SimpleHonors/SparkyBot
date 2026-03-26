Reporting a Vulnerability
If you discover a security vulnerability in SparkyBot, please report it responsibly.
Do NOT open a public GitHub issue for security vulnerabilities.
Instead, please use one of the following methods:

GitHub Private Vulnerability Reporting: Use the Security Advisories page to report privately.
Direct Contact: Reach out via Discord to SimpleHonors on the SparkyBot Discord server.

What to Include

A description of the vulnerability and its potential impact
Steps to reproduce the issue
The version of SparkyBot affected
Any relevant log output (with sensitive data like API keys or tokens redacted)

What to Expect

Acknowledgment within 48 hours of your report
Status update within 7 days with an assessment and remediation timeline
Credit in the release notes (unless you prefer to remain anonymous)

Scope
The following are in scope for security reports:

API key / token exposure — any scenario where Discord webhook URLs, Twitch OAuth tokens, or AI provider API keys could be leaked through logs, error messages, config files, or network traffic
Config file security — config.properties stores sensitive credentials in plaintext; reports about unauthorized access vectors are welcome
Self-updater integrity — vulnerabilities in the auto-update mechanism (e.g., man-in-the-middle, unsigned downloads, malicious zip extraction)
Code injection — any path where user-supplied data (file names, log content, AI responses) could lead to code execution
Dependency vulnerabilities — critical CVEs in Python packages listed in requirements.txt

The following are out of scope:

ArcDPS, GW2 Elite Insights, or Guild Wars 2 — these are third-party tools; report issues to their respective maintainers
AI provider security — vulnerabilities in OpenAI, MiniMax, Groq, or other LLM provider APIs should be reported to those providers
Social engineering or phishing — attacks requiring user deception beyond normal software usage
Denial of service via log flooding — SparkyBot processes files as they arrive; overwhelming it with files is expected behavior in high-volume WvW sessions

Security Considerations
Credentials Storage
SparkyBot stores the following sensitive data in config.properties (plaintext):

Discord webhook URLs
Twitch OAuth token
AI provider API key

Users should protect this file with appropriate filesystem permissions. SparkyBot does not transmit these credentials to any server other than their intended destination (Discord API, Twitch IRC, configured AI provider).
Network Communication
SparkyBot communicates with the following external services:
ServicePurposeProtocolDiscord API (discord.com)Posting fight reportsHTTPSTwitch IRC (irc.chat.twitch.tv)Posting chat messagesIRC (plaintext, port 6667)AI Provider (user-configured)Fight commentaryHTTPSGitHub API (api.github.com)Update checksHTTPSGitHub Releases (github.com)Downloading updatesHTTPS
Note: Twitch IRC uses plaintext on port 6667. OAuth tokens are sent over this connection. This is standard for Twitch IRC bots and matches the protocol used by Twitch4J, PlenBot, and other community tools.
Auto-Updater
The self-updater downloads release zips from GitHub over HTTPS. Downloads are not cryptographically verified beyond the HTTPS transport layer. The updater protects config.properties and the GW2EI/ directory during updates.
