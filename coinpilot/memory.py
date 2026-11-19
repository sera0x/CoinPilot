"""The bot's long-term memory: the archive of everything it priced.

Every cycle the filter writes one deduped row per token that passed the hard
screens (the investable universe) into `sightings` — its price, score, age and
corroboration at sighting time. Later cycles re-price the oldest unresolved
sightings and compute what the token did AFTER we saw it. Aggregated by score
band, that is the edge table: "what happened to tokens we rated this highly".

The auditor reads that table to decide whether the entry bar deserves a nudge.
Resolved sightings older than 21 days are pruned so the archive stays small.
"""

from __future__ import annotations

import time

STUDY_BATCH = 25  # one DexScreener batch call re-prices up to 30 tokens
MIN_AGE_MINUTES = 60  # a sighting needs an hour before hindsight means anything
PRUNE_DAYS = 21
MAX_OUTCOME = 1_000.0
MIN_OUTCOME = -99.0


def record(connection, survivors: list[dict]) -> int:
    """Remember screen-passing tokens. A token with an unresolved sighting is
    skipped, so a token seen many cycles in a row counts once."""

    now = time.time()
    added = 0
    for item in survivors or []:
        snap = item.get("snapshot") or {}
        address = snap.get("token_address")
        price = snap.get("price_usd")
        if not address or not price:
            continue
        added += _insert_sighting(
            connection,
            ts=now,
            token_address=address,
            symbol=snap.get("symbol"),
            price=float(price),
            score=float((item.get("rule") or {}).get("score") or 0.0),
            liquidity=snap.get("liquidity_usd"),
            volume=snap.get("volume_h24"),
            age_minutes=snap.get("age_minutes"),
            corroboration=int(item.get("corroboration") or 0),
        )
    connection.commit()
    return added


def _insert_sighting(connection, **row) -> int:
    cursor = connection.execute(
        "INSERT INTO sightings (ts, token_address, symbol, price, score, liquidity, volume, age_minutes, corroboration) "
        "SELECT ?,?,?,?,?,?,?,?,? "
        "WHERE NOT EXISTS (SELECT 1 FROM sightings WHERE token_address = ? AND outcome IS NULL)",
        (
            row["ts"], row["token_address"], row["symbol"], row["price"], row["score"],
            row["liquidity"], row["volume"], row["age_minutes"], row["corroboration"],
            row["token_address"],
        ),
    )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def study(connection, chain: str = "solana", pricer=None) -> dict:
    """Re-price the oldest unresolved sightings and grade them.

    `pricer(addresses) -> {address: price}` is injectable for offline tests;
    the default is one DexScreener batch call per study pass.
    """

    cutoff = time.time() - MIN_AGE_MINUTES * 60
    rows = connection.execute(
        "SELECT id, token_address, price, symbol FROM sightings "
        "WHERE outcome IS NULL AND ts <= ? ORDER BY ts LIMIT ?",
        (cutoff, STUDY_BATCH),
    ).fetchall()

    outstanding = connection.execute(
        "SELECT COUNT(*) AS n FROM sightings WHERE outcome IS NULL"
    ).fetchone()["n"]

    if rows:
        if pricer is None:
            from . import market

            def pricer(addresses):
                try:
                    return {
                        snap["token_address"]: float(snap["price_usd"])
                        for snap in market.token_snapshots(chain, addresses).values()
                        if snap.get("price_usd")
                    }
                except Exception as exc:  # noqa: BLE001 - study failures must not kill the cycle
                    print(f"    memory: study pricer failed: {exc}")
                    return {}

        prices = pricer([row["token_address"] for row in rows])
        now = time.time()
        studied = 0
        for row in rows:
            current = prices.get(row["token_address"])
            if not current:
                continue
            entry_price = float(row["price"] or 0.0)
            if entry_price <= 0:
                continue
            outcome = max(MIN_OUTCOME, min(MAX_OUTCOME, (current / entry_price - 1.0) * 100.0))
            connection.execute(
                "UPDATE sightings SET outcome = ?, studied_at = ? WHERE id = ? AND outcome IS NULL",
                (outcome, now, row["id"]),
            )
            studied += 1
        connection.commit()
        outstanding = connection.execute(
            "SELECT COUNT(*) AS n FROM sightings WHERE outcome IS NULL"
        ).fetchone()["n"]
    else:
        studied = 0

    pruned = connection.execute(
        "DELETE FROM sightings WHERE outcome IS NOT NULL AND studied_at <= ?",
        (time.time() - PRUNE_DAYS * 86400,),
    ).rowcount
    connection.commit()

    return {"studied": studied, "pruned": pruned, "outstanding": int(outstanding)}


def edges(connection) -> list[dict]:
    """Average hindsight outcome per score bucket — the archive's edge table."""

    buckets = ((40, 50), (50, 60), (60, 70), (70, 80), (80, 101))
    out = []
    for low, high in buckets:
        row = connection.execute(
            "SELECT COUNT(*) AS n, AVG(outcome) AS avg FROM sightings "
            "WHERE outcome IS NOT NULL AND score >= ? AND score < ?",
            (low, high),
        ).fetchone()
        n = int(row["n"] or 0)
        if n:
            label = f"{low}-{high if high <= 100 else '+'}"
            out.append({"bucket": label, "n": n, "avg_outcome": round(float(row["avg"]), 1)})
    return out


def band_stats(connection, threshold: float) -> dict:
    """Hindsight outcomes of sightings that would pass today's entry bar, vs
    the ones below it. This is the evidence the auditor tunes against."""

    def pack(row) -> dict:
        n = int(row["n"] or 0)
        return {"n": n, "avg_outcome": round(float(row["avg"]), 1) if n else None}

    above = connection.execute(
        "SELECT COUNT(*) AS n, AVG(outcome) AS avg FROM sightings "
        "WHERE outcome IS NOT NULL AND score >= ?",
        (threshold,),
    ).fetchone()
    below = connection.execute(
        "SELECT COUNT(*) AS n, AVG(outcome) AS avg FROM sightings "
        "WHERE outcome IS NOT NULL AND score < ?",
        (threshold,),
    ).fetchone()
    return {"above": pack(above), "below": pack(below)}
