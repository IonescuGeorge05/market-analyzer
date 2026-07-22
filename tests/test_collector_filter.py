"""
Tests for the collector's relevance filtering.

Why these exist: a live AAPL fetch returned a PyPI package listing, a Show HN
post, a memecoin story and two duplicated headlines - only ~6 of 15 articles
were about Apple. Every junk item costs a token and pulls the weighted average
toward noise, so the filter needs to stay honest.

All pure functions: no network, no API key.
"""
import pytest

from app.agents.collector import (
    _company, _domain, _filter_articles, _is_junk, _mentions_subject,
    _norm_title, _query, _relevance,
)
from app.schemas import ContentItem


def art(title, text="", url="https://reuters.com/x", source="Reuters"):
    return ContentItem(source=source, kind="news", title=title, text=text, url=url)


# ---------- ticker -> company ----------

def test_known_tickers_map_to_names():
    assert _company("AAPL") == "Apple"
    assert _company("TSLA") == "Tesla"
    assert _company("aapl") == "Apple"      # case insensitive


def test_unknown_ticker_falls_back_to_symbol():
    assert _company("ZZZZ") == "ZZZZ"


def test_query_searches_name_and_symbol_with_finance_clause():
    q = _query("AAPL")
    assert "Apple" in q and "AAPL" in q
    assert "stock" in q          # finance clause stops recipes and reviews


def test_query_for_unmapped_ticker_does_not_duplicate_symbol():
    q = _query("ZZZZ")
    assert q.count("ZZZZ") == 1


# ---------- junk domains ----------

@pytest.mark.parametrize("url,expected", [
    ("https://pypi.org/project/trail-gmd/", True),
    ("https://www.pypi.org/project/x", True),        # www stripped
    ("https://ticker-line.com/", True),
    ("https://news.ycombinator.com/item?id=1", True),
    ("https://reuters.com/markets/apple", False),
    ("https://www.barchart.com/story", False),
])
def test_junk_domain_detection(url, expected):
    assert _is_junk(url) is expected


def test_domain_parsing_ignores_scheme_www_and_path():
    assert _domain("https://www.Reuters.com/a/b?c=1") == "reuters.com"


# ---------- subject mentions ----------

def test_mentions_company_name():
    assert _mentions_subject("Apple beats earnings", "AAPL")


def test_mentions_bare_ticker():
    assert _mentions_subject("AAPL climbs 3%", "AAPL")


def test_ticker_with_punctuation_still_matches():
    assert _mentions_subject("Why buy AAPL, really?", "AAPL")


def test_unrelated_headline_does_not_match():
    assert not _mentions_subject("Robinhood Chain sees memecoin surge", "AAPL")


def test_single_letter_ticker_does_not_match_random_words():
    """V is Visa. It must not match every word starting with V."""
    assert not _mentions_subject("Vodafone and Verizon rally", "V")
    assert _mentions_subject("V climbs after earnings", "V")


# ---------- common-word tickers (regression) ----------

def test_lowercase_common_word_is_not_a_ticker_mention():
    """
    Regression, from a real live result. Ticker SHOP is Shopify, but matching
    case-insensitively made the ordinary word "shop" qualify, and the
    collector returned an article about an Indian restaurant.
    """
    dosa = "Thane dosa shop owner reveals why Rs 13 lakh monthly revenue now leaves little"
    assert not _mentions_subject(dosa, "SHOP")


def test_uppercase_symbol_still_matches():
    assert _mentions_subject("SHOP climbs 4% after earnings", "SHOP")


def test_cashtag_form_matches():
    """$AAPL is the common convention on social sources."""
    assert _mentions_subject("Loading up on $AAPL before earnings", "AAPL")


@pytest.mark.parametrize("word_ticker,innocent", [
    ("COIN", "Rare coin collection sells at auction"),
    ("ALL", "All eyes on the Fed this week"),
    ("ON", "Traders piled on after the open"),
    ("IT", "It was a volatile session for tech"),
    ("CAR", "Used car prices slipped again"),
    ("GO", "Investors go defensive ahead of data"),
])
def test_tickers_that_collide_with_english_words(word_ticker, innocent):
    """These all false-positived before the case-sensitive fix."""
    assert not _mentions_subject(innocent, word_ticker)


def test_company_name_match_stays_case_insensitive():
    """Only the SYMBOL check is case-sensitive; prose capitalizes freely."""
    assert _mentions_subject("shopify beats revenue estimates", "SHOP")
    assert _mentions_subject("Shopify beats revenue estimates", "SHOP")


def test_common_word_ticker_survives_the_full_filter():
    """End to end: the dosa article is dropped, the Shopify one is kept."""
    items = [
        art("Thane dosa shop owner reveals monthly revenue struggles"),
        art("Shopify shares jump on strong quarterly earnings"),
    ]
    kept = _filter_articles(items, "SHOP", 10)
    assert [i.title for i in kept] == ["Shopify shares jump on strong quarterly earnings"]


# ---------- company map coverage ----------

def test_retail_popular_tickers_are_mapped():
    """
    Unmapped tickers search the bare symbol and starve: PLTR pulled 15
    candidates where AAPL pulled 99, and RIVN fell under the 5-item
    thin-evidence threshold.
    """
    for ticker, name in [("PLTR", "Palantir"), ("RIVN", "Rivian"),
                         ("COIN", "Coinbase"), ("SHOP", "Shopify"),
                         ("HOOD", "Robinhood"), ("GME", "GameStop")]:
        assert _company(ticker) == name


def test_mapped_ticker_query_includes_the_company_name():
    assert "Palantir" in _query("PLTR")


def test_distinctive_symbol_stays_in_the_query():
    """AAPL is safe to search and adds recall, so it is kept."""
    q = _query("AAPL")
    assert "Apple" in q and "AAPL" in q


def test_ambiguous_symbol_is_left_out_of_the_query():
    """
    Regression: ("Shopify" OR SHOP) returned 98 shopping and travel articles,
    none of which mentioned Shopify. The symbol floods the candidate pool
    before our filter ever sees it, so it must not go in the query.
    """
    q = _query("SHOP")
    assert "Shopify" in q
    assert "SHOP" not in q.replace("Shopify", "")


def test_ambiguous_symbols_are_all_excluded():
    for t in ("COIN", "CAT", "NET", "ARM", "LINK"):
        stripped = _query(t).replace(_company(t), "")
        assert t not in stripped


# ---------- dedup ----------

def test_normalized_titles_collapse_punctuation_and_case():
    a = _norm_title("Apple's Stock: Is It a Buy?")
    b = _norm_title("apples stock is it a buy")
    assert a == b


# ---------- the filter as a whole ----------

def test_drops_junk_domains():
    items = [
        art("trail-gmd added to PyPI", url="https://pypi.org/x"),
        art("Apple beats earnings", url="https://reuters.com/x"),
    ]
    kept = _filter_articles(items, "AAPL", 10)
    assert [i.title for i in kept] == ["Apple beats earnings"]


def test_drops_articles_that_never_mention_the_subject():
    items = [
        art("Robinhood Chain sees 85% memecoin trading"),
        art("Oracle shares crash, embarrassing Ellison"),
        art("Apple shares climb on earnings beat"),
    ]
    kept = _filter_articles(items, "AAPL", 10)
    assert [i.title for i in kept] == ["Apple shares climb on earnings beat"]


def test_deduplicates_republished_headlines():
    """Biztoc and Barchart carried the identical Synopsys headline."""
    items = [
        art("Apple stock jumps on earnings", source="Biztoc.com"),
        art("Apple stock jumps on earnings", source="Yahoo"),
    ]
    assert len(_filter_articles(items, "AAPL", 10)) == 1


def test_respects_the_limit():
    items = [art(f"Apple stock update {n}") for n in range(50)]
    assert len(_filter_articles(items, "AAPL", 15)) == 15


def test_body_only_mention_is_kept_but_ranked_below_title_mention():
    title_hit = art("Apple stock rallies")
    body_hit = art("Tech roundup for Monday",
                   text="Apple was among the movers, analysts said")
    assert _relevance(title_hit, "AAPL") > _relevance(body_hit, "AAPL")
    assert len(_filter_articles([body_hit, title_hit], "AAPL", 10)) == 2


def test_best_articles_survive_when_limit_bites():
    """With the limit at 1, the title-mention beats the body-mention."""
    body_hit = art("Tech roundup", text="Apple was among the movers, analysts said")
    title_hit = art("Apple earnings beat expectations")
    kept = _filter_articles([body_hit, title_hit], "AAPL", 1)
    assert kept[0].title == "Apple earnings beat expectations"


# ---------- market signal: the product-journalism problem ----------

def test_product_articles_are_dropped_even_though_they_name_the_company():
    """
    The regression that made this rule necessary: after the first pass of
    filtering, AAPL returned iPhone leaks and Apple Watch redesigns, and TSLA
    returned car reviews. They name the company but say nothing about the stock.
    """
    product = [
        art("Apple Watch Ultra 4 Leaks Point to First Major Redesign"),
        art("The iPhone Air 2 Leak Solves the Ultra-Thin Phone's Compromise"),
        art("Apple is Rumored to Push the 2026 Apple TV 4K Price to $249"),
    ]
    assert _filter_articles(product, "AAPL", 10) == []


def test_market_article_survives_alongside_product_noise():
    items = [
        art("Apple Watch Ultra 4 Leaks Point to a Redesign"),
        art("Apple Q3 earnings beat as iPhone revenue climbs"),
    ]
    kept = _filter_articles(items, "AAPL", 10)
    assert [i.title for i in kept] == ["Apple Q3 earnings beat as iPhone revenue climbs"]


def test_earnings_story_outranks_product_story_naming_the_company():
    product = art("Apple TV 4K price rumored to rise")       # 'price' is weak only
    market = art("Apple shares jump after earnings beat")
    assert _relevance(market, "AAPL") > _relevance(product, "AAPL")


def test_market_signal_may_come_from_the_description():
    """A headline can carry the company while the body carries the market."""
    item = art("Apple unseats Nvidia as most valuable firm",
               text="Investors pushed the stock higher after strong quarterly results")
    assert _filter_articles([item], "AAPL", 10) == [item]


def test_empty_and_untitled_input_is_safe():
    assert _filter_articles([], "AAPL", 10) == []
    assert _filter_articles([art("")], "AAPL", 10) == []


def test_filter_never_raises_on_odd_input():
    """Design rule 3: the collector must not fail hard."""
    odd = [art("Apple", url=""), art("AAPL", url="not-a-url"),
           art("Apple stock", text="x" * 5000)]
    assert _filter_articles(odd, "AAPL", 10)
