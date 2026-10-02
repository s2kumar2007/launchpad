import re
def norm(s): return re.sub(r"[^a-z0-9]", "", s.lower())

def jaro_winkler(a, b):
    if a == b: return 1.0
    if not a or not b: return 0.0
    d = max(max(len(a), len(b)) // 2 - 1, 0)
    ma, mb = [False]*len(a), [False]*len(b); m = 0
    for i, c in enumerate(a):
        for j in range(max(0, i-d), min(len(b), i+d+1)):
            if not mb[j] and b[j] == c: ma[i] = mb[j] = True; m += 1; break
    if not m: return 0.0
    t, k = 0, 0
    for i in range(len(a)):
        if ma[i]:
            while not mb[k]: k += 1
            t += a[i] != b[k]; k += 1
    j = (m/len(a) + m/len(b) + (m - t/2)/m) / 3
    p = 0
    while p < min(4, len(a), len(b)) and a[p] == b[p]: p += 1
    return j + p * 0.1 * (1 - j)

def trigrams(s):
    s = f"  {s} "; return {s[i:i+3] for i in range(len(s)-2)}

def score(q, c):
    q, c = norm(q), norm(c)
    ta, tb = trigrams(q), trigrams(c)
    return 0.6 * jaro_winkler(q, c) + 0.4 * len(ta & tb) / len(ta | tb)

def match(query, names, high=0.80, margin=0.08):
    ranked = sorted(((score(query, n), n) for n in names), reverse=True)
    if not ranked: return {"status": "none", "candidates": []}
    top = ranked[0]
    if top[0] >= high and (len(ranked) == 1 or top[0] - ranked[1][0] >= margin):
        return {"status": "match", "match": top[1], "score": round(top[0], 3)}
    return {"status": "ambiguous", "candidates": [n for _, n in ranked[:3]]}
