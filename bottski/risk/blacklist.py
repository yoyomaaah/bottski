"""User exclusion list: sectors and symbols the bot must never BUY.

Design invariants:
- Blocks entries only. Sells are never blocked (you must always be able to
  exit); existing positions in newly-listed names unwind by normal exit rules.
- Blacklisted names STAY in the observation panel and research statistics —
  they are excluded from the portfolio, not from the experiment.
- Enforced as a risk rail, so exclusions show up in the decision log as
  blocked counterfactuals ("what did my exclusions cost or save me?").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Blacklist:
    symbols: set[str] = field(default_factory=set)
    sectors: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: str | Path) -> "Blacklist":
        bl = cls()
        p = Path(path)
        if not p.exists():
            return bl
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("sector:"):
                bl.sectors.add(line.split(":", 1)[1].strip().lower())
            else:
                bl.symbols.add(line.upper())
        return bl

    def matches(self, symbol: str, sector: str | None) -> bool:
        return symbol.upper() in self.symbols or (
            sector is not None and sector.lower() in self.sectors)

    def describe(self) -> str:
        parts = [f"sector:{s}" for s in sorted(self.sectors)] + sorted(self.symbols)
        return ", ".join(parts) if parts else "(empty)"
