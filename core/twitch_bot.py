"""Twitch chat integration — sends fight summaries to a Twitch channel via IRC."""

import logging
import socket
import time

logger = logging.getLogger(__name__)


class TwitchBot:
    """Sends messages to a Twitch chat channel via IRC."""

    def __init__(self, token: str, channel: str):
        self.token = token if token.startswith("oauth:") else f"oauth:{token}"
        self.channel = channel.lower().lstrip("#")
        self._sock = None

    def _connect(self):
        """Connect to Twitch IRC."""
        self._sock = socket.socket()
        self._sock.settimeout(10)
        self._sock.connect(("irc.chat.twitch.tv", 6667))
        self._sock.send(f"PASS {self.token}\r\n".encode("utf-8"))
        self._sock.send(f"NICK sparkybot\r\n".encode("utf-8"))
        self._sock.send(f"JOIN #{self.channel}\r\n".encode("utf-8"))
        # Read until we get confirmation
        response = self._sock.recv(4096).decode("utf-8", errors="ignore")
        logger.debug(f"Twitch IRC connect response: {response[:200]}")

    def send_message(self, text: str):
        """Send a message to the configured Twitch channel."""
        if not self.channel or not self.token:
            return

        # Truncate to 500 chars (Twitch limit)
        if len(text) > 500:
            # Truncate at last sentence boundary
            truncated = text[:497]
            last_period = truncated.rfind('.')
            last_bang = truncated.rfind('!')
            last_q = truncated.rfind('?')
            best = max(last_period, last_bang, last_q)
            if best > 200:
                text = truncated[:best + 1]
            else:
                text = truncated + "..."

        try:
            if self._sock is None:
                self._connect()

            self._sock.send(f"PRIVMSG #{self.channel} :{text}\r\n".encode("utf-8"))
            logger.info(f"Twitch message sent to #{self.channel}")
            time.sleep(3)  # Rate limiting

        except Exception as e:
            logger.error(f"Twitch send failed: {e}")
            self._sock = None  # Reset connection on failure
            raise

    def close(self):
        """Close the IRC connection."""
        if self._sock:
            try:
                self._sock.send(b"QUIT\r\n")
                self._sock.close()
            except Exception:
                pass
            self._sock = None
