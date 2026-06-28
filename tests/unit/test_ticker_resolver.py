from __future__ import annotations

from quanterback.ticker_resolver import TickerCandidate, TickerResolver


def test_ticker_resolver_returns_known_ambiguous_dual_listing() -> None:
    result = TickerResolver(
        search_fn=lambda _q, _limit: [],
        web_search_fn=lambda _q, _limit: [],
    ).resolve("分析阿里")

    assert result.ambiguous is True
    assert [c.symbol for c in result.candidates] == ["BABA", "9988.HK"]


def test_ticker_resolver_uses_search_for_non_alias_company() -> None:
    resolver = TickerResolver(
        search_fn=lambda _q, _limit: [
            TickerCandidate("1810.HK", "Xiaomi Corporation", "Hong Kong"),
        ],
        web_search_fn=lambda _q, _limit: [],
    )

    result = resolver.resolve("分析 Xiaomi")

    assert result.ticker == "1810.HK"


def test_ticker_resolver_keeps_multi_exchange_candidates() -> None:
    resolver = TickerResolver(
        search_fn=lambda _q, _limit: [
            TickerCandidate("1810.HK", "Xiaomi Corporation", "Hong Kong"),
            TickerCandidate("XIACF", "Xiaomi Corporation", "OTC Markets"),
            TickerCandidate("XIACY", "Xiaomi Corporation ADR", "OTC Markets"),
        ],
        web_search_fn=lambda _q, _limit: [],
    )

    result = resolver.resolve("分析小米")

    assert result.ambiguous is True
    assert [c.symbol for c in result.candidates] == ["1810.HK", "XIACF", "XIACY"]


def test_ticker_resolver_filters_crypto_noise() -> None:
    resolver = TickerResolver(
        search_fn=lambda _q, _limit: [
            TickerCandidate("ZHIPU-USD", "Knowledge Atlas", "CCC", "CRYPTOCURRENCY"),
        ],
        web_search_fn=lambda _q, _limit: [],
    )

    result = resolver.resolve("分析智谱股票", proposed_ticker="ZHIPU")

    assert result.found is False
    assert result.query == "Zhipu"


def test_ticker_resolver_uses_web_search_fallback_for_recent_listing() -> None:
    resolver = TickerResolver(
        search_fn=lambda _q, _limit: [
            TickerCandidate("ZHIPU-USD", "Knowledge Atlas", "CCC", "CRYPTOCURRENCY"),
        ],
        web_search_fn=lambda _q, _limit: [
            TickerCandidate("2513.HK", "智谱", "Hong Kong"),
        ],
    )

    result = resolver.resolve("分析智谱股票", proposed_ticker="ZHIPU")

    assert result.ticker == "2513.HK"
