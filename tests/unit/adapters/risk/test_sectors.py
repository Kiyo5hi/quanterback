from quanterback.adapters.risk.sectors import get_sector


def test_known_ticker() -> None:
    assert get_sector("NVDA") == "ai_semi"
    assert get_sector("AMD") == "ai_semi"


def test_case_insensitive() -> None:
    assert get_sector("nvda") == "ai_semi"
    assert get_sector("amd") == "ai_semi"


def test_unknown_ticker_other() -> None:
    assert get_sector("UNKNOWN_TICKER") == "other"
    assert get_sector("XYZ") == "other"
