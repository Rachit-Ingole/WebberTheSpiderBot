"""Small, dependency-free Telegram notifier with spam protection."""

import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TelegramNotifier:
    def __init__(self, cooldown_seconds=60):
        self._load_dotenv()
        self.bot_token = os.getenv("SPIDEY_TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("SPIDEY_TELEGRAM_CHAT_ID", "").strip()
        self.cooldown_seconds = cooldown_seconds
        self._last_intruder_alert = 0.0

        self.enabled = bool(self.bot_token and self.chat_id)
        if not self.enabled:
            print("[Telegram] Disabled: add credentials to .env")

    @staticmethod
    def _load_dotenv():
        """Load simple KEY=VALUE entries without requiring python-dotenv."""
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if not env_path.exists():
            return
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    def send_message(self, text):
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = urlencode({"chat_id": self.chat_id, "text": text}).encode()
        try:
            request = Request(url, data=payload, method="POST")
            with urlopen(request, timeout=8) as response:
                if 200 <= response.status < 300:
                    return True
            print("[Telegram] Message request failed.")
        except Exception as exc:
            print(f"[Telegram] Send failed: {exc}")
        return False

    def send_photo(self, photo_bytes, caption):
        if not self.enabled or not photo_bytes:
            return False

        boundary = f"----Spidey{uuid.uuid4().hex}"
        fields = {
            "chat_id": str(self.chat_id),
            "caption": caption,
        }
        body = bytearray()
        for name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(str(value).encode())
            body.extend(b"\r\n")
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(b'Content-Disposition: form-data; name="photo"; filename="intruder.jpg"\r\n')
        body.extend(b"Content-Type: image/jpeg\r\n\r\n")
        body.extend(bytes(photo_bytes))
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())

        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        try:
            request = Request(
                url,
                data=bytes(body),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with urlopen(request, timeout=12) as response:
                if 200 <= response.status < 300:
                    return True
            print("[Telegram] Photo request failed.")
        except Exception as exc:
            print(f"[Telegram] Photo send failed: {exc}")
        return False

    def send_intruder_alert(self, confidence=0.0, location="camera", image=None):
        now = time.monotonic()
        if now - self._last_intruder_alert < self.cooldown_seconds:
            return False

        message = "🚨 Spidey alert: unknown person detected"
        sent = self.send_photo(image, message) if image else self.send_message(message)
        if sent:
            self._last_intruder_alert = now
            print("[Telegram] Intruder alert sent.")
        return sent
