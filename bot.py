"""Thin Telegram Bot API client for Naija Scholar.

Pure HTTP wrapper over the Telegram Bot API using `requests`. No framework
dependencies. Used by main.py for long-polling (dev) and webhook (production)
modes.

Typical flow:
    bot = TelegramBot(token)
    me = bot.get_me()                      # validate token at startup
    bot.set_webhook(public_webhook_url)    # production: Telegram -> POST /webhook/telegram/<token>
    updates = bot.get_updates(offset)      # dev: long-poll fallback
    bot.send_message(chat_id, "hello", reply_markup={...})
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

API_BASE = "https://api.telegram.org"


class TelegramBotError(RuntimeError):
    """Raised when the Bot API returns ok:false or the request times out."""


class TelegramBot:
    def __init__(self, token: str, timeout: int = 15):
        self.token = token
        self.timeout = timeout
        self.base = f"{API_BASE}/bot{token}"

    def _call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        resp = requests.post(f"{self.base}/{method}", json=params or {}, timeout=self.timeout)
        try:
            data = resp.json()
        except ValueError as exc:
            raise TelegramBotError(f"{method}: non-JSON response {resp.status_code}") from exc
        if not data.get("ok"):
            raise TelegramBotError(f"{method} failed: {(data.get('description') or resp.text)[:300]}")
        return data.get("result")

    def get_me(self) -> Dict[str, Any]:
        return self._call("getMe")

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: Optional[str] = None,
        disable_web_page_preview: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        if parse_mode:
            params["parse_mode"] = parse_mode
        return self._call("sendMessage", params)

    def answer_callback_query(self, callback_query_id: str, text: Optional[str] = None, show_alert: bool = False) -> Any:
        params: Dict[str, Any] = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text is not None:
            params["text"] = text
        return self._call("answerCallbackQuery", params)

    def set_webhook(self, url: str, secret_token: Optional[str] = None, max_connections: int = 40) -> Any:
        params: Dict[str, Any] = {"url": url, "max_connections": max_connections}
        if secret_token:
            params["secret_token"] = secret_token
        return self._call("setWebhook", params)

    def delete_webhook(self) -> Any:
        return self._call("deleteWebhook")

    def get_webhook_info(self) -> Dict[str, Any]:
        return self._call("getWebhookInfo")

    def get_updates(self, offset: int = 0, timeout: int = 25, allowed_updates: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"offset": offset, "timeout": timeout}
        if allowed_updates:
            params["allowed_updates"] = allowed_updates
        return self._call("getUpdates", params)