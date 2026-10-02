UNIT_MIN, UNITS = 5, 288
FULL = (1 << UNITS) - 1

def mask_busy(mask, start, length):
    """Mark units busy (1 = free, 0 = busy)."""
    return mask & ~(((1 << length) - 1) << start)

def free_starts(masks, length, buffer_units=0):
    c = FULL
    for m in masks: c &= m
    need = length + buffer_units
    r = c
    for i in range(1, need): r &= c >> i
    return [s for s in range(UNITS - need + 1) if r >> s & 1]

def rank(starts, requested, length, mask, w=(1.0, 0.5)):
    """Closeness to request + penalty for leaving unusable gaps. Top 3."""
    def gap_after(s):
        e, g = s + length, 0
        while e + g < UNITS and mask >> (e + g) & 1: g += 1
        return g
    def score(s):
        orphan = 1 if 0 < gap_after(s) < 3 else 0
        return w[0] * abs(s - requested) + w[1] * 10 * orphan
    return sorted(starts, key=score)[:3]

def unit_to_hhmm(u): return f"{u*UNIT_MIN//60:02d}:{u*UNIT_MIN%60:02d}"
