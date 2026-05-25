from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class FakeHistoricalDataProvider:
    df_per_ticker: dict[str, pd.DataFrame]

    def fetch_historical(self, ticker: str, years: int) -> pd.DataFrame:
        return self.df_per_ticker[ticker.upper()].copy()
