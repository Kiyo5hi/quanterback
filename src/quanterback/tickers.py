from __future__ import annotations

import re

_SYMBOL_RE = re.compile(
    r"(?<![A-Za-z0-9])\$?([A-Za-z][A-Za-z0-9.\-]{0,9}|\d{1,5}(?:\.HK)?)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

_TICKER_ALIASES = {
    "NVIDIA": "NVDA",
    "NVIDIA CORP": "NVDA",
    "NVIDIA CORPORATION": "NVDA",
    "NVDA.O": "NVDA",
    "SOX": "SOXX",
    "PHLX SOX": "SOXX",
    "PHILADELPHIA SEMICONDUCTOR INDEX": "SOXX",
}

_NAME_ALIASES = {
    "英伟达": "NVDA",
    "特斯拉": "TSLA",
    "小米": "1810.HK",
    "小米集团": "1810.HK",
    "腾讯": "0700.HK",
    "腾讯控股": "0700.HK",
    "阿里巴巴": "BABA",
    "阿里": "BABA",
    "阿里港股": "9988.HK",
    "美团": "3690.HK",
    "京东": "JD",
    "京东港股": "9618.HK",
    "百度": "BIDU",
    "百度港股": "9888.HK",
    "网易": "NTES",
    "网易港股": "9999.HK",
    "比亚迪": "1211.HK",
    "理想": "LI",
    "理想港股": "2015.HK",
    "小鹏": "XPEV",
    "小鹏港股": "9868.HK",
}

_STOPWORDS = {
    "A",
    "ADD",
    "ANALYSE",
    "ANALYZE",
    "AND",
    "ASK",
    "CANCEL",
    "DELETE",
    "DIGEST",
    "ETF",
    "FROM",
    "HELP",
    "I",
    "IN",
    "JOB",
    "JOBS",
    "LIST",
    "ME",
    "MY",
    "NO",
    "OR",
    "PLEASE",
    "REMOVE",
    "REPORT",
    "SHOW",
    "THE",
    "TICKER",
    "TO",
    "UNWATCH",
    "WATCH",
    "WATCHLIST",
    "YES",
}


def canonical_ticker(raw: str) -> str:
    ticker = raw.strip().upper().replace("$", "")
    ticker = _TICKER_ALIASES.get(ticker, ticker)
    if ticker.endswith(".HK"):
        code = ticker[:-3]
        if code.isdigit():
            return f"{int(code):04d}.HK"
    if ticker.isdigit():
        return f"{int(ticker):04d}.HK"
    return ticker


def extract_tickers(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    lowered = text.lower()
    for name, ticker in sorted(_NAME_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if name.lower() in lowered:
            _append_unique(out, seen, canonical_ticker(ticker))
    for match in _SYMBOL_RE.finditer(text):
        token = match.group(1).upper()
        if token in _STOPWORDS:
            continue
        _append_unique(out, seen, canonical_ticker(token))
    return out


def _append_unique(out: list[str], seen: set[str], ticker: str) -> None:
    if ticker and ticker not in seen:
        seen.add(ticker)
        out.append(ticker)
