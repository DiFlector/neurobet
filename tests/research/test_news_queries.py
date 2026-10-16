"""Withdrawal news is the first search, not a sports.ru match card."""

from app.search_backend import SearchBackend


def test_tennis_queries_lead_with_injury_sites():
    queries = SearchBackend.generate_queries("tennis", "Фонсека Ж", "Синнер Я", "WTA. Ухань")
    assert queries[0].startswith("site:championat.com")
    assert "снялся" in queries[0] and "травма" in queries[0]
    assert queries[1].startswith("site:atptour.com")
    assert queries[2].startswith("site:wtatennis.com")
    assert "fonseka" in queries[1]
    assert not any(q.startswith("site:sports.ru") for q in queries[:3])
    assert SearchBackend.PRIORITY_DOMAINS[0] == "championat.com"


def test_other_sports_do_not_ask_atp():
    queries = SearchBackend.generate_queries("football", "Спартак", "Зенит")
    assert queries[0].startswith("site:championat.com")
    assert all("atptour.com" not in q and "wtatennis.com" not in q for q in queries)


if __name__ == "__main__":
    test_tennis_queries_lead_with_injury_sites()
    test_other_sports_do_not_ask_atp()
    print("NEWS QUERY TESTS OK")
