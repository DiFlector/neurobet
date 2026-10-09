"""Domain allowlist and denylist security filter."""

from urllib.parse import urlparse
from typing import Set, Tuple
from .config import config


class DomainFilter:
    """Enforces strict domain allowlist and denylist policies."""

    @staticmethod
    def extract_domain(url: str) -> str:
        """Extracts clean base domain or host from URL."""
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if ":" in host:
            host = host.split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        return host

    @classmethod
    def is_allowed(
        cls,
        url: str,
        allowlist: Set[str] = None,
        denylist: Set[str] = None,
    ) -> Tuple[bool, str]:
        """
        Validates URL against allowlist and denylist.
        Returns (is_allowed, reason).
        """
        active_allowlist = allowlist if allowlist is not None else config.allowlist
        active_denylist = denylist if denylist is not None else config.denylist

        host = cls.extract_domain(url)
        if not host:
            return False, "INVALID_HOST"

        # Check denylist first
        for blocked in active_denylist:
            if host == blocked or host.endswith(f".{blocked}"):
                return False, f"DOMAIN_DENIED: {host} is in denylist"

        # Check allowlist
        for allowed in active_allowlist:
            if host == allowed or host.endswith(f".{allowed}"):
                return True, "ALLOWED"

        return False, f"DOMAIN_NOT_IN_ALLOWLIST: {host}"
