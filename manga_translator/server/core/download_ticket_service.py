import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Dict, Optional


def resolve_path_within(root: str | Path, path: str | Path) -> Path:
    """Resolve *path* and reject anything outside *root*."""
    root_path = os.path.normcase(os.path.realpath(os.fspath(root)))
    file_path = os.path.normcase(os.path.realpath(os.fspath(path)))
    root_prefix = root_path if root_path.endswith(os.sep) else root_path + os.sep
    if not file_path.startswith(root_prefix):
        raise ValueError(f"Path is outside the allowed directory: {path}")
    return Path(file_path)


@dataclass
class DownloadTicket:
    token: str
    path: Path
    filename: str
    media_type: str
    expires_at: datetime
    delete_on_cleanup: bool = False


class DownloadTicketService:
    DEFAULT_TTL = timedelta(minutes=5)

    def __init__(self) -> None:
        self._tickets: Dict[str, DownloadTicket] = {}
        self._lock = Lock()

    def issue_ticket(
        self,
        path: str | Path,
        *,
        allowed_root: str | Path,
        filename: Optional[str] = None,
        media_type: str = "application/octet-stream",
        ttl: Optional[timedelta] = None,
        delete_on_cleanup: bool = False,
    ) -> DownloadTicket:
        file_path = resolve_path_within(allowed_root, path)
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"Download source does not exist: {file_path}")

        now = datetime.now(timezone.utc)
        ticket = DownloadTicket(
            token=secrets.token_urlsafe(32),
            path=file_path,
            filename=filename or file_path.name,
            media_type=media_type,
            expires_at=now + (ttl or self.DEFAULT_TTL),
            delete_on_cleanup=delete_on_cleanup,
        )

        with self._lock:
            self._cleanup_expired_locked(now)
            self._tickets[ticket.token] = ticket

        return ticket

    def get_ticket(self, token: str) -> Optional[DownloadTicket]:
        now = datetime.now(timezone.utc)
        with self._lock:
            self._cleanup_expired_locked(now)
            ticket = self._tickets.get(token)
            if ticket is None:
                return None
            if not ticket.path.exists() or not ticket.path.is_file():
                self._cleanup_ticket_locked(token, ticket)
                return None
            return ticket

    def revoke_ticket(self, token: str) -> None:
        with self._lock:
            ticket = self._tickets.pop(token, None)
            if ticket is not None:
                self._delete_ticket_file(ticket)

    def _cleanup_expired_locked(self, now: datetime) -> None:
        expired_tokens = [
            token
            for token, ticket in self._tickets.items()
            if ticket.expires_at <= now
        ]
        for token in expired_tokens:
            ticket = self._tickets.pop(token, None)
            if ticket is not None:
                self._delete_ticket_file(ticket)

    def _cleanup_ticket_locked(self, token: str, ticket: DownloadTicket) -> None:
        self._tickets.pop(token, None)
        self._delete_ticket_file(ticket)

    def _delete_ticket_file(self, ticket: DownloadTicket) -> None:
        if not ticket.delete_on_cleanup:
            return
        try:
            if ticket.path.exists() and ticket.path.is_file():
                os.remove(ticket.path)
        except OSError:
            pass
