"""Domain allowlist, denylist, and SSRF security filter."""

import ipaddress
from urllib.parse import urlparse
from typing import Set, Tuple
from .config import config


class DomainFilter:
    """Enforces strict domain allowlist, denylist, and SSRF defense policies."""

    BLOCKED_INTERNAL_HOSTS = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "instance-data",
        "backend",
        "postgres",
        "redis",
        "minio",
        "bet-manager",
        "neural",
        "llm",
        "worker",
        "prometheus",
        "grafana",
    }

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
        Validates URL against allowlist, denylist, scheme, and SSRF restrictions.
        Returns (is_allowed, reason).
        """
        active_allowlist = allowlist if allowlist is not None else config.allowlist
        active_denylist = denylist if denylist is not None else config.denylist

        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            return False, f"SCHEME_NOT_ALLOWED: {scheme}"

        host = cls.extract_domain(url)
        if not host:
            return False, "INVALID_HOST"

        # Check internal / docker service names
        if host in cls.BLOCKED_INTERNAL_HOSTS or host.endswith(".local") or host.endswith(".internal"):
            return False, f"SSRF_BLOCKED: Internal hostname '{host}' is forbidden"

        # Check for IP address (SSRF mitigation)
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False, f"SSRF_BLOCKED: Private/reserved IP '{host}' is forbidden"
            return False, f"RAW_IP_FORBIDDEN: Direct IP access '{host}' is forbidden"
        except ValueError:
            # host is a domain name, proceed
            pass

        # Check denylist first
        for blocked in active_denylist:
            if host == blocked or host.endswith(f".{blocked}"):
                return False, f"DOMAIN_DENIED: {host} is in denylist"

        # Check allowlist
        for allowed in active_allowlist:
            if host == allowed or host.endswith(f".{allowed}"):
                return True, "ALLOWED"

        return False, f"DOMAIN_NOT_IN_ALLOWLIST: {host}"
