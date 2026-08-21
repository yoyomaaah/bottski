"""VADER sentiment scoring with finance-lexicon boosts.

Versioned: a lexicon or logic change bumps SCORER_VERSION and produces new
rows; old scores are never mutated, so scorer generations stay comparable on
identical raw text.

Granularity note (v1): one compound score per document, assigned to every
ticker extracted from it. Per-ticker context windows are a future version.
"""

from __future__ import annotations

import logging
import sqlite3
from functools import lru_cache

from bottski.config import Settings
from bottski.extract.tickers import EXTRACTOR_VERSION

logger = logging.getLogger("bottski.score")

SCORER_VERSION = "vader-fin-v1"

# Additions/overrides on VADER's lexicon. Values are VADER valences (-4..+4).
# Unigrams only — VADER is unigram-based. Finance slang it would otherwise
# misread or miss entirely.
FINANCE_LEXICON = {
    "moon": 2.5, "mooning": 2.5, "rocket": 1.5, "tendies": 2.0,
    "stonks": 1.5, "printing": 1.8, "breakout": 1.5, "undervalued": 1.8,
    "bullish": 2.5, "calls": 1.2, "long": 0.8, "buy": 1.0, "buying": 1.0,
    "rip": 1.5, "ripping": 2.0, "squeeze": 1.0, "ath": 2.0, "green": 1.2,
    "gains": 1.8, "winner": 1.5, "beat": 1.2, "beats": 1.2, "upgraded": 1.5,
    "outperform": 1.5, "rally": 1.5, "surge": 1.5, "soars": 2.0, "soar": 2.0,
    "bearish": -2.5, "puts": -1.2, "short": -0.8, "shorting": -1.2,
    "sell": -1.0, "selling": -1.0, "dump": -2.0, "dumping": -2.2,
    "bagholder": -2.5, "bagholders": -2.5, "bags": -1.5, "rekt": -3.0,
    "guh": -2.5, "drilling": -2.0, "tank": -2.0, "tanking": -2.5,
    "tanked": -2.5, "crash": -2.5, "crashing": -2.8, "plunge": -2.2,
    "plunges": -2.2, "overvalued": -1.8, "downgraded": -1.5, "misses": -1.5,
    "miss": -1.2, "red": -1.2, "losses": -1.8, "loss": -1.5, "bubble": -1.5,
    "scam": -3.0, "fraud": -3.0, "delisted": -2.5, "bankruptcy": -3.0,
    "bankrupt": -3.0, "underperform": -1.5, "warns": -1.5, "cuts": -1.2,
    # neutralize words VADER scores but finance uses neutrally
    "shares": 0.0, "stock": 0.0, "gross": 0.0,
}


@lru_cache(maxsize=1)
def get_analyzer():
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    analyzer = SentimentIntensityAnalyzer()
    analyzer.lexicon.update(FINANCE_LEXICON)
    return analyzer


def score_text(text: str) -> dict[str, float]:
    return get_analyzer().polarity_scores(text)


def run(settings: Settings, conn: sqlite3.Connection) -> dict[str, int]:
    """Score every (document, symbol) pair from the current extractor version
    that lacks a row for the current scorer version. Incremental, idempotent."""
    rows = conn.execute(
        """
        SELECT t.document_id, t.symbol, d.title, d.body
        FROM document_tickers t
        JOIN raw_documents d ON d.id = t.document_id
        WHERE t.extractor_version = ? AND t.symbol != '__NONE__'
          AND NOT EXISTS (
            SELECT 1 FROM document_sentiment s
            WHERE s.document_id = t.document_id AND s.symbol = t.symbol
              AND s.scorer_version = ?
          )
        ORDER BY t.document_id
        """,
        (EXTRACTOR_VERSION, SCORER_VERSION),
    ).fetchall()

    stats = {"scored": 0}
    doc_cache: dict[int, dict[str, float]] = {}
    for r in rows:
        if r["document_id"] not in doc_cache:
            text = " ".join(filter(None, [r["title"], r["body"]]))
            doc_cache[r["document_id"]] = score_text(text)
        sc = doc_cache[r["document_id"]]
        conn.execute(
            "INSERT OR IGNORE INTO document_sentiment (document_id, symbol,"
            " compound, pos, neu, neg, scorer_version) VALUES (?,?,?,?,?,?,?)",
            (r["document_id"], r["symbol"], sc["compound"], sc["pos"],
             sc["neu"], sc["neg"], SCORER_VERSION),
        )
        stats["scored"] += 1
    conn.commit()
    logger.info("scoring done: %s", stats)
    return stats
