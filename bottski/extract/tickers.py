"""Ticker extraction from document text.

Three match tiers, in confidence order:
  cashtag       $TSLA anywhere in text                     (0.95)
  provider_tag  symbols pre-tagged by the news provider    (0.95)
  bare          ALL-CAPS token that is a universe symbol   (0.80)
  company_name  alias phrase from universe.csv             (0.70)

The AMBIGUOUS set is the heart of this module: tickers that are also English
words or common finance abbreviations. Those NEVER match as bare tokens —
only as cashtags or aliases. Tune it against tests/fixtures/extraction_labels.

Versioned: bump EXTRACTOR_VERSION on any behavior change; rows are keyed by
(document_id, symbol, extractor_version) so re-extraction never mutates history.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from bottski.config import Settings

logger = logging.getLogger("bottski.extract")

EXTRACTOR_VERSION = "x3"

# Universe tickers that are English words / common abbreviations. Bare-token
# matching is disabled for these; cashtag and alias matching still work.
AMBIGUOUS = {
    "AI", "ALL", "ON", "SO", "IT", "DD", "CAR", "OPEN", "NOW", "SNOW", "NET",
    "ARM", "COIN", "HOOD", "SQ", "V", "MA", "C", "T", "D", "F", "M", "GM",
    "GS", "MS", "GD", "GE", "CAT", "DE", "KO", "PM", "MO", "BA", "HD", "LOW",
    "TM", "LI", "U", "RUN", "PLUG", "BEAM", "EDIT", "SAVE", "REAL", "LOVE",
    "PLAY", "FAST", "COST", "NICE", "WELL", "SNAP", "META", "UBER", "DASH",
    "MAR", "LUV", "EA", "BB", "SMR", "HUT", "MU", "PG", "CL", "UPS", "A",
    "O", "K", "SEE", "ANY", "BIG", "FOR", "ONE", "TWO", "CEO", "IPO", "ATH",
    "YOLO", "FOMO", "EV", "AR", "VR", "PE", "PT", "USA", "SEC", "FED", "GDP",
    "CPI", "ETF", "API", "APP", "TV", "ELON",
}

# Sell-side banks named as the ACTOR of a rating action ("Citigroup Maintains
# Buy on X") are commentary about X, not the bank; alias matches for these
# banks are suppressed in analyst-action headlines. Cashtag/bare/provider-tag
# matches for the banks still work.
ANALYST_BANK_SYMBOLS = {"C", "GS", "MS", "JPM", "WFC", "BAC", "SCHW", "AXP"}
ANALYST_ACTION_RE = re.compile(
    r"\b(maintains|upgrades|downgrades|reiterates|initiates coverage)\b"
    r"|price target (to|of) \$?\d"
    r"|\banalysts? (says?|sees?|said|expects?)\b",
    re.IGNORECASE,
)

CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5}(?:\.[A-Za-z])?)\b")
BARE_RE = re.compile(r"\b([A-Z]{1,5}(?:\.[A-Z])?)\b")


@dataclass(frozen=True)
class Match:
    symbol: str
    match_type: str  # 'cashtag' | 'provider_tag' | 'bare' | 'company_name'
    confidence: float


class Universe:
    def __init__(self, symbols: set[str], aliases: dict[str, str]):
        self.symbols = symbols
        self.aliases = aliases  # lowercase alias phrase -> symbol
        self._alias_res = {
            alias: re.compile(r"(?<!\w)" + re.escape(alias) + r"(?!\w)")
            for alias in aliases
        }

    @classmethod
    def load(cls, path: str | Path) -> "Universe":
        symbols: set[str] = set()
        aliases: dict[str, str] = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                sym = row["symbol"].strip().upper()
                symbols.add(sym)
                for alias in (row.get("aliases") or "").split(";"):
                    alias = alias.strip().lower()
                    if len(alias) >= 3:
                        aliases[alias] = sym
        return cls(symbols, aliases)


def extract(text: str, universe: Universe, provider_symbols: list[str] | None = None) -> list[Match]:
    """Extract ticker matches from one document's text. Deterministic; keeps
    the highest-confidence match per symbol."""
    best: dict[str, Match] = {}

    def offer(m: Match) -> None:
        cur = best.get(m.symbol)
        if cur is None or m.confidence > cur.confidence:
            best[m.symbol] = m

    for sym in provider_symbols or []:
        sym = sym.strip().upper()
        if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", sym):
            offer(Match(sym, "provider_tag", 0.95))

    for raw in CASHTAG_RE.findall(text):
        sym = raw.upper()
        if sym in universe.symbols:
            offer(Match(sym, "cashtag", 0.95))

    for sym in BARE_RE.findall(text):
        if sym in universe.symbols and sym not in AMBIGUOUS:
            offer(Match(sym, "bare", 0.80))

    lower = text.lower()
    is_analyst_action = bool(ANALYST_ACTION_RE.search(text))
    for alias, rex in universe._alias_res.items():
        sym = universe.aliases[alias]
        if is_analyst_action and sym in ANALYST_BANK_SYMBOLS:
            continue
        if rex.search(lower):
            offer(Match(sym, "company_name", 0.70))

    return sorted(best.values(), key=lambda m: (-m.confidence, m.symbol))


def run(settings: Settings, conn: sqlite3.Connection, limit: int | None = None) -> dict[str, int]:
    """Extract tickers for all documents lacking rows for this extractor
    version. Incremental and idempotent."""
    universe = Universe.load(settings.universe_file)
    q = """
        SELECT d.id, d.source, d.title, d.body, d.raw_json FROM raw_documents d
        WHERE NOT EXISTS (
            SELECT 1 FROM document_tickers t
            WHERE t.document_id = d.id AND t.extractor_version = ?
        ) ORDER BY d.id"""
    if limit:
        q += f" LIMIT {int(limit)}"
    docs = conn.execute(q, (EXTRACTOR_VERSION,)).fetchall()

    stats = {"docs": 0, "matches": 0}
    for doc in docs:
        text = " ".join(filter(None, [doc["title"], doc["body"]]))
        provider_symbols = None
        if doc["source"] == "news":
            provider_symbols = json.loads(doc["raw_json"] or "{}").get("symbols")
        matches = extract(text, universe, provider_symbols)
        for m in matches:
            conn.execute(
                "INSERT OR IGNORE INTO document_tickers (document_id, symbol,"
                " match_type, confidence, extractor_version) VALUES (?,?,?,?,?)",
                (doc["id"], m.symbol, m.match_type, m.confidence, EXTRACTOR_VERSION),
            )
        # Sentinel so processed-but-empty docs are not rescanned forever.
        if not matches:
            conn.execute(
                "INSERT OR IGNORE INTO document_tickers (document_id, symbol,"
                " match_type, confidence, extractor_version) VALUES (?,?,?,?,?)",
                (doc["id"], "__NONE__", "none", 0.0, EXTRACTOR_VERSION),
            )
        stats["docs"] += 1
        stats["matches"] += len(matches)
    conn.commit()
    logger.info("extraction done: %s", stats)
    return stats
