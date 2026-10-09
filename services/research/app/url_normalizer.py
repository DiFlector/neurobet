"""URL normalization for web research deduplication."""

from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode


class URLNormalizer:
    """Normalizes URLs to ensure accurate deduplication and cache hits."""

    # Tracking parameters to strip
    STRIP_PARAMS = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "ref",
        "fbclid",
        "gclid",
        "yclid",
        "_ga",
        "_gl",
        "origin",
    }

    @classmethod
    def normalize(cls, raw_url: str) -> str:
        """
        Normalizes a URL:
        - Lowercases scheme and netloc.
        - Removes anchor fragments (#...).
        - Strips standard marketing and tracking parameters.
        - Normalizes trailing slashes.
        """
        if not raw_url:
            return ""

        parsed = urlparse(raw_url.strip())
        scheme = parsed.scheme.lower() or "https"
        netloc = parsed.netloc.lower()

        # Remove default ports (80 for http, 443 for https)
        if netloc.endswith(":80") and scheme == "http":
            netloc = netloc[:-3]
        elif netloc.endswith(":443") and scheme == "https":
            netloc = netloc[:-4]

        # Path: strip duplicate slashes and trailing slash (unless path is just '/')
        path = parsed.path
        while "//" in path:
            path = path.replace("//", "/")
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]

        # Query: filter out tracking params and sort remaining
        filtered_query = []
        if parsed.query:
            for k, v in parse_qsl(parsed.query, keep_blank_values=False):
                if k.lower() not in cls.STRIP_PARAMS:
                    filtered_query.append((k, v))
        filtered_query.sort()
        new_query = urlencode(filtered_query)

        # Build clean URL without fragment
        clean_url = urlunparse((scheme, netloc, path, "", new_query, ""))
        return clean_url
