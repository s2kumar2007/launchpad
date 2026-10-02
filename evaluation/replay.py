"""Gap Filler replay. Estimates refill rate / recovered revenue from no-show data under STATED assumptions.
  python -m evaluation.replay --synthetic 5000        (synthetic data, labelled as such)
  python -m evaluation.replay --csv evaluation/data/<file>.csv   (needs a 'No-show' column with Yes/No)"""
import argparse, csv, json, random
from pathlib import Path

def run(rows, price, waitlist_prob, accept_prob, seed, source):
    rnd = random.Random(seed); noshows = sum(r == "Yes" for r in rows)
    refilled = sum(1 for r in rows if r == "Yes" and rnd.random() < waitlist_prob and rnd.random() < accept_prob)
    return {"source": source, "appointments": len(rows), "no_shows": noshows, "refilled": refilled,
            "refill_rate": round(refilled / noshows, 3) if noshows else 0, "recovered_revenue": round(refilled * price, 2),
            "assumptions": {"price": price, "waitlist_has_candidate": waitlist_prob, "candidate_accepts": accept_prob, "seed": seed},
            "note": "Estimate under stated assumptions, not a measured result."}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--csv"); ap.add_argument("--synthetic", type=int)
    ap.add_argument("--price", type=float, default=500); ap.add_argument("--waitlist-prob", type=float, default=0.5)
    ap.add_argument("--accept-prob", type=float, default=0.6); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="evaluation/results.json"); a = ap.parse_args()
    if a.csv: rows = [r["No-show"] for r in csv.DictReader(open(a.csv, encoding="utf-8"))]; src = "public-data replay"
    elif a.synthetic: g = random.Random(a.seed); rows = ["Yes" if g.random() < 0.2 else "No" for _ in range(a.synthetic)]; src = "synthetic replay"
    else: ap.error("give --csv or --synthetic")
    res = run(rows, a.price, a.waitlist_prob, a.accept_prob, a.seed, src); Path(a.out).write_text(json.dumps(res, indent=2), encoding="utf-8"); print(json.dumps(res, indent=2))
