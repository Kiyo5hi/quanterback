"""Maps raw failed_check identifiers from RiskAssessment to human-readable
labels for display in reports and TG notifications.

Identifiers come from CompositeRiskGate.evaluate() and decorator gates
(PdtAwareRiskGate, TotalExposureRiskGate, SectorExposureRiskGate). Keep
this single source of truth — add new keys here when new gates introduce them.
"""
from __future__ import annotations

# language -> {identifier: label}
LABELS: dict[str, dict[str, str]] = {
    "en": {
        # Sanity caps
        "sanity_min_num_trades": "too few backtest trades",
        "sanity_max_drawdown": "drawdown > 50%",
        "sanity_min_oos_trades": "no out-of-sample evidence",
        "sanity_min_sharpe": "Sharpe ratio too low",
        # Relative gates
        "strategy_worse_than_bh_and_negative": "strategy lost money AND trailed buy-and-hold",
        "oos_loss_relative_and_absolute": "out-of-sample loss vs market",
        "strategy_dd_worse_than_bh": "strategy more risky than buy-and-hold",
        # PDT gate
        "pdt_protection": "PDT protection (account < $25k near day-trade limit)",
        # Exposure caps
        "total_exposure_exceeded": "total $ exposure cap reached",
        # Sector exposure cap — prefix match
        "sector_exposure": "sector $ exposure cap reached",
    },
    "zh": {
        "sanity_min_num_trades": "回测样本太少",
        "sanity_max_drawdown": "回撤超过 50%",
        "sanity_min_oos_trades": "样本外样本不足",
        "sanity_min_sharpe": "夏普比率过低",
        "strategy_worse_than_bh_and_negative": "策略亏损且跑输大盘",
        "oos_loss_relative_and_absolute": "样本外亏损跑输市场",
        "strategy_dd_worse_than_bh": "策略回撤比大盘还大",
        "pdt_protection": "PDT 保护触发(账户 < $25k 且接近 day-trade 上限)",
        "total_exposure_exceeded": "总 $ 暴露已达上限",
        "sector_exposure": "同板块 $ 暴露已达上限",
    },
}


def humanize(failed_check: str, language: str = "en") -> str:
    """Translate one failed_check identifier to a friendly label."""
    table = LABELS.get(language, LABELS["en"])
    # Some identifiers carry a suffix after ":" (e.g., sector_exposure:ai_semi)
    base, _, detail = failed_check.partition(":")
    label = table.get(base, failed_check)  # fall through to raw if unknown
    if detail:
        return f"{label} ({detail})"
    return label


def humanize_list(failed_checks: list[str], language: str = "en") -> str:
    """Join all failed_checks into a friendly comma-separated string."""
    if not failed_checks:
        return ""
    return ", ".join(humanize(fc, language) for fc in failed_checks)


# Top-level rejection reasons (from pipeline) — different from
# RiskAssessment failed_checks above. These come from:
#  - "ticker has open lifecycle" — already holding the ticker
#  - "riskgate: 'X'" — risk gate inner failure (X is humanizable above)
#  - "exception: ..." — pipeline error
REJECT_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "ticker has open lifecycle": "already holding this ticker (won't double-up)",
    },
    "zh": {
        "ticker has open lifecycle": "已持有该票（避免重复建仓）",
    },
}


def humanize_reject(reason: str, language: str = "en") -> str:
    """Translate a top-level pipeline rejection reason into a friendly phrase.

    Handles:
      - exact matches in REJECT_LABELS
      - 'risk_gate: [...]' prefix → list of failures parsed and humanized
      - 'riskgate: X' prefix → 'X' lookup in LABELS (risk gate humanizer, legacy)
      - 'exception: X' prefix → '执行错误: X' / 'execution error: X'
      - falls through to raw if unknown
    """
    import ast

    table = REJECT_LABELS.get(language, REJECT_LABELS["en"])
    if reason in table:
        return table[reason]
    # risk_gate: ['failure_id1', 'failure_id2'] — Python list representation
    if reason.startswith("risk_gate:"):
        inner = reason.split(":", 1)[1].strip()
        if inner.startswith("[") and inner.endswith("]"):
            try:
                items = ast.literal_eval(inner)
                if isinstance(items, list):
                    # Defensive: skip None items, stringify non-strings cleanly
                    items = [str(i).strip() for i in items if i is not None and str(i).strip()]
                    if not items:
                        return "风控护栏(细节未知)" if language == "zh" else "risk gate (no detail)"
                    humanized = " + ".join(humanize(i, language) for i in items)
                    prefix = "风控护栏: " if language == "zh" else "risk gate: "
                    return f"{prefix}{humanized}"
            except (ValueError, SyntaxError):
                pass
        # Fallback: treat as single item (no brackets)
        if not inner:
            return "风控护栏(细节未知)" if language == "zh" else "risk gate (no detail)"
        humanized = humanize(inner.strip().strip("'\""), language)
        prefix = "风控护栏: " if language == "zh" else "risk gate: "
        return f"{prefix}{humanized}"
    # riskgate: 'failure_id' or "riskgate: failure_id" (legacy, no underscore)
    if reason.startswith("riskgate:"):
        inner = reason.split(":", 1)[1].strip().strip("'\"")
        if not inner:
            return "风控护栏(细节未知)" if language == "zh" else "risk gate (no detail)"
        humanized = humanize(inner, language)
        prefix = "风控护栏: " if language == "zh" else "risk gate: "
        return f"{prefix}{humanized}"
    if reason.startswith("exception:"):
        detail = reason.split(":", 1)[1].strip()
        if not detail:
            return "执行错误(无详情)" if language == "zh" else "execution error (no detail)"
        prefix = "执行错误: " if language == "zh" else "execution error: "
        return f"{prefix}{detail[:80]}"
    if reason.startswith("market_data:"):
        detail = reason.split(":", 1)[1].strip()
        if not detail:
            return "行情数据不可用" if language == "zh" else "market data unavailable"
        prefix = "行情数据不可用: " if language == "zh" else "market data unavailable: "
        return f"{prefix}{detail[:80]}"
    return reason
