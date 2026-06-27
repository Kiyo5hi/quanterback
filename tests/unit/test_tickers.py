from __future__ import annotations

from quanterback.tickers import canonical_ticker, extract_tickers


def test_canonical_ticker_supports_hk_yahoo_format() -> None:
    assert canonical_ticker("700.hk") == "0700.HK"
    assert canonical_ticker("1810") == "1810.HK"
    assert canonical_ticker("$nvda") == "NVDA"


def test_extract_tickers_supports_chinese_names_and_multiple_symbols() -> None:
    assert extract_tickers("分别分析 tsla 和 spcx") == ["TSLA", "SPCX"]
    assert extract_tickers("看看小米和腾讯") == ["1810.HK", "0700.HK"]
