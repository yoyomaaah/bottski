from bottski.score.vader import FINANCE_LEXICON, score_text


def test_finance_slang_scores_directionally():
    assert score_text("TSLA mooning, tendies incoming, diamond hands")["compound"] > 0.3
    assert score_text("bagholders rekt, total rug, stock tanking hard")["compound"] < -0.3


def test_puts_and_calls_carry_direction():
    bull = score_text("loading up on calls, very bullish")["compound"]
    bear = score_text("loading up on puts, very bearish")["compound"]
    assert bull > 0 > bear


def test_neutralized_words():
    # 'gross' (as in gross margin) must not read as disgust
    assert FINANCE_LEXICON["gross"] == 0.0
    assert abs(score_text("gross margin of 40 percent")["compound"]) < 0.2


def test_plain_vader_still_works():
    assert score_text("this is wonderful great news")["compound"] > 0.5
