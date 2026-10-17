# Trade proof — Trading By Signal

A tamper-evident public record of every trade the automated trading system places on
OKX Europe (X-Perp futures). It is written by a program, not by hand, and cannot be
changed afterwards without anyone noticing.

## How it works

1. **Source: OKX itself.** A separate program reads every futures fill and every closed
   position from OKX with a read-only API key. OKX's values (order ID, size, price,
   fee, time, profit) are copied verbatim, so every line can be checked against the
   exchange.
2. **Hash chain.** Each line in `journal.jsonl` contains the SHA-256 hash of the line
   before it. Editing or deleting one line — for example, to hide a losing trade —
   breaks every hash after it.
3. **Timestamps outside our control.** On every update, a GitHub Action stamps the newest
   chain head with [OpenTimestamps](https://opentimestamps.org), which anchors it in the
   Bitcoin blockchain, and saves the journal file to the
   [Wayback Machine](https://web.archive.org). Proofs live on the `proofs` branch.
   A stamped head proves that the head, and with it every earlier entry, existed at that
   time and has not changed since.
4. **Completeness.** Every fill on the futures account is recorded, including manual
   ones (`"source": "other"`). An auditor with a read-only key can confirm that no OKX
   fill is missing (`verify.py --okx`).

Each entry is recorded within about a minute of the fill (`recorded_at`). Entries marked
`"backfill": true` were recorded when the record started, from OKX's history, and are
only proven from their `recorded_at` time onward.

## Verify it yourself

```bash
git clone https://github.com/<owner>/<repo>.git
cd <repo>
git worktree add ../proofs proofs
python verify.py --proofs ../proofs/heads
```

To check a timestamp independently, upload `heads/<seq>-<hash>.txt` and its `.ots` file at
https://opentimestamps.org.

## Fields

| Field | Meaning |
|---|---|
| `seq` | Running number, no gaps |
| `recorded_at` | When the entry was written (UTC) |
| `event` | `fill` (one execution) or `position_closed` (OKX's closed-position record, incl. liquidations) |
| `source` | `bridge` = placed by the trading system, `other` = placed by hand |
| `strategy` | Which strategy placed the order (from the system's own records); empty if unknown |
| `okx` | Values exactly as reported by OKX (`ordId`, `fillSz` in contracts, `fillPx`, `fee`, `fillTime` in ms, …) |
| `prev`, `hash` | Hash chain |

Not investment advice. Past results do not predict future results.
