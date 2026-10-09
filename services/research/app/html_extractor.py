"""HTML content extraction, metadata parsing, and hashing."""

from datetime import datetime, timezone
import hashlib
import re
from typing import Dict, Any, Optional, Tuple
from html import unescape


class HTMLExtractor:
    """Extracts clean text, publication dates, and calculates content hashes from HTML."""

    @staticmethod
    def strip_tags(html: str) -> str:
        """Removes script, style, navigation, headers, footers and all HTML tags."""
        # Remove scripts, styles, head, comments
        text = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
        text = re.sub(r"<(script|style|nav|header|footer|aside|noscript)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        # Replace block tags with newline
        text = re.sub(r"<(p|div|h[1-6]|li|tr|br)[^>]*>", "\n", text, flags=re.IGNORECASE)
        # Strip all remaining tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Unescape HTML entities
        text = unescape(text)
        # Collapse multiple spaces and newlines
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
        clean_text = "\n".join(l for l in lines if l)
        return clean_text

    @staticmethod
    def extract_title(html: str) -> str:
        """Extracts article title from <title> or <meta property="og:title">."""
        # Check og:title
        og_match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if og_match:
            return unescape(og_match.group(1)).strip()

        # Check <title>
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
        if title_match:
            clean = re.sub(r"<[^>]+>", "", title_match.group(1))
            return unescape(clean).strip()

        # Check <h1>
        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL | re.IGNORECASE)
        if h1_match:
            clean = re.sub(r"<[^>]+>", "", h1_match.group(1))
            return unescape(clean).strip()

        return "Untitled Document"

    @staticmethod
    def extract_published_time(html: str) -> Optional[datetime]:
        """Extracts publication timestamp from meta tags or time element."""
        # Meta article:published_time or pubdate
        meta_patterns = [
            r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\'](?:pubdate|publishdate|date)["\'][^>]+content=["\']([^"\']+)["\']',
            r'<time[^>]+datetime=["\']([^"\']+)["\']',
        ]
        for pat in meta_patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                raw_val = m.group(1).strip()
                try:
                    # ISO 8601 parsing (handles Z or offsets)
                    raw_val = raw_val.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(raw_val)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc)
                except Exception:
                    pass
        return None

    @classmethod
    def calculate_freshness(cls, published_at: Optional[datetime], retrieved_at: datetime) -> Tuple[Optional[int], float]:
        """
        Calculates age in seconds and bounded freshness score [0.0, 1.0].
        Freshness scale:
        - <= 6h: 1.0
        - <= 24h: 0.90
        - <= 3d: 0.75
        - <= 7d: 0.50
        - <= 14d: 0.20
        - > 14d: 0.0
        - Unknown: 0.50
        """
        if not published_at:
            return None, 0.50

        age_sec = max(0, int((retrieved_at - published_at).total_seconds()))

        if age_sec <= 21600:  # 6 hours
            score = 1.0
        elif age_sec <= 86400:  # 24 hours
            score = 0.90
        elif age_sec <= 259200:  # 3 days
            score = 0.75
        elif age_sec <= 604800:  # 7 days
            score = 0.50
        elif age_sec <= 1209600:  # 14 days
            score = 0.20
        else:
            score = 0.0

        return age_sec, score

    @classmethod
    def extract_document(
        cls,
        html: str,
        retrieved_at: Optional[datetime] = None,
        max_text_size: int = 30000,
    ) -> Dict[str, Any]:
        """Processes raw HTML into extracted text, metadata, content hash, and freshness."""
        now = retrieved_at or datetime.now(timezone.utc)
        title = cls.extract_title(html)
        published_at = cls.extract_published_time(html)
        age_sec, freshness = cls.calculate_freshness(published_at, now)

        text = cls.strip_tags(html)
        if len(text) > max_text_size:
            text = text[:max_text_size]

        # SHA-256 hash
        content_hash = f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"

        # Concise snippet: first 400 chars of meaningful text
        snippet = text[:400].replace("\n", " ").strip()
        if len(text) > 400:
            snippet += "..."

        return {
            "title": title,
            "text": text,
            "snippet": snippet,
            "published_at": published_at,
            "retrieved_at": now,
            "age_seconds": age_sec,
            "freshness_score": freshness,
            "content_hash": content_hash,
        }
