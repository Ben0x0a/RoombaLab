"""Verification of the raw log: payload hashes, the SHA-256 chain, and gaps left by recorded deletions.

Used by: roombalab.server (GET /api/verify).
Uses: roombalab.db, roombalab.retention (names of the deletion records).
"""

import bisect
import hashlib
import json

from roombalab.db import db
from roombalab.retention import RETENTION_OPERATION_NAME, RETENTION_SOURCE_NAME


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _recorded_deletions() -> list[list[int]]:
    """Merged [first, last] seq ranges that retention records say were deleted."""
    ranges = []
    for (payload,) in db.execute(
        "SELECT raw_payload FROM raw_messages WHERE source=? AND operation=?", (RETENTION_SOURCE_NAME, RETENTION_OPERATION_NAME)
    ):
        try:
            ranges += json.loads(payload)["deleted_seq_ranges"]
        except (ValueError, KeyError, TypeError):
            continue  # a malformed record covers nothing, so any gap it claims is still reported
    merged: list[list[int]] = []
    for a, b in sorted(ranges):
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def _covered(merged: list[list[int]], a: int, b: int) -> bool:
    i = bisect.bisect_right(merged, [a, float("inf")]) - 1
    return i >= 0 and merged[i][0] <= a and b <= merged[i][1]


def verify_chain() -> dict:
    """Recompute every hash in seq order and report the FIRST break.

    Each row is checked against its own stored previous_chain_sha256, and consecutive rows must link.
    A gap in seq is accepted only when a retention record lists exactly those seqs as deleted (their
    content and chain values are in the archive named by that record). The first break is what matters:
    every later chain value depends on it, so later rows would only repeat the same finding.
    """
    deletions = _recorded_deletions()
    prev_seq, prev_chain, checked, gaps = 0, None, 0, 0
    for r in db.execute(
        "SELECT seq,source,operation,raw_payload,raw_sha256,previous_chain_sha256,chain_sha256 FROM raw_messages ORDER BY seq"
    ):
        problem = None
        if r["seq"] != prev_seq + 1:
            if _covered(deletions, prev_seq + 1, r["seq"] - 1):
                gaps += 1
            else:
                problem = f"rows seq {prev_seq + 1}–{r['seq'] - 1} are missing and not recorded as deleted by retention"
        elif r["previous_chain_sha256"] != prev_chain:
            problem = "previous_chain_sha256 does not match the previous row"
        if problem is None and r["raw_sha256"] != _sha(r["raw_payload"]):
            problem = "raw_sha256 does not match the payload (payload modified)"
        elif problem is None and r["chain_sha256"] != _sha(
            f"{r['previous_chain_sha256'] or ''}|{r['seq']}|{r['source']}|{r['operation'] or ''}|{r['raw_payload']}"
        ):
            problem = "chain_sha256 does not match (seq, source, operation or payload modified)"
        if problem:
            return {"ok": False, "checked": checked, "break_at_seq": r["seq"], "reason": problem, "recorded_gaps": gaps}
        prev_seq, prev_chain = r["seq"], r["chain_sha256"]
        checked += 1
    return {"ok": True, "checked": checked, "recorded_gaps": gaps, "last_chain_sha256": prev_chain}
