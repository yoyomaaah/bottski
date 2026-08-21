"""Extractor unit tests — the ambiguity rules are the contract."""

from bottski.extract.tickers import AMBIGUOUS, Match, Universe, extract

UNI = Universe(
    symbols={"TSLA", "NVDA", "AI", "CAT", "GME", "SPY", "F", "BRK.B", "HOOD", "MU"},
    aliases={"tesla": "TSLA", "caterpillar": "CAT", "c3.ai": "AI", "robinhood": "HOOD"},
)


def syms(matches, types=None):
    return {m.symbol for m in matches if types is None or m.match_type in types}


def test_cashtag_always_matches_even_ambiguous():
    m = extract("loading up on $AI and $TSLA calls", UNI)
    assert syms(m, {"cashtag"}) == {"AI", "TSLA"}


def test_bare_matches_only_unambiguous_universe_symbols():
    m = extract("NVDA and GME to the moon, the CAT sat on the mat with AI", UNI)
    assert syms(m, {"bare"}) == {"NVDA", "GME"}   # CAT and AI are AMBIGUOUS
    assert "CAT" not in syms(m)


def test_lowercase_never_matches_bare():
    assert extract("nvda is cheap", UNI) == []


def test_word_the_word_f_and_common_words_do_not_match():
    m = extract("F in the chat. IT IS ON ALL DAY.", UNI)
    assert syms(m) == set()  # F is ambiguous; others not in universe or ambiguous


def test_company_name_alias():
    m = extract("Tesla and Caterpillar reported earnings; Robinhood fell.", UNI)
    assert syms(m, {"company_name"}) == {"TSLA", "CAT", "HOOD"}


def test_alias_requires_word_boundary():
    m = extract("neighborhoods are nice", UNI)  # contains 'hood' but not alias
    assert "HOOD" not in syms(m)


def test_provider_tags_pass_through():
    m = extract("irrelevant text", UNI, provider_symbols=["MU", "TSLA"])
    assert syms(m, {"provider_tag"}) == {"MU", "TSLA"}


def test_highest_confidence_wins_per_symbol():
    m = extract("$TSLA tesla TSLA", UNI)
    (match,) = [x for x in m if x.symbol == "TSLA"]
    assert match.match_type == "cashtag" and match.confidence == 0.95


def test_class_share_ticker():
    m = extract("BRK.B keeps grinding", UNI)
    assert "BRK.B" in syms(m, {"bare"})


def test_ambiguous_set_covers_known_traps():
    for trap in ("AI", "ALL", "IT", "ON", "SO", "DD", "CAR", "OPEN", "NOW", "COST"):
        assert trap in AMBIGUOUS


def test_analyst_action_suppresses_bank_alias():
    uni = Universe(symbols={"C", "GS", "TGT"}, aliases={"citigroup": "C", "goldman sachs": "GS"})
    m = extract("Citigroup Maintains Neutral on Target, Raises Price Target to $160", uni)
    assert "C" not in syms(m)
    m2 = extract("Goldman Sachs Upgrades Target to Buy", uni)
    assert "GS" not in syms(m2)


def test_bank_alias_still_matches_outside_analyst_context():
    uni = Universe(symbols={"C"}, aliases={"citigroup": "C"})
    m = extract("Citigroup announces 10,000 layoffs in restructuring", uni)
    assert "C" in syms(m, {"company_name"})


def test_musk_alias_removed_from_universe_file():
    real = Universe.load("universe.csv")
    assert "elon musk" not in real.aliases
    assert real.aliases.get("tesla") == "TSLA"


def test_analyst_says_phrasing_also_suppresses_bank_alias():
    uni = Universe(symbols={"JPM", "MRVL"}, aliases={"jpmorgan": "JPM", "marvell": "MRVL"})
    m = extract(
        "Marvell's AI Deal Could Unlock Opportunity, Analyst Says "
        "JPMorgan sees strong earnings upside for Marvell", uni)
    assert "JPM" not in syms(m)
    assert "MRVL" in syms(m)
