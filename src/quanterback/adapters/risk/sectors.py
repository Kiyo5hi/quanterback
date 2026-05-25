"""Static ticker → sector mapping. Coarse-grained (10 buckets).

Used by SectorConcurrencyRiskGate to prevent over-concentration.
Edit/extend as new tickers enter the universe."""
from __future__ import annotations

TICKER_TO_SECTOR: dict[str, str] = {
    # AI / semiconductors
    "NVDA": "ai_semi", "AMD": "ai_semi", "ARM": "ai_semi",
    "MU": "ai_semi", "SMCI": "ai_semi", "AVGO": "ai_semi",
    "TSM": "ai_semi", "INTC": "ai_semi", "SOUN": "ai_semi",
    # Mega-cap tech
    "AAPL": "mega_tech", "MSFT": "mega_tech", "GOOGL": "mega_tech",
    "META": "mega_tech", "AMZN": "mega_tech",
    # EV / auto
    "TSLA": "ev_auto", "RIVN": "ev_auto", "LCID": "ev_auto",
    # Software / cloud
    "CRM": "software", "ADBE": "software", "ORCL": "software",
    "PLTR": "software", "SNOW": "software", "NET": "software",
    "DDOG": "software", "CRWD": "software", "ZS": "software",
    "TWLO": "software", "ZM": "software", "TEAM": "software",
    "WDAY": "software", "VEEV": "software", "HUBS": "software",
    "DOCN": "software", "ESTC": "software", "PATH": "software",
    "AI": "software", "BBAI": "software", "BIGC": "software",
    "APPN": "software", "WIX": "software", "SQSP": "software",
    "GLBE": "software", "NOW": "software", "INTU": "software",
    "SHOP": "software", "MDB": "software", "CFLT": "software",
    "GTLB": "software", "PANW": "software", "FTNT": "software",
    "OKTA": "software",
    # Crypto-adjacent
    "COIN": "crypto", "MARA": "crypto", "RIOT": "crypto",
    "MSTR": "crypto",
    # Finance / banks / fintech
    "JPM": "financials", "BAC": "financials", "GS": "financials",
    "MS": "financials", "WFC": "financials", "C": "financials",
    "V": "financials", "MA": "financials", "AXP": "financials",
    "KKR": "financials", "APO": "financials", "BX": "financials",
    "SOFI": "financials", "UPST": "financials", "AFRM": "financials",
    "ALLY": "financials", "SCHW": "financials", "SQ": "financials",
    "HOOD": "financials", "PYPL": "financials", "ICE": "financials",
    "CME": "financials", "SPGI": "financials", "MCO": "financials",
    "MSCI": "financials", "NDAQ": "financials",
    # Healthcare
    "UNH": "healthcare", "JNJ": "healthcare", "LLY": "healthcare",
    "NVO": "healthcare", "PFE": "healthcare", "MRK": "healthcare",
    "ABBV": "healthcare", "TMO": "healthcare", "DHR": "healthcare",
    "ISRG": "healthcare", "GILD": "healthcare", "AMGN": "healthcare",
    "BIIB": "healthcare", "REGN": "healthcare", "VRTX": "healthcare",
    "MRNA": "healthcare", "BNTX": "healthcare", "CRSP": "healthcare",
    "EDIT": "healthcare", "NTLA": "healthcare", "BEAM": "healthcare",
    "SANA": "healthcare", "ARWR": "healthcare", "IONS": "healthcare",
    "NVAX": "healthcare",
    # Consumer / retail
    "NKE": "consumer", "SBUX": "consumer", "MCD": "consumer",
    "COST": "consumer", "WMT": "consumer", "TGT": "consumer",
    "HD": "consumer", "LOW": "consumer", "ETSY": "consumer",
    "PINS": "consumer", "CHWY": "consumer", "PTON": "consumer",
    "ROKU": "consumer", "TTD": "consumer", "TRIP": "consumer",
    "EXPE": "consumer", "BKNG": "consumer", "MAR": "consumer",
    "HLT": "consumer", "LVS": "consumer", "WYNN": "consumer",
    "MGM": "consumer", "DKNG": "consumer", "PENN": "consumer",
    "FUBO": "consumer", "PARA": "consumer", "WBD": "consumer",
    "NFLX": "consumer", "SPOT": "consumer", "LYV": "consumer",
    "EDR": "consumer", "LULU": "consumer", "DECK": "consumer",
    "ANF": "consumer", "GAP": "consumer", "URBN": "consumer",
    "RH": "consumer", "WSM": "consumer", "TPR": "consumer",
    "CPRI": "consumer", "EL": "consumer", "ULTA": "consumer",
    "CMG": "consumer", "TXRH": "consumer", "DPZ": "consumer",
    "WING": "consumer", "SHAK": "consumer", "DASH": "consumer",
    "UBER": "consumer", "LYFT": "consumer", "ABNB": "consumer",
    "RBLX": "consumer", "DIS": "consumer",
    # Energy / utilities
    "XOM": "energy", "CVX": "energy", "COP": "energy",
    "SLB": "energy", "HAL": "energy", "EOG": "energy",
    "FANG": "energy", "PSX": "energy", "MPC": "energy",
    "VLO": "energy", "NRG": "utilities", "VST": "utilities",
    "TLN": "utilities", "CEG": "utilities", "NEE": "utilities",
    "DUK": "utilities", "GE": "industrial", "ENPH": "utilities",
    "FSLR": "utilities",
    # Defense / industrial / aerospace
    "LMT": "defense", "RTX": "defense", "BA": "defense",
    "GD": "defense", "NOC": "defense", "HII": "defense",
    "LDOS": "defense", "TXT": "defense", "AXON": "defense",
    "PWR": "industrial", "ETN": "industrial", "EMR": "industrial",
    "PH": "industrial", "ITW": "industrial", "CAT": "industrial",
    "DE": "industrial", "F": "industrial", "GM": "industrial",
    "HEI": "industrial",
    # Telecom
    "T": "telecom", "VZ": "telecom", "TMUS": "telecom",
    "CMCSA": "telecom", "CHTR": "telecom", "SIRI": "telecom",
    # Real Estate / REIT
    "PLD": "reit", "EQIX": "reit", "DLR": "reit",
    "AMT": "reit", "SBAC": "reit",
    # Meme / momentum stocks
    "GME": "consumer", "AMC": "consumer", "BBBY": "consumer",
    "BB": "software", "NOK": "telecom",
    # ETFs (treat as broad index)
    "SPY": "etf", "QQQ": "etf", "IWM": "etf", "SMH": "etf",
    "ARKK": "etf", "XLK": "etf", "XLF": "etf", "XLE": "etf",
    "XBI": "etf", "TLT": "etf", "GLD": "etf", "SLV": "etf",
    "USO": "etf", "UNG": "etf",
    # Miscellaneous
    "W": "consumer", "LMND": "software", "QCOM": "ai_semi",
    "TXN": "ai_semi", "MRVL": "ai_semi", "ASML": "ai_semi",
    "LRCX": "ai_semi", "AMAT": "ai_semi", "KLAC": "ai_semi",
    "ON": "ai_semi", "TRV": "financials",
}


def get_sector(ticker: str) -> str:
    """Return sector for ticker; 'other' if unknown."""
    return TICKER_TO_SECTOR.get(ticker.upper(), "other")
