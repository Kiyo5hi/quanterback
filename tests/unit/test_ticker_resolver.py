from __future__ import annotations

from quanterback.ticker_resolver import TickerCandidate, TickerResolver


def test_ticker_resolver_returns_known_ambiguous_dual_listing() -> None:
    result = TickerResolver(search_fn=lambda _q, _limit: []).resolve("分析阿里")

    assert result.ambiguous is True
    assert [c.symbol for c in result.candidates] == ["BABA", "9988.HK"]


def test_ticker_resolver_uses_search_for_non_alias_company() -> None:
    resolver = TickerResolver(
        search_fn=lambda _q, _limit: [
            TickerCandidate("1810.HK", "Xiaomi Corporation", "Hong Kong"),
        ]
    )

    result = resolver.resolve("分析 Xiaomi")

    assert result.ticker == "1810.HK"


def test_ticker_resolver_filters_crypto_noise() -> None:
    resolver = TickerResolver(
        search_fn=lambda _q, _limit: [
            TickerCandidate("ZHIPU-USD", "Knowledge Atlas", "CCC", "CRYPTOCURRENCY"),
        ]
    )

    result = resolver.resolve("分析智谱股票", proposed_ticker="ZHIPU")

    assert result.found is False
    assert result.query == "Zhipu"
