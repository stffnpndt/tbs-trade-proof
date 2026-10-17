"""
Verify the trade-proof record. Anyone can run this - it needs only Python 3.

  git clone https://github.com/<owner>/<repo>.git && cd <repo>
  git worktree add ../proofs proofs          # the timestamp proofs live on branch 'proofs'
  python verify.py --proofs ../proofs/heads

Checks
  1. Hash chain: every line links to the previous one and (if the content is
     published) its hash matches its content. One edited or deleted line breaks
     every hash after it.
  2. Timestamps: each proofs/heads/<seq>-<hash>.txt names a chain head that
     exists in the journal; its .ots file proves the head existed at that time.
     Check the .ots files at https://opentimestamps.org (drop the .txt and its .ots)
     or with `pip install opentimestamps-client` and `ots verify <file>.ots`.
  3. Optional, for the account owner or an auditor:
       --full journal_full.jsonl   revealed entries (fingerprint mode) match the public hashes
       --okx                       every recorded fill exists on OKX with identical values,
                                   and no OKX futures fill in the covered period is missing.
                                   Needs a READ-ONLY key in env vars NOTARY_OKX_API_KEY,
                                   NOTARY_OKX_API_SECRET, NOTARY_OKX_API_PASSPHRASE
                                   (OKX keeps fill history for 3 months).
"""

import argparse
import glob
import hashlib
import json
import os
import sys
import time

GENESIS = "0" * 64
META = {"seq", "recorded_at", "prev", "hash"}


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def chain_hash(prev, body):
    return hashlib.sha256((prev + canon(body)).encode("utf-8")).hexdigest()


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def check_chain(lines, label):
    errors, prev = 0, GENESIS
    for i, line in enumerate(lines):
        seq = line.get("seq")
        if seq != i + 1:
            print(f"  FAIL {label} line {i + 1}: seq is {seq}, expected {i + 1}")
            errors += 1
        if line.get("prev") != prev:
            print(f"  FAIL {label} seq {seq}: prev does not match the hash of the line before")
            errors += 1
        if set(line) - META:          # content present -> recompute
            body = {k: v for k, v in line.items() if k not in ("prev", "hash")}
            if chain_hash(line.get("prev", ""), body) != line.get("hash"):
                print(f"  FAIL {label} seq {seq}: hash does not match content")
                errors += 1
        prev = line.get("hash")
    return errors


def check_proofs(lines, proofs_dir):
    heads = {line["hash"]: line["seq"] for line in lines}
    files = sorted(glob.glob(os.path.join(proofs_dir, "*.txt")))
    errors = stamped = pending = covered = 0
    for txt in files:
        with open(txt, encoding="utf-8") as f:
            head = f.read().strip()
        name = os.path.basename(txt)
        if head not in heads:
            print(f"  FAIL {name}: head {head[:12]} is not in the journal")
            errors += 1
            continue
        covered = max(covered, heads[head])
        if f"-{head}.txt" not in name:
            print(f"  FAIL {name}: file name does not match its content")
            errors += 1
        ots = txt + ".ots"
        if not os.path.exists(ots):
            print(f"  FAIL {name}: no .ots proof")
            errors += 1
            continue
        with open(ots, "rb") as f:
            data = f.read()
        # a completed proof contains a Bitcoin block header attestation (tag 0x0588960d73d71901)
        if bytes.fromhex("0588960d73d71901") in data:
            stamped += 1
        else:
            pending += 1
    print(f"  {len(files)} timestamped heads: {stamped} confirmed in Bitcoin, {pending} still pending "
          f"(pending ones complete within hours). Latest timestamped seq: {covered} of {len(lines)}.")
    return errors


def check_full(public, full):
    errors = check_chain(full, "full")
    if len(full) != len(public):
        print(f"  FAIL full journal has {len(full)} lines, public has {len(public)}")
        errors += 1
    for p, f in zip(public, full):
        if p["hash"] != f["hash"] or p["seq"] != f["seq"]:
            print(f"  FAIL seq {p['seq']}: revealed entry does not match the public fingerprint")
            errors += 1
    return errors


def check_okx(entries):
    import ccxt
    try:
        from dotenv import load_dotenv
        for p in ("notary.env", os.path.join("..", "notary.env")):
            if os.path.exists(p):
                load_dotenv(p)
                break
    except ImportError:
        pass
    ex = ccxt.okx({"apiKey": os.environ["NOTARY_OKX_API_KEY"],
                   "secret": os.environ["NOTARY_OKX_API_SECRET"],
                   "password": os.environ["NOTARY_OKX_API_PASSPHRASE"],
                   "enableRateLimit": True, "hostname": "my.okx.com"})
    fills = [e for e in entries if e.get("event") == "fill"]
    if not fills:
        print("  no fills recorded yet")
        return 0
    t0 = min(int(e["okx"].get("fillTime") or e["okx"]["ts"]) for e in fills)
    okx, cursor = {}, None
    while True:
        params = {"instType": "FUTURES", "limit": "100"}
        if cursor:
            params["after"] = cursor
        page = ex.privateGetTradeFillsHistory(params).get("data", [])
        for f in page:
            okx[f["billId"]] = f
        if len(page) < 100 or int(page[-1].get("fillTime") or page[-1]["ts"]) < t0:
            break
        cursor = page[-1]["billId"]
        time.sleep(0.3)

    errors = 0
    for e in fills:
        f = okx.get(e["okx"]["billId"])
        if f is None:
            print(f"  FAIL seq {e['seq']}: bill {e['okx']['billId']} not found on OKX "
                  f"(or older than 3 months)")
            errors += 1
            continue
        for k in ("instId", "ordId", "clOrdId", "side", "fillSz", "fillPx", "fee"):
            if k in e["okx"] and str(f.get(k)) != str(e["okx"][k]):
                print(f"  FAIL seq {e['seq']}: {k} journal={e['okx'][k]} okx={f.get(k)}")
                errors += 1
    recorded = {e["okx"]["billId"] for e in fills}
    t1 = max(int(e["okx"].get("fillTime") or e["okx"]["ts"]) for e in fills)
    for bill, f in okx.items():
        t = int(f.get("fillTime") or f["ts"])
        if t0 <= t <= t1 and bill not in recorded:
            print(f"  FAIL OKX fill {f['instId']} {f['side']} {f['fillSz']} @ {f['fillPx']} "
                  f"(bill {bill}) is missing from the journal")
            errors += 1
    print(f"  {len(fills)} recorded fills compared with {len(okx)} OKX fills")
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default="journal.jsonl")
    ap.add_argument("--proofs", help="folder with <seq>-<hash>.txt + .ots (branch 'proofs', heads/)")
    ap.add_argument("--full", help="revealed full journal (fingerprint mode)")
    ap.add_argument("--okx", action="store_true", help="cross-check fills against OKX (read-only key)")
    a = ap.parse_args()

    public = read_jsonl(a.journal)
    print(f"Journal: {len(public)} entries")
    errors = check_chain(public, "journal")
    print("  chain " + ("OK" if not errors else "BROKEN"))
    entries = public
    if a.full:
        full = read_jsonl(a.full)
        e = check_full(public, full)
        print("  revealed entries " + ("match" if not e else "DO NOT match"))
        errors += e
        entries = full
    if a.proofs:
        print("Timestamps:")
        errors += check_proofs(public, a.proofs)
    if a.okx:
        print("OKX cross-check:")
        errors += check_okx(entries)
    print("\nRESULT: " + ("all checks passed" if not errors else f"{errors} problem(s) found"))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
