"""Unit and integration test suite for Phase 15: Web Research Service."""

from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
import pytest

from contracts import (
    ResearchEvidence,
    ResearchPacket,
    ResearchRequest,
)
from app.config import config
from app.url_normalizer import URLNormalizer
from app.domain_filter import DomainFilter
from app.html_extractor import HTMLExtractor
from app.fetcher import SandboxedFetcher
from app.search_backend import SearchBackend
from app.cache import ResearchCache
from app.service import WebResearchService
from app.main import app


def test_url_normalization_and_tracking_removal():
    """Verify URLNormalizer strips tracking params, fragments, and handles casing."""
    dirty_urls = [
        ("https://www.TENNIS.com/news/article/?utm_source=twitter&utm_medium=social#comments", "https://www.tennis.com/news/article"),
        ("HTTP://ATPTour.com:80/en/news/article/?ref=homepage&fbclid=12345", "http://atptour.com/en/news/article"),
        ("https://tennismajors.com/news/article//?category=atp&utm_campaign=winter", "https://tennismajors.com/news/article?category=atp"),
    ]
    for raw, expected in dirty_urls:
        normalized = URLNormalizer.normalize(raw)
        assert normalized == expected, f"Expected '{expected}', got '{normalized}'"

    print("test_url_normalization_and_tracking_removal: OK")


def test_domain_allowlist_and_denylist():
    """Verify DomainFilter enforces allowlist and denylist security policies."""
    # 1. Allowed domains
    assert DomainFilter.is_allowed("https://www.tennis.com/article")[0] is True
    assert DomainFilter.is_allowed("https://news.atptour.com/match")[0] is True
    assert DomainFilter.is_allowed("https://sports.ru/tennis")[0] is True

    # 2. Denied betting / prediction selling domains
    allowed, reason = DomainFilter.is_allowed("https://fon.bet/results")
    assert allowed is False
    assert "DOMAIN_DENIED" in reason

    allowed, reason = DomainFilter.is_allowed("https://1xbet.com/live")
    assert allowed is False
    assert "DOMAIN_DENIED" in reason

    # 3. Non-allowlisted arbitrary domain
    allowed, reason = DomainFilter.is_allowed("https://shady-blog-predicts.xyz/page")
    assert allowed is False
    assert "DOMAIN_NOT_IN_ALLOWLIST" in reason

    print("test_domain_allowlist_and_denylist: OK")


def test_html_text_and_title_extraction():
    """Verify HTMLExtractor strips scripts, styles, navigation, and extracts title."""
    sample_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Medvedev Battles Past Alcaraz in Thriller</title>
        <script>console.log("tracking");</script>
        <style>body { color: red; }</style>
        <meta property="article:published_time" content="2026-10-09T08:00:00Z">
    </head>
    <body>
        <nav><a href="/">Home</a><a href="/news">News</a></nav>
        <header><h1>ATP Shanghai Masters</h1></header>
        <article>
            <p>Daniil Medvedev produced a masterclass on hard court today.</p>
            <p>The Russian star won in three sets: 6-4, 3-6, 6-4.</p>
        </article>
        <footer>Copyright 2026 Tennis News</footer>
    </body>
    </html>
    """
    doc = HTMLExtractor.extract_document(sample_html)
    assert doc["title"] == "Medvedev Battles Past Alcaraz in Thriller"
    assert "masterclass on hard court" in doc["text"]
    assert "console.log" not in doc["text"]
    assert "Copyright 2026" not in doc["text"]
    assert "Home" not in doc["text"]
    assert doc["content_hash"].startswith("sha256:")
    assert doc["published_at"] is not None

    print("test_html_text_and_title_extraction: OK")


def test_content_hash_and_deduplication():
    """Verify content hash is deterministic and detects duplicate documents."""
    text1 = "<html><body><p>Daniil Medvedev has a shoulder issue.</p></body></html>"
    text2 = "<html><body><div>Daniil Medvedev has a shoulder issue.</div></body></html>"
    text3 = "<html><body><p>Carlos Alcaraz is fully fit and ready.</p></body></html>"

    doc1 = HTMLExtractor.extract_document(text1)
    doc2 = HTMLExtractor.extract_document(text2)
    doc3 = HTMLExtractor.extract_document(text3)

    assert doc1["content_hash"] == doc2["content_hash"]
    assert doc1["content_hash"] != doc3["content_hash"]
    print("test_content_hash_and_deduplication: OK")


def test_published_time_and_freshness_scoring():
    """Verify publication timestamp parsing and bounded freshness scoring."""
    now = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)

    # 1. 2 hours old: fresh (1.0)
    t_2h = now - timedelta(hours=2)
    age, fresh = HTMLExtractor.calculate_freshness(t_2h, now)
    assert age == 7200
    assert fresh == 1.0

    # 2. 2 days old: 0.75
    t_2d = now - timedelta(days=2)
    age, fresh = HTMLExtractor.calculate_freshness(t_2d, now)
    assert fresh == 0.75

    # 3. 10 days old: 0.20
    t_10d = now - timedelta(days=10)
    age, fresh = HTMLExtractor.calculate_freshness(t_10d, now)
    assert fresh == 0.20

    # 4. 20 days old: stale (0.0)
    t_20d = now - timedelta(days=20)
    age, fresh = HTMLExtractor.calculate_freshness(t_20d, now)
    assert fresh == 0.0

    # 5. Missing date: default 0.5
    age, fresh = HTMLExtractor.calculate_freshness(None, now)
    assert fresh == 0.5

    print("test_published_time_and_freshness_scoring: OK")


def test_security_gates_get_only_and_captcha_abort():
    """Verify security controls: prohibition of POST/PUT and immediate CAPTCHA abort."""
    fetcher = SandboxedFetcher(timeout_seconds=2.0)

    # 1. Prohibit arbitrary POST/PUT requests
    with pytest.raises(ValueError, match="forbidden"):
        fetcher.fetch("https://www.tennis.com/post", method="POST")

    with pytest.raises(ValueError, match="forbidden"):
        fetcher.fetch("https://www.tennis.com/put", method="PUT")

    # 2. Mock CAPTCHA response
    captcha_html = "<html><body>Please solve the cloudflare cf-browser-verification challenge</body></html>"
    fetcher.register_mock_url("https://www.tennis.com/captcha-page", captcha_html)

    content, status = fetcher.fetch("https://www.tennis.com/captcha-page", method="GET")
    assert content is None
    assert "CAPTCHA" in status

    print("test_security_gates_get_only_and_captcha_abort: OK")


def test_live_score_authority_guard():
    """Verify architectural principle: research evidence is NEVER live score authority."""
    sample_html = "<html><head><title>Score</title></head><body>Live score: 6-1 4-1</body></html>"
    doc = HTMLExtractor.extract_document(sample_html)

    ev = ResearchEvidence(
        evidence_id="ev_test",
        url="https://www.tennis.com/live",
        normalized_url="https://www.tennis.com/live",
        domain="tennis.com",
        title=doc["title"],
        snippet=doc["snippet"],
        content_hash=doc["content_hash"],
        is_live_score_authority=False,  # Enforced
    )
    assert ev.is_live_score_authority is False
    print("test_live_score_authority_guard: OK")


def test_research_cache_ttl_and_idempotency():
    """Verify research cache returns cached packet without network operations."""
    cache = ResearchCache(ttl_seconds=3600)
    event_id = "evt_123456"
    query = "Medvedev vs Alcaraz tennis injury"

    key = cache.generate_cache_key(event_id, query)
    assert cache.get(key) is None

    packet = ResearchPacket(
        packet_id="pkt_999",
        event_id=event_id,
        sport_code="tennis",
        query=query,
        evidence=[],
        summary="Test packet",
        cached=False,
    )
    cache.set(key, packet)

    # Second request hits cache
    cached = cache.get(key)
    assert cached is not None
    assert cached.cached is True
    assert cached.packet_id == "pkt_999"

    print("test_research_cache_ttl_and_idempotency: OK")


def test_end_to_end_research_pipeline():
    """Verify WebResearchService executes search, extraction, and packet packaging."""
    fetcher = SandboxedFetcher()
    search = SearchBackend()
    cache = ResearchCache()

    # Setup mock URLs
    test_url = "https://www.tennis.com/news/medvedev-alcaraz-shanghai"
    test_html = """
    <html>
    <head>
        <title>Shanghai Masters Preview: Medvedev vs Alcaraz</title>
        <meta property="article:published_time" content="2026-10-09T09:00:00Z">
    </head>
    <body>
        <p>Daniil Medvedev has reported feeling comfortable on the Shanghai hard courts.</p>
        <p>No injury concerns for either player ahead of the semi-final.</p>
    </body>
    </html>
    """
    fetcher.register_mock_url(test_url, test_html)
    for q in search.generate_tennis_queries("Daniil Medvedev", "Carlos Alcaraz", "Shanghai Masters"):
        search.register_mock_query(q, [test_url])

    service = WebResearchService(fetcher=fetcher, search_backend=search, cache=cache)

    req = ResearchRequest(
        event_id="evt_shanghai_001",
        sport_code="tennis",
        participant_a="Daniil Medvedev",
        participant_b="Carlos Alcaraz",
        tournament="Shanghai Masters",
        max_pages=3,
    )

    packet = service.execute_research(req)
    assert packet.event_id == "evt_shanghai_001"
    assert len(packet.evidence) == 1
    assert packet.cached is False

    ev = packet.evidence[0]
    assert ev.domain == "tennis.com"
    assert "comfortable on the Shanghai hard courts" in ev.snippet
    assert ev.is_live_score_authority is False
    assert ev.freshness_score > 0.0

    # Repeat request with same params should return from cache
    packet2 = service.execute_research(req)
    assert packet2.cached is True
    assert packet2.packet_id == packet.packet_id

    print("test_end_to_end_research_pipeline: OK")


def test_backend_api_endpoints():
    """Verify FastAPI research service endpoints (/health, /api/research, /api/research/history)."""
    from app.main import research_service
    test_url = "https://www.tennis.com/news/medvedev-alcaraz-shanghai"
    test_html = "<html><head><title>Preview</title></head><body><p>Ready.</p></body></html>"
    research_service.fetcher.register_mock_url(test_url, test_html)
    for q in research_service.search_backend.generate_tennis_queries("Daniil Medvedev", "Carlos Alcaraz", "Shanghai"):
        research_service.search_backend.register_mock_query(q, [test_url])

    client = TestClient(app)

    # 1. Health
    h_resp = client.get("/health")
    assert h_resp.status_code == 200
    h_data = h_resp.json()
    assert h_data["status"] == "healthy"
    assert h_data["service"] == "research"
    assert h_data["enabled"] is True

    # 2. Execute research
    payload = {
        "event_id": "evt_api_test",
        "sport_code": "tennis",
        "participant_a": "Daniil Medvedev",
        "participant_b": "Carlos Alcaraz",
        "tournament": "Shanghai",
        "max_pages": 3,
        "force_refresh": False,
    }
    r_resp = client.post("/api/research", json=payload)
    assert r_resp.status_code == 200
    r_data = r_resp.json()
    assert r_data["event_id"] == "evt_api_test"
    assert "evidence" in r_data

    # 3. Cache info
    c_resp = client.get("/api/research/cache")
    assert c_resp.status_code == 200
    assert c_resp.json()["cache_size"] >= 1

    # 4. History
    hist_resp = client.get("/api/research/history")
    assert hist_resp.status_code == 200
    assert len(hist_resp.json()) >= 1

    print("test_backend_api_endpoints: OK")


if __name__ == "__main__":
    print("\n--- Running Phase 15 Web Research Tests ---")
    test_url_normalization_and_tracking_removal()
    test_domain_allowlist_and_denylist()
    test_html_text_and_title_extraction()
    test_content_hash_and_deduplication()
    test_published_time_and_freshness_scoring()
    test_security_gates_get_only_and_captcha_abort()
    test_live_score_authority_guard()
    test_research_cache_ttl_and_idempotency()
    test_end_to_end_research_pipeline()
    test_backend_api_endpoints()
    print("\nALL WEB RESEARCH TESTS PASSED SUCCESSFULLY!")
