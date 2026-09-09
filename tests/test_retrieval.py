from app.services.retrieval import score_text, tokenize


def test_tokenize_removes_common_stopwords() -> None:
    tokens = tokenize("How can I check my fare and payment?")
    assert "fare" in tokens
    assert "payment" in tokens
    assert "how" not in tokens


def test_generic_policy_words_do_not_turn_unknown_topics_into_knowledge_hits() -> None:
    assert tokenize("What is the moon policy?") == {"moon"}
    assert tokenize("Apakah polisi bulan?") == {"apakah", "bulan"}
    assert "政策" not in tokenize("月球政策是什么？")


def test_score_text_rewards_relevant_overlap() -> None:
    score = score_text("fare payment refund", "Fare estimates and payment issues can be reviewed.")
    assert score > 0


def test_tokenize_builds_searchable_chinese_bigrams() -> None:
    assert {"车费", "付款"} <= tokenize("车费和付款")
