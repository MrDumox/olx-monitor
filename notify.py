import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

# Webhook Discorda przyjmuje ok. 30 wiadomości/min na kanał.
DELAY_BETWEEN_MESSAGES = 2


def discord_webhook_url() -> str | None:
    return os.environ.get("DISCORD_WEBHOOK_URL", "").strip() or None


def send_discord(webhook_url: str, content: str = "", embed: dict | None = None) -> bool:
    payload = {
        "content": content,
        "embeds": [embed] if embed else [],
        # Tytuł ogłoszenia z "@everyone" nie powinien nikogo pingować.
        "allowed_mentions": {"parse": []},
    }
    for _ in range(2):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=15)
        except requests.RequestException as e:
            # Treść wyjątku zawiera URL webhooka (a w nim token), więc logujemy tylko typ błędu.
            logger.error("Discord: błąd połączenia (%s)", type(e).__name__)
            return False

        time.sleep(DELAY_BETWEEN_MESSAGES)
        if resp.ok:
            return True

        try:
            body = resp.json()
        except ValueError:
            body = {"message": resp.text[:200]}

        if resp.status_code == 429:
            retry_after = float(body.get("retry_after", 5))
            logger.warning("Discord: limit wiadomości, czekam %.1fs", retry_after)
            time.sleep(retry_after)
            continue

        logger.error("Discord: HTTP %s – %s", resp.status_code, body.get("message", ""))
        return False
    return False
