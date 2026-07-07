"""Minimal SMTP mailer for the web cabinet (verification / receipts).

Best-effort: blocking smtplib runs in a thread; a misconfigured or unreachable SMTP
never raises into the request path. Off unless SMTP host + from-address are set.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.mime.text import MIMEText

from src.core.logging import get_logger

log = get_logger(__name__)


class Mailer:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        from_addr: str,
        *,
        use_tls: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._from = from_addr
        self._tls = use_tls

    @property
    def configured(self) -> bool:
        return bool(self._host and self._from)

    def _send_sync(self, to: str, subject: str, body: str) -> None:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = to
        with smtplib.SMTP(self._host, self._port, timeout=15) as smtp:
            if self._tls:
                smtp.starttls()
            if self._user:
                smtp.login(self._user, self._password)
            smtp.send_message(msg)

    async def send(self, to: str, subject: str, body: str) -> bool:
        if not self.configured:
            return False
        try:
            await asyncio.to_thread(self._send_sync, to, subject, body)
        except Exception as exc:
            log.warning("email send failed", to=to, error=str(exc))
            return False
        return True
