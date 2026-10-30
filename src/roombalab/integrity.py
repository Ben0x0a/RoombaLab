"""Verification of the raw journal: payload hashes and the SHA-256 chain (dossier §6, TP 3).

Used by: roombalab.server (GET /api/verify).
Uses: roombalab.db.
"""

import hashlib

from roombalab.db import db


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_chain() -> dict:
    """Recompute every hash in seq order and report the FIRST break.

    The first break is what matters: every later chain value depends on it, so they all differ too
    and would only repeat the same finding.
    """
    prev_chain = None
    expected_seq = 1
    checked = 0
    for r in db.execute(
        "SELECT seq,source,operation,raw_payload,raw_sha256,previous_chain_sha256,chain_sha256 FROM raw_messages ORDER BY seq"
    ):
        problem = None
        if r["seq"] != expected_seq:
            problem = f"expected seq {expected_seq}, found {r['seq']} (row missing or inserted)"
        elif r["raw_sha256"] != _sha(r["raw_payload"]):
            problem = "raw_sha256 does not match the payload (payload modified)"
        elif r["previous_chain_sha256"] != prev_chain:
            problem = "previous_chain_sha256 does not match the previous row"
        elif r["chain_sha256"] != _sha(f"{prev_chain or ''}|{r['seq']}|{r['source']}|{r['operation'] or ''}|{r['raw_payload']}"):
            problem = "chain_sha256 does not match (seq, source, operation or payload modified)"
        if problem:
            return {"ok": False, "checked": checked, "break_at_seq": r["seq"], "reason": problem}
        prev_chain = r["chain_sha256"]
        expected_seq += 1
        checked += 1
    return {"ok": True, "checked": checked, "last_chain_sha256": prev_chain}
