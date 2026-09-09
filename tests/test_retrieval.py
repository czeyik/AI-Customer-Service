from app.services.retrieval import score_text, tokenize


def test_tokenize_removes_common_stopwords() -> None:
    tokens = tokenize("How can I check my fare and payment?")
    assert "fare" in tokens
    assert "payment" in tokens
    assert "how" not in tokens


def test_score_text_rewards_relevant_overlap() -> None:
    score = score_text("fare payment refund", "Fare estimates and payment issues can be reviewed.")
    assert score > 0


def test_tokenize_builds_searchable_chinese_bigrams() -> None:
    assert {"车费", "付款"} <= tokenize("车费和付款")
