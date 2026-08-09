import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.discord_bot import normalize_webhook_url, validate_webhook_url


def test_discord_webhook_requires_complete_url():
    assert not validate_webhook_url("token-fragment-without-a-url")
    assert not validate_webhook_url("https://discord.com/api/webhooks/")
    assert not validate_webhook_url("http://discord.com/api/webhooks/123/token")
    assert validate_webhook_url("https://discord.com/api/webhooks/123/token")


def test_blank_webhook_is_allowed_for_unused_slots():
    assert validate_webhook_url("")


def test_id_and_token_shorthand_is_converted_to_full_url():
    shorthand = "123456789012345678/secret-token"
    assert normalize_webhook_url(shorthand) == (
        "https://discord.com/api/webhooks/123456789012345678/secret-token"
    )


def test_token_alone_cannot_be_converted_without_webhook_id():
    assert normalize_webhook_url("secret-token-without-id") is None
