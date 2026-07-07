"""
financial_parser.py
--------------------
Turns messy, free-text financial figures into structured numbers that
code can actually compare.

WHY THIS FILE EXISTS:
Claude extracts guidance and actuals as natural-language strings, because
that's how these figures actually get spoken on an earnings call --
things like "$22.1B", "$21.5-22.0B", "~76.7%", "$0.42". You cannot
determine whether a company beat, missed, or met guidance by comparing
two strings. You need real numbers.

This module has exactly one job: turn a string like "$21.5-22.0B" into
a ParsedValue with real float numbers on it. It does not decide beat
vs. miss -- that judgment call lives in credibility_tracker.py, which
uses this module's output. Keeping "parse the text" and "judge the
comparison" as two separate responsibilities means each one is small,
testable, and easy to explain on its own.

This module makes NO calls to Claude or any API. It is pure Python
string parsing -- given the same input, it always produces the same
output. That determinism is the entire point: it's what makes the
credibility score something you can defend with a formula instead of
something you have to trust an AI's wording for.
"""

import re
from dataclasses import dataclass
from typing import Optional


# Multipliers so "$22.1B" and "22100" (already in millions) end up on the
# same numeric scale before we ever compare them.
UNIT_MULTIPLIERS = {
    "b": 1_000_000_000,
    "bn": 1_000_000_000,
    "billion": 1_000_000_000,
    "m": 1_000_000,
    "mm": 1_000_000,
    "million": 1_000_000,
    "k": 1_000,
}

# Matches a range like "21.5-22.0" or "21.5 to 22.0", with an optional
# unit letter (b/m/k) attached to either number. Company guidance is
# routinely given as a range rather than a single figure.
_RANGE_PATTERN = re.compile(
    r"(-?\d+\.?\d*)\s*(b|bn|billion|m|mm|million|k)?\s*(?:-|to)\s*(-?\d+\.?\d*)\s*(b|bn|billion|m|mm|million|k)?"
)

# Matches a single figure like "22.1b" or "76.7" with an optional unit.
_SINGLE_PATTERN = re.compile(r"(-?\d+\.?\d*)\s*(b|bn|billion|m|mm|million|k)?")


@dataclass
class ParsedValue:
    """
    A normalized numeric representation of a financial figure.

    low / high: identical for a single point estimate (e.g. "$22.1B" ->
                low=high=22_100_000_000). Different for a range.
    is_percent: True for margins/growth rates. We deliberately do NOT
                apply the billion/million multipliers to percentages --
                "76.7%" means 76.7 percentage points, not 76.7 billion
                percentage points.
    raw:        the original input string, kept so you can always trace
                a parsed number back to exactly what Claude wrote. This
                matters for auditability -- if a client ever asks "how
                did you get this score," you can show the original text.
    """
    low: float
    high: float
    is_percent: bool
    raw: str

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2

    @property
    def is_range(self) -> bool:
        return self.low != self.high


def parse_financial_value(text: Optional[str]) -> Optional[ParsedValue]:
    """
    Attempts to parse a free-text financial figure into a ParsedValue.

    Returns None if no number could be extracted. Callers MUST handle
    the None case -- this is not an edge case to ignore, it's the
    expected outcome whenever Claude's text is qualitative rather than
    numeric (e.g. "double-digit growth," "in line with expectations").
    Silently assuming a number is always available is exactly the kind
    of hidden failure we're trying to get rid of.

    Handles:
      - Single values:        "$22.1B", "76.7%", "$0.42"
      - Ranges:               "$21.5-22.0B", "21.5-22.0%", "21.5 to 22.0"
      - Approximation words:  "~$22B", "approximately $22B", "about 76%"
    """
    if not text:
        return None

    cleaned = text.strip().lower()
    # Approximation words don't change the number, they just mean "point
    # estimate, not exact" -- which we already treat as a point estimate
    # (low == high), so we simply strip these words out.
    for word in ("approximately", "about", "~"):
        cleaned = cleaned.replace(word, "")

    is_percent = "%" in cleaned
    cleaned = cleaned.replace("$", "").replace("%", "").replace(",", "")

    range_match = _RANGE_PATTERN.search(cleaned)
    if range_match:
        low_raw, low_unit, high_raw, high_unit = range_match.groups()
        # A unit (like "B") sometimes appears only once in a range, e.g.
        # "$21.5-22.0B" -- both numbers share the trailing unit. Use
        # whichever one was actually captured.
        unit = low_unit or high_unit
        multiplier = 1 if is_percent else UNIT_MULTIPLIERS.get(unit, 1)
        low = float(low_raw) * multiplier
        high = float(high_raw) * multiplier
        return ParsedValue(
            low=min(low, high),
            high=max(low, high),
            is_percent=is_percent,
            raw=text,
        )

    single_match = _SINGLE_PATTERN.search(cleaned)
    if single_match:
        value_raw, unit = single_match.groups()
        multiplier = 1 if is_percent else UNIT_MULTIPLIERS.get(unit, 1)
        value = float(value_raw) * multiplier
        return ParsedValue(low=value, high=value, is_percent=is_percent, raw=text)

    # No number found at all -- e.g. "flat", "N/A", "not provided".
    return None
