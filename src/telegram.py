"""Минимальный клиент Telegram Bot API на стандартной библиотеке (urllib)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


class Telegram:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"

    def _post(self, method: str, data: bytes, content_type: str) -> dict:
        req = urllib.request.Request(f"{self.base}/{method}", data=data,
                                     headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> dict:
        params = {"timeout": timeout, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        payload = urllib.parse.urlencode(params).encode()
        # long-poll: HTTP-таймаут должен быть больше long-poll timeout
        req = urllib.request.Request(f"{self.base}/getUpdates", data=payload,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=timeout + 15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    def send_message(self, chat_id, text: str, parse_mode: str = "HTML") -> dict:
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        }).encode()
        return self._post("sendMessage", payload, "application/x-www-form-urlencoded")

    def send_document(self, chat_id, filename: str, content: bytes, caption: str = "",
                      parse_mode: str = "HTML",
                      mime: str = "application/octet-stream") -> dict:
        boundary = "----f5gospodina" + os.urandom(8).hex()
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
            fields["parse_mode"] = parse_mode
        body = bytearray()
        for k, v in fields.items():
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
            body += v.encode() + b"\r\n"
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="document"; '
                 f'filename="{filename}"\r\n').encode()
        body += f"Content-Type: {mime}\r\n\r\n".encode()
        body += content + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        return self._post("sendDocument", bytes(body),
                          f"multipart/form-data; boundary={boundary}")
