from quanterback.failed_check_labels import humanize, humanize_list, humanize_reject


def test_humanize_known_check_en() -> None:
    assert humanize("sanity_max_drawdown", "en") == "drawdown > 50%"


def test_humanize_known_check_zh() -> None:
    assert humanize("sanity_max_drawdown", "zh") == "回撤超过 50%"


def test_humanize_unknown_check_passes_through() -> None:
    assert humanize("foobar_unknown", "en") == "foobar_unknown"


def test_humanize_with_suffix() -> None:
    assert humanize("sector_concurrency:ai_semi", "zh") == "同板块持仓已满 (ai_semi)"


def test_humanize_list_joins() -> None:
    out = humanize_list(
        ["sanity_max_drawdown", "sanity_min_sharpe"], "zh",
    )
    assert "回撤超过 50%" in out
    assert "夏普比率过低" in out
    assert "," in out


def test_humanize_list_empty() -> None:
    assert humanize_list([], "en") == ""


def test_humanize_unknown_language_falls_back_to_en() -> None:
    # No "fr" table → uses en
    assert humanize("sanity_max_drawdown", "fr") == "drawdown > 50%"


def test_humanize_reject_handles_risk_gate_list_en() -> None:
    result = humanize_reject(
        "risk_gate: ['strategy_worse_than_bh_and_negative', 'sanity_max_drawdown']",
        "en",
    )
    assert "risk gate:" in result
    assert "strategy lost money AND trailed buy-and-hold" in result
    assert "drawdown > 50%" in result
    assert "+" in result


def test_humanize_reject_handles_risk_gate_list_zh() -> None:
    result = humanize_reject(
        "risk_gate: ['strategy_worse_than_bh_and_negative', 'sanity_max_drawdown']",
        "zh",
    )
    assert "风控护栏:" in result
    assert "策略亏损且跑输大盘" in result
    assert "回撤超过 50%" in result
    assert "+" in result


def test_humanize_reject_handles_legacy_riskgate_prefix() -> None:
    result = humanize_reject("riskgate: 'sanity_max_drawdown'", "en")
    assert "risk gate:" in result
    assert "drawdown > 50%" in result
