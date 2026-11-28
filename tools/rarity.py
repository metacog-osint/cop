"""Comparable rarity: one figure per item that reads the same in every layer (feed contract 2.1, F4). Standard library.

A program's tier and its strength rule mean different things in each layer, so neither compares across programs. An
item's `signal` carries the one number its tier rests on. COP ranks that number within its own history of the same
subject and publishes `cop_rarity` on the item:

- COP's readings of a subject are its `signal.value` once per data day (the day of `data_as_of`), kept in COP's
  memory (tools/memory.py, `sv`). Today's own day is left out, so the item is compared with what came before it.
- `signal.tail` says which direction is unusual: "high" counts earlier readings at least as large, "low" at least as
  small, "both" at least as far from `signal.expected` (or from the median of the earlier readings when the feed
  gives no expected value).
- With k such readings among n, the exceedance is (k + 1) / (n + 1): never finer than 1 in n + 1, as a program's own
  `signal.exceedance` is never finer than 1 in baseline_n + 1. Shown as "about 1 day in N".
- COP's figure is used from 60 earlier readings. Below that, the program's own `signal.exceedance` stands in, marked
  as the program's. With neither, there is no figure, and the item says why.
- A signal with no `tail` names no direction, so COP computes nothing for it (the program's own figure still stands).

The figure is per layer: it is never added across layers, and it changes no tier and no pick.
"""
import math

MIN_READINGS = 60
FORMAT = "cop-rarity/1"
PHRASE = {"high": "as high or higher", "low": "as low or lower", "both": "as far from usual or further"}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def day_of(value):
    return value[:10] if isinstance(value, str) and len(value) >= 10 and value[4] == "-" else None


def earlier_readings(record, day):
    """The subject's readings on data days before `day`, oldest first."""
    sv = (record or {}).get("sv") or {}
    return [sv[d] for d in sorted(sv) if (day is None or d < day) and _num(sv[d])]


def _median(xs):
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def exceedance(value, tail, readings, expected=None):
    """(k + 1) / (n + 1): the share of the readings, with this one, at least as extreme in the tail's direction."""
    if tail == "high":
        k = sum(1 for x in readings if x >= value)
    elif tail == "low":
        k = sum(1 for x in readings if x <= value)
    elif tail == "both":
        centre = expected if _num(expected) else _median(readings)
        k = sum(1 for x in readings if abs(x - centre) >= abs(value - centre))
    else:
        return None
    return (k + 1) / (len(readings) + 1)


def _one_in(ex):
    return max(1, round(1 / ex))


def rarity(p, record, program_name, memory_read=True):
    """The item's cop_rarity, or None when it carries no usable signal."""
    sig = p.get("signal")
    if not isinstance(sig, dict) or not _num(sig.get("value")):
        return None
    tail = sig.get("tail") if sig.get("tail") in PHRASE else None
    readings = earlier_readings(record, day_of(p.get("data_as_of"))) if memory_read else []
    n = len(readings)
    out = {"format": FORMAT, "exceedance": None, "one_in": None, "readings": n if memory_read else None,
           "whose": None, "baseline_n": None, "tail": tail, "text": None}
    if not memory_read:
        why = "COP's history was not read on this build"
    elif not tail:
        why = "the feed names no direction (tail) for this signal, so COP computes no figure of its own"
    elif n < MIN_READINGS:
        why = f"COP holds {n} daily readings of this subject, fewer than {MIN_READINGS}"
    else:
        ex = exceedance(sig["value"], tail, readings, sig.get("expected"))
        out.update(exceedance=round(ex, 4), one_in=_one_in(ex), whose="cop")
        out["text"] = (f"About 1 day in {out['one_in']} has been {PHRASE[tail]} in COP's history of this subject "
                       f"({n} daily readings).")
        return out
    own = sig.get("exceedance")
    if _num(own) and 0 < own <= 1:
        bn = sig.get("baseline_n") if isinstance(sig.get("baseline_n"), int) else None
        out.update(exceedance=round(own, 4), one_in=_one_in(own), whose="program", baseline_n=bn)
        base = f"{program_name}'s own baseline of {bn} readings" if bn else f"{program_name}'s own baseline"
        out["text"] = f"About 1 in {out['one_in']} against {base} ({program_name}'s figure; {why})."
        return out
    out["text"] = f"No comparable rarity yet: {why}, and {program_name} gives no figure of its own."
    return out


def add(features, mem, names, key):
    """Put cop_rarity on every item with a signal. mem is COP's memory or None when it was not read. Returns counts."""
    counts = {"cop": 0, "program": 0, "none": 0}
    subjects = (mem or {}).get("subjects") or {}
    for f in features:
        p = f.get("properties") or {}
        p.pop("cop_rarity", None)
        program = p.get("cop_program") or p.get("program")
        r = rarity(p, subjects.get(key(p)), names.get(program, program), memory_read=mem is not None)
        if r is None:
            continue
        p["cop_rarity"] = r
        counts[r["whose"] or "none"] += 1
    return counts
