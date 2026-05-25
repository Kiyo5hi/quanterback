from quanterback.domain.market import NewsItem


def test_news_item_minimal() -> None:
    n = NewsItem(title="X", publisher="Y", age_hours=2.5)
    assert n.title == "X"
    assert n.link is None


def test_news_item_age_must_be_nonneg() -> None:
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        NewsItem(title="X", publisher="Y", age_hours=-1)
