from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote_plus

import pandas as pd
import requests
import yfinance as yf  # type: ignore[import-untyped]

from quanterback.domain.market import (
    AnalystAction,
    EpsTrend,
    InsiderActivity,
    MarketDataQualityError,
    NewsItem,
    PriceWindow,
    ShortInterestSnapshot,
)

log = logging.getLogger(__name__)


class YFinanceProvider:
    """DataProvider adapter over yfinance with on-disk Parquet cache."""

    def __init__(self, cache_dir: Path, cache_ttl_hours: int = 4) -> None:
        self._cache = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._ttl = timedelta(hours=cache_ttl_hours)

    def fetch(self, ticker: str) -> PriceWindow:
        ticker = ticker.upper()
        now = datetime.now(tz=timezone.utc)

        daily = self._read_cache(ticker, "daily", now)
        hourly = self._read_cache(ticker, "hourly", now)

        if daily is None or hourly is None:
            t = yf.Ticker(ticker)
            if daily is None:
                daily = self._normalize(t.history(period="1y", interval="1d"))
                if daily.empty:
                    raise MarketDataQualityError(
                        "last close unavailable; ticker has no usable daily price data"
                    )
                self._write_cache(ticker, "daily", daily, now)
            if hourly is None:
                hourly = self._normalize(t.history(period="30d", interval="1h"))
                self._write_cache(ticker, "hourly", hourly, now)

        return PriceWindow(ticker=ticker, daily=daily, hourly=hourly, as_of=now)

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        columns = ["open", "high", "low", "close", "volume"]
        if df.empty:
            return pd.DataFrame(columns=columns)
        out = df.rename(columns={c: c.lower() for c in df.columns})
        if not set(columns).issubset(out.columns):
            return pd.DataFrame(columns=columns)
        out = out[columns].copy()
        required = ["open", "high", "low", "close"]
        out = out.dropna(subset=required)
        out = out[out["close"] > 0]
        return out

    def _cache_path(self, ticker: str, kind: str, now: datetime) -> Path:
        day = now.strftime("%Y%m%d")
        return self._cache / f"{ticker}_{kind}_{day}.parquet"

    def _read_cache(self, ticker: str, kind: str, now: datetime) -> pd.DataFrame | None:
        path = self._cache_path(ticker, kind, now)
        if not path.exists():
            return None
        age = now - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if age > self._ttl:
            return None
        df = pd.read_parquet(path)
        if self._has_bad_price_rows(df):
            log.warning("Ignoring bad %s cache for %s: %s", kind, ticker, path)
            return None
        return df

    def _write_cache(self, ticker: str, kind: str, df: pd.DataFrame, now: datetime) -> None:
        if df.empty or self._has_bad_price_rows(df):
            log.warning("Skipping bad %s cache write for %s", kind, ticker)
            return
        df.to_parquet(self._cache_path(ticker, kind, now))

    @staticmethod
    def _has_bad_price_rows(df: pd.DataFrame) -> bool:
        required = {"open", "high", "low", "close"}
        if not required.issubset(df.columns):
            return True
        if df.empty:
            return True
        prices = df[list(required)]
        return bool(prices.isna().any().any() or (df["close"] <= 0).any())

    def fetch_historical(self, ticker: str, years: int) -> pd.DataFrame:
        ticker = ticker.upper()
        path = self._cache / f"{ticker}_hist_{years}y.parquet"
        now = datetime.now(tz=timezone.utc)
        if path.exists():
            age = now - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if age < timedelta(days=7):
                return pd.read_parquet(path)
        t = yf.Ticker(ticker)
        df = self._normalize(t.history(period=f"{years}y", interval="1d"))
        df.to_parquet(path)
        return df

    def fetch_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        """Returns recent headlines from multiple best-effort free sources.

        Cache: 1 hour. Yfinance news endpoint is unreliable — failures are
        swallowed and supplemented with RSS sources.
        """
        ticker = ticker.upper()
        now = datetime.now(tz=timezone.utc)
        cache_path = self._cache / f"{ticker}_news.parquet"

        # Check cache (1h TTL)
        if cache_path.exists():
            age = now - datetime.fromtimestamp(
                cache_path.stat().st_mtime, tz=timezone.utc,
            )
            if age < timedelta(hours=1):
                try:
                    cached_df = pd.read_parquet(cache_path)
                    return [
                        NewsItem(
                            title=row["title"], publisher=row["publisher"],
                            age_hours=float(row["age_hours"]),
                            link=row.get("link") or None,
                        )
                        for _, row in cached_df.iterrows()
                    ][:limit]
                except Exception as e:
                    log.warning("News cache read failed for %s: %s", ticker, e)

        # Fresh fetch — best-effort
        out: list[NewsItem] = []
        try:
            out.extend(self._fetch_yfinance_news(ticker, now))
        except Exception as e:
            log.warning("yfinance news fetch failed for %s: %s", ticker, e)
        out.extend(self._fetch_yahoo_rss_news(ticker, now))
        out.extend(self._fetch_google_news(ticker, now))

        out = self._dedupe_news(out)
        out.sort(key=lambda n: n.age_hours)
        out = out[:limit]

        # Write cache
        try:
            if out:
                df = pd.DataFrame([
                    {"title": n.title, "publisher": n.publisher,
                     "age_hours": n.age_hours, "link": n.link or ""}
                    for n in out
                ])
                df.to_parquet(cache_path)
        except Exception as e:
            log.debug("News cache write failed for %s: %s", ticker, e)

        return out

    def _fetch_yfinance_news(self, ticker: str, now: datetime) -> list[NewsItem]:
        items = yf.Ticker(ticker).news or []
        out: list[NewsItem] = []
        seven_days_ago = now - timedelta(days=7)
        for item in items:
            # yfinance has shifted news shape; try both nested and flat
            if "content" in item:
                content = item.get("content") or {}
                title = (content.get("title") or "").strip()
                publisher = (
                    (content.get("provider") or {}).get("displayName")
                    or item.get("publisher") or "unknown"
                )
                pub_date_str = content.get("pubDate") or content.get("displayTime")
                if pub_date_str:
                    try:
                        # ISO-8601 with Z
                        pub_dt = datetime.fromisoformat(
                            pub_date_str.replace("Z", "+00:00")
                        )
                    except Exception:
                        pub_dt = now
                else:
                    pub_dt = now
                link = (content.get("canonicalUrl") or {}).get("url")
            else:
                title = (item.get("title") or "").strip()
                publisher = item.get("publisher") or "unknown"
                ts = item.get("providerPublishTime")
                pub_dt = (datetime.fromtimestamp(ts, tz=timezone.utc)
                           if ts else now)
                link = item.get("link")

            if not title or pub_dt < seven_days_ago:
                continue
            age_h = (now - pub_dt).total_seconds() / 3600
            out.append(NewsItem(
                title=title, publisher=publisher,
                age_hours=max(age_h, 0.0), link=link,
            ))
        return out

    def _fetch_yahoo_rss_news(self, ticker: str, now: datetime) -> list[NewsItem]:
        url = (
            "https://feeds.finance.yahoo.com/rss/2.0/headline"
            f"?s={quote_plus(ticker)}&region=US&lang=en-US"
        )
        return self._fetch_rss_news(url, now, default_publisher="Yahoo Finance")

    def _fetch_google_news(self, ticker: str, now: datetime) -> list[NewsItem]:
        query = quote_plus(f'{ticker} stock OR {ticker} ETF')
        url = (
            "https://news.google.com/rss/search"
            f"?q={query}&hl=en-US&gl=US&ceid=US:en"
        )
        return self._fetch_rss_news(url, now, default_publisher="Google News")

    def _fetch_rss_news(
        self, url: str, now: datetime, *, default_publisher: str,
    ) -> list[NewsItem]:
        try:
            resp = requests.get(
                url,
                timeout=8,
                headers={"User-Agent": "quanterback/0.1 (+news-fetch)"},
            )
            resp.raise_for_status()
        except Exception as e:
            log.debug("RSS news fetch failed for %s: %s", url, e)
            return []

        out: list[NewsItem] = []
        seven_days_ago = now - timedelta(days=7)
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            log.debug("RSS news parse failed for %s: %s", url, e)
            return []

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip() or None
            source = item.findtext("source")
            publisher = (source or default_publisher).strip()
            pub_text = item.findtext("pubDate") or ""
            try:
                pub_dt = parsedate_to_datetime(pub_text)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                else:
                    pub_dt = pub_dt.astimezone(timezone.utc)
            except Exception:
                pub_dt = now
            if not title or pub_dt < seven_days_ago:
                continue
            age_h = (now - pub_dt).total_seconds() / 3600
            out.append(NewsItem(
                title=title, publisher=publisher,
                age_hours=max(age_h, 0.0), link=link,
            ))
        return out

    @staticmethod
    def _dedupe_news(items: list[NewsItem]) -> list[NewsItem]:
        deduped: list[NewsItem] = []
        seen: set[str] = set()
        for item in items:
            key = (item.link or item.title).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def fetch_next_earnings_date(self, ticker: str) -> date | None:
        """Returns next earnings call date if known.

        Cache: 24-hour TTL. Earnings dates are static between scans.
        """
        ticker = ticker.upper()
        now = datetime.now(tz=timezone.utc)
        cache_path = self._cache / f"{ticker}_earnings_date.parquet"

        # Check cache (24h TTL)
        if cache_path.exists():
            age = now - datetime.fromtimestamp(
                cache_path.stat().st_mtime, tz=timezone.utc,
            )
            if age < timedelta(hours=24):
                try:
                    cached_df = pd.read_parquet(cache_path)
                    if not cached_df.empty and "date" in cached_df.columns:
                        return self._coerce_date(cached_df["date"].iloc[0])
                    return None
                except Exception as e:
                    log.warning("Earnings date cache read failed for %s: %s", ticker, e)

        # Fresh fetch
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None:
                return None
            # yfinance returns either a DataFrame or a dict depending on ticker
            ed_value = None
            if isinstance(cal, dict):
                ed_value = cal.get("Earnings Date")
            elif hasattr(cal, "empty"):
                if cal.empty or "Earnings Date" not in cal.columns:
                    return None
                ed = cal["Earnings Date"]
                ed_value = ed.iloc[0] if hasattr(ed, "iloc") else ed
            result = self._coerce_date(ed_value)
            if result is None:
                return None

            # Write cache
            try:
                cache_df = pd.DataFrame([{"date": result}])
                cache_df.to_parquet(cache_path)
            except Exception as e:
                log.debug("Earnings date cache write failed for %s: %s", ticker, e)

            return result
        except Exception as e:
            log.warning("fetch_next_earnings_date(%s) failed: %s", ticker, e)
            return None

    @staticmethod
    def _coerce_date(value: Any) -> date | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            if not value:
                return None
            return YFinanceProvider._coerce_date(value[0])
        if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
            try:
                return YFinanceProvider._coerce_date(value.tolist())
            except Exception:
                pass
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            if pd.isna(value):
                return None
        except Exception:
            return None
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return cast(date, parsed.date())

    def fetch_insider_activity(
        self, ticker: str, lookback_days: int = 30,
    ) -> InsiderActivity | None:
        """Aggregates Form 4 activity in lookback window."""
        ticker = ticker.upper()
        try:
            t = yf.Ticker(ticker)
            df = t.insider_transactions
            if df is None or df.empty:
                return InsiderActivity(lookback_days=lookback_days)

            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)

            # Find date column
            date_col = None
            for col in df.columns:
                if "date" in col.lower():
                    date_col = col
                    break

            if date_col:
                df_dates = df[date_col]
                # Handle timezone-aware vs naive
                try:
                    if hasattr(df_dates.dtype, "tz") and df_dates.dtype.tz is not None:
                        # Timezone-aware
                        df = df[df_dates >= cutoff]
                    else:
                        # Timezone-naive
                        df = df[df_dates >= pd.Timestamp(cutoff).tz_localize(None)]
                except Exception:
                    # If comparison fails, just don't filter by date
                    pass

            # Parse transaction types
            transaction_col = None
            for col in df.columns:
                if "transaction" in col.lower():
                    transaction_col = col
                    break

            if transaction_col:
                buys = df[
                    df[transaction_col].str.contains(
                        "Purchase|Buy", case=False, na=False
                    )
                ]
                sells = df[
                    df[transaction_col].str.contains(
                        "Sale|Sell", case=False, na=False
                    )
                ]
            else:
                buys = pd.DataFrame()
                sells = pd.DataFrame()

            n_buys = len(buys)
            n_sells = len(sells)

            # Find value column
            value_col = None
            for col in df.columns:
                if "value" in col.lower():
                    value_col = col
                    break

            total_buy = buys[value_col].sum() if value_col and value_col in buys.columns else 0.0
            total_sell = sells[value_col].sum() if value_col and value_col in sells.columns else 0.0

            # Extract notable buyer
            notable = None
            if n_buys > 0 and value_col and value_col in buys.columns:
                insider_col = None
                for col in buys.columns:
                    if "insider" in col.lower():
                        insider_col = col
                        break
                if insider_col:
                    row = buys.nlargest(1, value_col).iloc[0]
                    notable = (
                        f"{row.get(insider_col, 'Insider')} bought "
                        f"${row.get(value_col, 0):,.0f}"
                    )

            return InsiderActivity(
                n_buys=int(n_buys),
                n_sells=int(n_sells),
                total_buy_usd=float(total_buy or 0.0),
                total_sell_usd=float(total_sell or 0.0),
                notable_buyer=notable,
                lookback_days=lookback_days,
            )
        except Exception as e:
            log.warning("fetch_insider_activity(%s) failed: %s", ticker, e)
            return None

    def fetch_analyst_actions(
        self, ticker: str, lookback_days: int = 14,
    ) -> list[AnalystAction]:
        """Returns analyst rating changes in lookback window."""
        ticker = ticker.upper()
        try:
            t = yf.Ticker(ticker)
            df = t.upgrades_downgrades
            if df is None or df.empty:
                return []

            cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)).date()
            out = []

            # Index is typically GradeDate
            for idx, row in df.iterrows():
                d = idx.date() if hasattr(idx, "date") else idx
                if d < cutoff:
                    continue
                out.append(
                    AnalystAction(
                        firm=str(row.get("Firm", "")),
                        action=str(row.get("Action", "")),
                        from_grade=str(row.get("FromGrade", "") or ""),
                        to_grade=str(row.get("ToGrade", "") or ""),
                        date=d,
                    )
                )
            return out
        except Exception as e:
            log.warning("fetch_analyst_actions(%s) failed: %s", ticker, e)
            return []

    def fetch_short_interest(self, ticker: str) -> ShortInterestSnapshot | None:
        """Returns current short interest metrics."""
        ticker = ticker.upper()
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            return ShortInterestSnapshot(
                short_pct_of_float=info.get("shortPercentOfFloat"),
                days_to_cover=info.get("daysToCover"),
                short_ratio=info.get("shortRatio"),
            )
        except Exception as e:
            log.warning("fetch_short_interest(%s) failed: %s", ticker, e)
            return None

    def fetch_eps_trend(self, ticker: str) -> EpsTrend | None:
        """Returns EPS estimate and trend."""
        ticker = ticker.upper()
        try:
            t = yf.Ticker(ticker)
            current = None
            growth_q_yoy = None

            # Try to get earnings estimate (may fail on older yfinance versions)
            try:
                df = t.earnings_estimate
                if df is not None and not df.empty and "0q" in df.index:
                    row = df.loc["0q"]
                    current = float(row.get("avg", 0)) if row.get("avg") is not None else None
            except Exception:
                pass

            # Get growth from info
            info = t.info or {}
            growth_q_yoy = info.get("earningsQuarterlyGrowth")

            return EpsTrend(
                current_estimate=current,
                days_7_change_pct=None,
                days_30_change_pct=None,
                growth_q_yoy=growth_q_yoy,
            )
        except Exception as e:
            log.warning("fetch_eps_trend(%s) failed: %s", ticker, e)
            return None
