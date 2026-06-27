from __future__ import annotations

from quanterback.chat.router import ResearchChatRouter


def test_router_maps_slash_commands_to_tools() -> None:
    r = ResearchChatRouter()

    assert r.route("/analyze nvda").tool_name == "research.analyze_ticker"
    assert r.route("/add nvda").tool_name == "research.watchlist_add"
    assert r.route("/remove nvda").tool_name == "research.watchlist_remove"
    assert r.route("/watchlist").tool_name == "research.watchlist_list"
    assert r.route("/digest daily 08:00").tool_name == "research.schedule_digest"
    assert r.route("/jobs").tool_name == "research.list_jobs"
    assert r.route("/cancel 3").params["job_id"] == "3"


def test_router_maps_natural_language_to_tools() -> None:
    r = ResearchChatRouter()

    assert r.route("分析一下 NVDA").kind == "unknown"
    assert r.route_natural_fallback("分析一下nvda").tool_name == "research.analyze_ticker"
    assert r.route_natural_fallback("帮我把 SOXX 加到 watchlist").tool_name == "research.watchlist_add"
    assert r.route_natural_fallback("从列表删除SPCX").tool_name == "research.watchlist_remove"
    assert r.route_natural_fallback("看看我的关注列表").tool_name == "research.watchlist_list"


def test_router_confirm_cancel_help() -> None:
    r = ResearchChatRouter()

    assert r.route("确认").kind == "confirm"
    assert r.route("取消").kind == "cancel"
    assert r.route("/help").kind == "help"


def test_router_maps_trading_commands_when_enabled() -> None:
    r = ResearchChatRouter(enable_trading_commands=True)

    assert r.route("/status").tool_name == "trading.status"
    assert r.route("/freeze").tool_name == "trading.freeze"
    assert r.route("/scan nvda").tool_name == "trading.scan_tickers"
    assert r.route("/preview nvda").tool_name == "trading.preview_tickers"
    assert r.route("/rescan").tool_name == "trading.rescan_watchlist"
    assert r.route("/watchlist add nvda").tool_name == "trading.watchlist_add"
    assert r.route_natural_fallback("preview一下spcx").tool_name == "trading.preview_tickers"
