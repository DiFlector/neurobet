"""Sandboxed HTTP Fetcher with strict GET-only enforcement and anti-bot boundaries."""

import logging
import re
from typing import Dict, Optional, Tuple
try:
    import httpx
except ImportError:
    httpx = None
from .config import config
from .domain_filter import DomainFilter

logger = logging.getLogger("research.fetcher")


class SandboxedFetcher:
    """
    Safely retrieves web pages for qualitative context research.
    Strictly forbids HTTP mutations (POST/PUT), CAPTCHA bypass, or non-allowlisted domains.
    """

    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

    def __init__(self, timeout_seconds: float = None):
        self.timeout = timeout_seconds or config.timeout_seconds
        self._mock_responses: Dict[str, str] = {}

    def register_mock_url(self, url: str, html_content: str):
        """Allows test suites to register deterministic responses."""
        self._mock_responses[url] = html_content

    def fetch(self, url: str, method: str = "GET") -> Tuple[Optional[str], str]:
        """
        Executes a sandboxed fetch.
        Returns (html_content, status_or_reason).
        """
        # 1. Enforce GET-only rule
        if method.upper() != "GET":
            raise ValueError(f"Arbitrary HTTP mutation methods ({method}) are strictly forbidden in Web Research.")

        # 2. Enforce domain allowlist
        allowed, reason = DomainFilter.is_allowed(url)
        if not allowed:
            logger.info(f"Skipping URL '{url}': {reason}")
            return None, reason

        # 3. Retrieve body (from mock or network)
        if url in self._mock_responses:
            body = self._mock_responses[url]
        else:
            headers = {
                "User-Agent": self.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            }

            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
                    resp = client.get(url)

                    # Check anti-bot challenges / 403 / 429
                    if resp.status_code in (403, 429):
                        logger.warning(f"Anti-bot or rate limit hit on '{url}' (status {resp.status_code}). Aborting without bypass.")
                        return None, f"HTTP_{resp.status_code}_ANTI_BOT_BLOCKED"

                    if resp.status_code != 200:
                        return None, f"HTTP_{resp.status_code}"

                    # Content-type check
                    content_type = resp.headers.get("content-type", "").lower()
                    if content_type and not any(t in content_type for t in ("text/html", "text/plain", "application/xhtml+xml", "application/xml", "application/json")):
                        logger.warning(f"Rejected non-text content-type '{content_type}' on '{url}'.")
                        return None, f"UNSUPPORTED_CONTENT_TYPE: {content_type}"

                    # Payload size cap (5 MB)
                    if len(resp.content) > 5 * 1024 * 1024:
                        logger.warning(f"Payload on '{url}' exceeds 5MB limit.")
                        return None, "PAYLOAD_TOO_LARGE"

                    body = resp.text

            except httpx.TimeoutException:
                logger.warning(f"Timeout ({self.timeout}s) exceeded while fetching '{url}'.")
                return None, "TIMEOUT"
            except Exception as e:
                logger.warning(f"Error fetching '{url}': {e}")
                return None, f"FETCH_ERROR: {str(e)}"

        # 4. CAPTCHA / Cloudflare: только явный challenge, не сам факт наличия CF-скриптов
        # (у многих сайтов в HTML есть "challenge-platform", но страница уже отдана).
        body_lower = body.lower()
        hard_challenge = (
            "cf-browser-verification" in body_lower
            or "just a moment..." in body_lower
            or "checking your browser before accessing" in body_lower
            or "attention required! | cloudflare" in body_lower
        )
        soft_widgets = ("g-recaptcha" in body_lower) or ("hcaptcha" in body_lower)
        textish_len = len(re.sub(r"<[^>]+>", " ", body))
        if hard_challenge or (soft_widgets and textish_len < 800):
            logger.warning(f"CAPTCHA signature detected on '{url}'. Aborting without bypass.")
            return None, "CAPTCHA_CHALLENGE_ABORTED"

        return body, "OK"
