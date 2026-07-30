"""
channel_review.py — CSV channel detection & filtering for the upload review step.

Pure logic, no Streamlit: analyze a parsed RacePak/DataLink DataFrame for
duplicate and dead channels, and filter unwanted columns out of the raw CSV
bytes losslessly (original text values preserved, only columns dropped).

Duplicate semantics:
  • load_racepak_csv() dedups repeated header names as "Name_2", "Name_3", …
    (first occurrence keeps the bare name). Suffixed members of a name group
    are duplicate candidates; the bare first occurrence is the primary.
  • Cross-correlation at small lags confirms the duplicate and measures the
    timing offset (DataLink re-exports often carry a slightly shifted copy).
  • Differently-named channels that are near-exact copies at lag 0
    (r >= 0.9995) are also flagged.

Dead channels: no numeric samples, or a constant (zero-variance) trace.
Per product decision, dead channels default to KEEP (visibility, not a forced
decision); confirmed duplicates default to DROP.
"""

import re

import numpy as np
import pandas as pd

_SUFFIX_RE = re.compile(r"^(.*)_(\d+)$")

# Correlation thresholds
_DUP_R_MIN       = 0.99    # name-duplicate confirmation at best lag
_NEAR_COPY_R_MIN = 0.9995  # differently-named exact-copy detection at lag 0
_MAX_LAG_SAMPLES = 50      # ± lag search window (~1s at 0.02s sampling)


def base_name(name: str) -> str:
    """'Engine RPM_2' → 'Engine RPM'; bare names pass through."""
    m = _SUFFIX_RE.match(str(name))
    return m.group(1) if m else str(name)


def _corr_at_lag(a: np.ndarray, b: np.ndarray, lag: int):
    if lag < 0:
        x, y = a[-lag:], b[: len(b) + lag]
    elif lag > 0:
        x, y = a[: len(a) - lag], b[lag:]
    else:
        x, y = a, b
    if len(x) < 20:
        return None
    mask = ~(np.isnan(x) | np.isnan(y))
    if mask.sum() < 20:
        return None
    xs, ys = x[mask], y[mask]
    if xs.std() == 0 or ys.std() == 0:
        return None
    return float(np.corrcoef(xs, ys)[0, 1])


def _is_monotonicish(s: pd.Series) -> bool:
    """True for cumulative/ramp channels (time, distance, turn counters).
    Two ramps always correlate near 1.0, so content-based duplicate detection
    is meaningless between them — they must be excluded from that scan."""
    v = s.dropna().to_numpy(dtype=float)
    if len(v) < 20:
        return False
    d = np.diff(v)
    return float((d >= 0).mean()) > 0.97 or float((d <= 0).mean()) > 0.97


def best_lag_corr(a: pd.Series, b: pd.Series, max_lag: int = _MAX_LAG_SAMPLES):
    """(best_r, best_lag_samples) over ±max_lag; (0.0, 0) if uncomputable."""
    av = a.to_numpy(dtype=float)
    bv = b.to_numpy(dtype=float)
    best_r, best_lag = 0.0, 0
    for lag in range(-max_lag, max_lag + 1):
        r = _corr_at_lag(av, bv, lag)
        if r is not None and abs(r) > abs(best_r):
            best_r, best_lag = r, lag
    return best_r, best_lag


def analyze_channels(df: pd.DataFrame, time_col: str) -> "list[dict]":
    """One dict per non-time channel:
    {name, n, min, max, mean, badge, keep_default, duplicate_of, offset_s, r}
    """
    chans = [c for c in df.columns if c != time_col]

    # Sampling interval for offset-in-seconds reporting
    dt = 0.0
    try:
        t = df[time_col].dropna().to_numpy(dtype=float)
        if len(t) > 2:
            dt = float(np.median(np.diff(t)))
    except Exception:
        pass

    rows_by_name: dict = {}
    for c in chans:
        s = df[c].dropna()
        n = int(len(s))
        dead = n < 5 or (n > 0 and float(s.std()) == 0.0)
        rows_by_name[c] = {
            "name": c,
            "n": n,
            "min": round(float(s.min()), 3) if n else None,
            "max": round(float(s.max()), 3) if n else None,
            "mean": round(float(s.mean()), 3) if n else None,
            "badge": ("💤 dead — no data" if n < 5 else
                      ("💤 dead — constant value" if dead else "")),
            "keep_default": True,          # dead stays KEEP per product decision
            "duplicate_of": None,
            "offset_s": None,
            "r": None,
            "_dead": dead,
        }

    # ── Name-based duplicate groups (primary = bare first occurrence) ────────
    groups: dict = {}
    for c in chans:
        groups.setdefault(base_name(c), []).append(c)

    flagged = set()

    # Special case: extra copies of the time column itself (DataLink repeats
    # "Time" mid-file for the EGT section → parses as "Time_2"). Primary is
    # the time column, which never appears in the table.
    _tbase = base_name(time_col)
    for m in groups.pop(_tbase, []):
        r, lag = best_lag_corr(df[time_col], df[m])
        off = round(lag * dt, 3) if dt else None
        row = rows_by_name[m]
        row.update({"duplicate_of": time_col, "offset_s": off,
                    "r": round(r, 4) if r else None})
        if r >= _DUP_R_MIN or row["_dead"]:
            row["keep_default"] = False
            row["badge"] = (
                f"⚠️ duplicate of time column {time_col}"
                + (f" (offset {off:+g}s, r={r:.4f})" if r else "")
            )
        else:
            row["badge"] = (f"❓ same name as time column {time_col} but "
                            f"traces differ (r={r:.3f}) — review")
        flagged.add(m)
    for base, members in groups.items():
        if len(members) < 2:
            continue
        primary = members[0]
        for m in members[1:]:
            r, lag = best_lag_corr(df[primary], df[m])
            off = round(lag * dt, 3) if dt else None
            row = rows_by_name[m]
            row.update({"duplicate_of": primary, "offset_s": off,
                        "r": round(r, 4) if r else None})
            if r >= _DUP_R_MIN or row["_dead"]:
                row["keep_default"] = False
                row["badge"] = (
                    f"⚠️ duplicate of {primary}"
                    + (f" (offset {off:+g}s" if off is not None else " (offset n/a")
                    + (f", r={r:.4f})" if r else ")")
                )
            else:
                # Same name but traces disagree — flag, keep, let the user call it
                row["badge"] = (
                    f"❓ same name as {primary} but traces differ "
                    f"(r={r:.3f}) — review"
                )
            flagged.add(m)
            if not rows_by_name[primary]["badge"]:
                rows_by_name[primary]["badge"] = "✔ primary"

    # ── Cross-name exact copies at lag 0 ─────────────────────────────────────
    # Monotonic (cumulative/ramp) channels are excluded pairwise: distance,
    # turn counters, and clocks all correlate ~1.0 with each other by nature,
    # which is not evidence of duplication.
    live = [c for c in chans
            if c not in flagged and not rows_by_name[c]["_dead"]]
    _mono = {c: _is_monotonicish(df[c]) for c in live}
    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            a, b = live[i], live[j]
            if base_name(a) == base_name(b):
                continue
            if _mono[a] and _mono[b]:
                continue
            r = _corr_at_lag(df[a].to_numpy(dtype=float),
                             df[b].to_numpy(dtype=float), 0)
            if r is not None and r >= _NEAR_COPY_R_MIN:
                row = rows_by_name[b]  # later column = the copy
                if row["duplicate_of"] is None:
                    row.update({
                        "duplicate_of": a,
                        "r": round(r, 4),
                        "keep_default": False,
                        "badge": f"⚠️ near-exact copy of {a} (r={r:.4f})",
                    })

    out = []
    for c in chans:
        row = rows_by_name[c]
        row.pop("_dead", None)
        out.append(row)
    return out


def filter_csv_bytes(file_bytes: bytes, drop_names: "list[str]") -> bytes:
    """Drop columns (by parsed/deduped channel name) from the raw CSV bytes.

    Replays load_racepak_csv()'s header-dedup walk to map parsed names back to
    raw column indices, then rebuilds every line keeping only surviving
    columns — original cell text is preserved verbatim.
    """
    if not drop_names:
        return file_bytes
    drop = set(drop_names)

    text = file_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return file_bytes

    raw_headers = [h.strip() for h in lines[0].split(",")]
    trailing_empty = bool(raw_headers) and raw_headers[-1] == ""
    if trailing_empty:
        raw_headers = raw_headers[:-1]

    # Same dedup walk as load_racepak_csv
    seen: dict = {}
    parsed: list = []
    for h in raw_headers:
        if h in seen:
            seen[h] += 1
            parsed.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 1
            parsed.append(h)

    keep_idx = [i for i, p in enumerate(parsed) if p not in drop]
    if not keep_idx:
        return file_bytes  # never produce an empty file

    out_lines = [",".join(raw_headers[i] for i in keep_idx)
                 + ("," if trailing_empty else "")]
    for line in lines[1:]:
        if not line.strip():
            out_lines.append(line)
            continue
        vals = line.split(",")
        out_lines.append(",".join(
            (vals[i] if i < len(vals) else "") for i in keep_idx
        ))
    return ("\n".join(out_lines) + "\n").encode("utf-8")
