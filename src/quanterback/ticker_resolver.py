from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import yfinance as yf  # type: ignore[import-untyped]

from quanterback.tickers import canonical_ticker, extract_tickers

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TickerCandidate:
    symbol: str
    name: str
    exchange: str
    quote_type: str = "EQUITY"

    def label(self) -> str:
        parts = [self.symbol]
        if self.exchange:
            parts.append(self.exchange)
        if self.name:
            parts.append(self.name)
        return " - ".join(parts)


@dataclass(frozen=True)
class TickerResolution:
    ticker: str | None = None
    candidates: tuple[TickerCandidate, ...] = ()
    query: str = ""

    @property
    def found(self) -> bool:
        return self.ticker is not None or bool(self.candidates)

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


_QUERY_ALIASES = {
    "小米": "Xiaomi",
    "小米集团": "Xiaomi",
    "腾讯": "Tencent",
    "腾讯控股": "Tencent",
    "阿里": "Alibaba",
    "阿里巴巴": "Alibaba",
    "京东": "JD.com",
    "百度": "Baidu",
    "网易": "NetEase",
    "理想": "Li Auto",
    "小鹏": "XPeng",
    "比亚迪": "BYD Company",
    "智谱": "Zhipu",
    "智谱ai": "Zhipu",
    "智谱股票": "Zhipu",
}

_KNOWN_CANDIDATES = {
    "Xiaomi": (
        TickerCandidate("1810.HK", "Xiaomi Corporation", "Hong Kong"),
    ),
    "Tencent": (
        TickerCandidate("0700.HK", "Tencent Holdings Limited", "Hong Kong"),
        TickerCandidate("TCEHY", "Tencent Holdings Limited ADR", "OTC Markets"),
    ),
    "Alibaba": (
        TickerCandidate("BABA", "Alibaba Group Holding Limited", "NYSE"),
        TickerCandidate("9988.HK", "Alibaba Group Holding Limited", "Hong Kong"),
    ),
    "JD.com": (
        TickerCandidate("JD", "JD.com, Inc.", "NASDAQ"),
        TickerCandidate("9618.HK", "JD.com, Inc.", "Hong Kong"),
    ),
    "Baidu": (
        TickerCandidate("BIDU", "Baidu, Inc.", "NASDAQ"),
        TickerCandidate("9888.HK", "Baidu, Inc.", "Hong Kong"),
    ),
    "NetEase": (
        TickerCandidate("NTES", "NetEase, Inc.", "NASDAQ"),
        TickerCandidate("9999.HK", "NetEase, Inc.", "Hong Kong"),
    ),
    "Li Auto": (
        TickerCandidate("LI", "Li Auto Inc.", "NASDAQ"),
        TickerCandidate("2015.HK", "Li Auto Inc.", "Hong Kong"),
    ),
    "XPeng": (
        TickerCandidate("XPEV", "XPeng Inc.", "NYSE"),
        TickerCandidate("9868.HK", "XPeng Inc.", "Hong Kong"),
    ),
    "BYD Company": (
        TickerCandidate("1211.HK", "BYD Company Limited", "Hong Kong"),
        TickerCandidate("BYDDY", "BYD Company Limited ADR", "OTC Markets"),
    ),
}


SearchFn = Callable[[str, int], list[TickerCandidate]]


class TickerResolver:
    def __init__(self, search_fn: SearchFn | None = None) -> None:
        self._search_fn = search_fn or _yfinance_search

    def resolve(self, text: str, proposed_ticker: str | None = None) -> TickerResolution:
        query = _extract_query(text)
        known = _KNOWN_CANDIDATES.get(query) if query else None
        if known is not None:
            candidates = _preferred_candidates(known)
            if len(candidates) == 1:
                return TickerResolution(ticker=candidates[0].symbol, query=query)
            return TickerResolution(candidates=tuple(candidates), query=query)

        explicit = extract_tickers(text)
        if explicit:
            return TickerResolution(ticker=explicit[0], query=explicit[0])

        if not query:
            if proposed_ticker:
                return TickerResolution(ticker=canonical_ticker(proposed_ticker))
            return TickerResolution()

        try:
            candidates = _preferred_candidates(self._search_fn(query, 8))
        except Exception as exc:
            log.warning("Ticker search failed query=%r: %s", query, exc)
            candidates = []
        if len(candidates) == 1:
            return TickerResolution(ticker=candidates[0].symbol, query=query)
        if len(candidates) > 1:
            return TickerResolution(candidates=tuple(candidates), query=query)
        return TickerResolution(query=query)


def _extract_query(text: str) -> str:
    lowered = text.lower()
    for name, query in sorted(_QUERY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if name.lower() in lowered:
            return query
    cleaned = re.sub(
        r"(分析|研究|看看|看下|看一下|应该买|能买吗|股票|ticker|代码|查一下|查下)",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = " ".join(cleaned.split()).strip(" ?？,，。")
    return cleaned


def _preferred_candidates(
    candidates: tuple[TickerCandidate, ...] | list[TickerCandidate],
) -> list[TickerCandidate]:
    out: list[TickerCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.quote_type and candidate.quote_type.upper() not in {"EQUITY", "ETF"}:
            continue
        symbol = canonical_ticker(candidate.symbol)
        exchange = candidate.exchange
        if exchange.upper() in {"FRANKFURT", "SÃO PAULO", "SAO PAULO", "MEXICO", "SINGAPORE"}:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        out.append(TickerCandidate(symbol, candidate.name, exchange, candidate.quote_type))
    primary = [c for c in out if c.exchange.upper() not in {"OTC MARKETS", "PNK"}]
    return primary or out


def _yfinance_search(query: str, limit: int) -> list[TickerCandidate]:
    search = yf.Search(query, max_results=limit)
    quotes = getattr(search, "quotes", []) or []
    out: list[TickerCandidate] = []
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        symbol = str(quote.get("symbol") or "")
        if not symbol:
            continue
        out.append(_candidate_from_quote(quote))
    return out


def _candidate_from_quote(quote: dict[str, Any]) -> TickerCandidate:
    return TickerCandidate(
        symbol=canonical_ticker(str(quote.get("symbol") or "")),
        name=str(quote.get("longname") or quote.get("shortname") or ""),
        exchange=str(quote.get("exchDisp") or quote.get("exchange") or ""),
        quote_type=str(quote.get("quoteType") or quote.get("typeDisp") or ""),
    )
