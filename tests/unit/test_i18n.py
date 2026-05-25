from __future__ import annotations

from pathlib import Path

import pytest

from quanterback.i18n import I18n


def test_render_en_template(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "hello.j2").write_text("Hello {{ name }}")
    i = I18n(language="en", templates_dir=tmp_path)
    assert i.render("hello", name="world") == "Hello world"


def test_missing_lang_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="templates not found"):
        I18n(language="zh", templates_dir=tmp_path)


def test_language_property(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    i = I18n(language="en", templates_dir=tmp_path)
    assert i.language == "en"


def test_strict_undefined_raises(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "test.j2").write_text("Value: {{ missing_var }}")
    i = I18n(language="en", templates_dir=tmp_path)
    with pytest.raises(Exception):  # jinja2.UndefinedError
        i.render("test")


def test_render_with_dict_context(tmp_path: Path) -> None:
    (tmp_path / "zh").mkdir()
    (tmp_path / "zh" / "msg.j2").write_text("{{ ticker }}: {{ action }}")
    i = I18n(language="zh", templates_dir=tmp_path)
    assert i.render("msg", ticker="AAPL", action="BUY") == "AAPL: BUY"


def test_render_with_list_context(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "list.j2").write_text("Items: {{ items | join(', ') }}")
    i = I18n(language="en", templates_dir=tmp_path)
    result = i.render("list", items=["A", "B", "C"])
    assert result == "Items: A, B, C"


def test_template_with_conditionals(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "cond.j2").write_text(
        "{% if count > 0 %}{{ count }} items{% else %}No items{% endif %}"
    )
    i = I18n(language="en", templates_dir=tmp_path)
    assert i.render("cond", count=5) == "5 items"
    assert i.render("cond", count=0) == "No items"
