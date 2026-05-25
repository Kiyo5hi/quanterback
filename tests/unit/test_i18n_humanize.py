from pathlib import Path

from quanterback.i18n import I18n


def test_i18n_exposes_humanize_filter(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "demo.j2").write_text(
        "{{ ['sanity_max_drawdown'] | humanize_checks }}"
    )
    i18n = I18n(language="en", templates_dir=tmp_path)
    assert i18n.render("demo") == "drawdown > 50%"


def test_i18n_humanize_filter_uses_lang(tmp_path: Path) -> None:
    (tmp_path / "zh").mkdir()
    (tmp_path / "zh" / "demo.j2").write_text(
        "{{ ['sanity_max_drawdown'] | humanize_checks }}"
    )
    i18n = I18n(language="zh", templates_dir=tmp_path)
    assert i18n.render("demo") == "回撤超过 50%"
