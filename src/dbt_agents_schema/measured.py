"""`measured_values_in_context` detection (SPEC 8). Pure logic.

Published text should describe method, not measurements. Each line is checked in order:
count phrases (unless exempt), then allow patterns, then any number >= `min_number`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dbt_agents_schema.config import MeasuredValuesConfig

BUILTIN_NOUNS = (
    r"rows?|records?|tables?|columns?|events?|jobs?|users?|customers?|accounts?|orders?"
    r"|transactions?"
)
BUILTIN_EXEMPT = (r"\b1 (row|record) per\b",)
BUILTIN_ALLOW = (
    r"[<>]=\s*\d",
    r"\b0-100\b",
    r"\bone (row|record) per\b",
    r"\b1:1\b",
    r"accepted values",
    r"percentile",
    r"\bp\d{2}\b",
    r"\b(ISO|RFC|IEC|IEEE)[ -]?\d+",
)
_LIST_MARKER = re.compile(r"^\d+[.)]\s+")
_NUMBER = re.compile(r"(?<![\w.\-])\d[\d,]*(\.\d+)?")
_FIX = (
    "use qualitative wording or the query that derives the value, or add a pattern to "
    "check.measured_values.allow / exempt"
)


@dataclass(frozen=True)
class MeasuredValues:
    """Compiled patterns for one `check.measured_values` setting."""

    settings: MeasuredValuesConfig
    _count: tuple[re.Pattern[str], ...] = field(init=False, repr=False)
    _exempt: tuple[re.Pattern[str], ...] = field(init=False, repr=False)
    _allow: tuple[re.Pattern[str], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        nouns = "|".join((BUILTIN_NOUNS, *(f"(?:{n})" for n in self.settings.count_nouns)))
        count = (
            re.compile(rf"\d[\d,]*\s*({nouns})\b", re.IGNORECASE),
            re.compile(r"\b\d[\d,]*\s+of\s+\d", re.IGNORECASE),
            re.compile(r"\b\d+(\.\d+)?x\b", re.IGNORECASE),
            re.compile(r"\b\d+(\.\d+)?[KMB]\b"),  # suffix case-sensitive
        )
        object.__setattr__(self, "_count", count)
        object.__setattr__(self, "_exempt", _compile((*BUILTIN_EXEMPT, *self.settings.exempt)))
        object.__setattr__(self, "_allow", _compile((*BUILTIN_ALLOW, *self.settings.allow)))

    def match(self, raw_line: str) -> str | None:
        """The matched text if `raw_line` contains a measured value, else None."""
        line = _LIST_MARKER.sub("", raw_line.strip(), count=1)
        if not line:
            return None
        # 1. Count phrases, unless the line is exempt.
        for pattern in self._count:
            found = pattern.search(line)
            if found is not None:
                if any(p.search(line) for p in self._exempt):
                    break
                return found.group(0)
        # 2. Allowed lines may contain numbers.
        if any(p.search(line) for p in self._allow):
            return None
        # 3. Any number at or above min_number.
        for found in _NUMBER.finditer(line):
            if float(found.group(0).replace(",", "")) >= self.settings.min_number:
                return found.group(0)
        return None

    def scan(self, text: str) -> list[tuple[int, str]]:
        """(1-based line number, message) for each line of `text` with a measured value."""
        out: list[tuple[int, str]] = []
        for number, line in enumerate(text.splitlines(), start=1):
            matched = self.match(line)
            if matched is not None:
                out.append((number, message(matched, line)))
        return out


def message(matched: str, line: str) -> str:
    excerpt = line.strip()[:100]
    return f'measured value "{matched}" in published text (line: "{excerpt}"); {_FIX}'


def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)
