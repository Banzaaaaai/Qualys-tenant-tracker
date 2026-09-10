"""Safe, best-effort version ordering.

The tenant-vs-snapshot comparison in comparison.py stays a plain string
inequality on purpose (see its module docstring) -- Qualys version
strings don't share one scheme. Determining the *latest publicly
announced* version among several candidates, though, genuinely needs
an ordering. This module provides one that only ever claims an
ordering when it's confident, and reports UNKNOWN otherwise rather
than guessing (spec: "favor correctness over assumptions").

Algorithm: tokenize into alternating digit-runs/letter-runs, compare
token by token. Two numeric tokens compare numerically. A differing
pair where either token is alphabetic (e.g. "SNAPSHOT" vs "12", or
"SNAPSHOT" vs "RC") is not safely orderable. Trailing numeric-only
tokens on the longer string are treated as zero-padding (so "4.9.4"
== "4.9.4.0", and "4.9.4" < "4.9.4.1"); a trailing non-numeric tail
(e.g. "-SNAPSHOT-1") is not safely orderable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_TOKEN_RE = re.compile(r"[A-Za-z]+|\d+")


class Ordering(str, Enum):
    EQUAL = "EQUAL"
    OLDER = "OLDER"  # a is older than b
    NEWER = "NEWER"  # a is newer than b
    UNKNOWN = "UNKNOWN"


def _tokenize(version: str) -> list[str]:
    return _TOKEN_RE.findall(version)


def compare_versions(a: str, b: str) -> Ordering:
    """Compare `a` to `b`. Returns how `a` relates to `b`."""
    if a == b:
        return Ordering.EQUAL

    ta, tb = _tokenize(a), _tokenize(b)
    length = min(len(ta), len(tb))

    for i in range(length):
        pa, pb = ta[i], tb[i]
        if pa == pb:
            continue
        if pa.isdigit() and pb.isdigit():
            na, nb = int(pa), int(pb)
            return Ordering.NEWER if na > nb else Ordering.OLDER
        return Ordering.UNKNOWN

    # Common prefix matched exactly (or one/both had zero tokens);
    # whichever has extra trailing tokens may just be zero-padded.
    tail = tb[length:] if len(tb) > len(ta) else ta[length:]
    if not tail:
        return Ordering.EQUAL
    if not all(tok.isdigit() for tok in tail):
        return Ordering.UNKNOWN
    if all(int(tok) == 0 for tok in tail):
        return Ordering.EQUAL
    return Ordering.OLDER if len(tb) > len(ta) else Ordering.NEWER


@dataclass
class LatestVersionResult:
    version: str | None
    confident: bool
    candidates: list[str]


def latest_version(versions: list[str]) -> LatestVersionResult:
    """Pick the newest of `versions`, or report that ordering is unsafe.

    Never raises. An empty list yields `version=None, confident=True`
    (nothing to be unsure about). Any single ambiguous pairwise
    comparison makes the whole result `confident=False` -- per spec,
    that must be reported as "unable to safely determine ordering"
    rather than guessing.
    """
    unique = list(dict.fromkeys(versions))
    if not unique:
        return LatestVersionResult(None, True, [])
    if len(unique) == 1:
        return LatestVersionResult(unique[0], True, unique)

    best = unique[0]
    for candidate in unique[1:]:
        ordering = compare_versions(candidate, best)
        if ordering == Ordering.UNKNOWN:
            return LatestVersionResult(None, False, unique)
        if ordering == Ordering.NEWER:
            best = candidate

    for candidate in unique:
        if candidate != best and compare_versions(best, candidate) == Ordering.UNKNOWN:
            return LatestVersionResult(None, False, unique)

    return LatestVersionResult(best, True, unique)
