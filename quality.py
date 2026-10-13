"""Puntaje de calidad de datos antes y después de la limpieza."""
from __future__ import annotations

import datetime as _dt

import pandas as pd

from .config import NULL_TOKENS
from .normalize import clean_text_value, parse_date, parse_number
from .structure import blank_mask, is_blank


def _kind(v):
    if isinstance(v, (_dt.datetime, _dt.date, pd.Timestamp)):
        return "fecha"
    if isinstance(v, bool):
        return "texto"
    if isinstance(v, (int, float)):
        return "num"
    return "texto"


def score(comp: float, val: float, uni: float, unif: float) -> float:
    return round(100 * (0.30 * comp + 0.30 * val + 0.15 * uni + 0.25 * unif), 1)


def quality_before(df: pd.DataFrame) -> dict:
    total = df.size or 1
    blank = blank_mask(df)
    n_blank = int(blank.values.sum())
    tokens = 0
    unparsable = 0
    mismatched = 0
    data_cells = total - n_blank
    for j in range(df.shape[1]):
        col = df.iloc[:, j]
        vals = [v for v in col.tolist() if not is_blank(v)]
        if not vals:
            continue
        tok = [isinstance(v, str) and v.strip().lower() in NULL_TOKENS for v in vals]
        tokens += sum(tok)
        real = [v for v, t in zip(vals, tok) if not t]
        if not real:
            continue
        nums = sum(parse_number(v) is not None for v in real)
        dates = sum(parse_date(v) is not None for v in real if not isinstance(v, (int, float)))
        if nums / len(real) >= 0.6:
            unparsable += len(real) - nums
            mismatched += sum(isinstance(v, str) for v in real)
        elif dates / len(real) >= 0.6:
            unparsable += len(real) - dates
            mismatched += sum(isinstance(v, str) for v in real)
        else:
            kinds = [_kind(v) for v in real]
            dom = max(set(kinds), key=kinds.count)
            mismatched += sum(k != dom for k in kinds)
    dup = int(df.astype(str).duplicated().sum()) if len(df) else 0
    comp = (total - n_blank - tokens) / total
    val = 1 - (tokens + unparsable) / max(data_cells, 1)
    uni = 1 - dup / max(len(df), 1)
    unif = 1 - mismatched / max(data_cells, 1)
    clamp = lambda x: max(0.0, min(1.0, x))
    comp, val, uni, unif = map(clamp, (comp, val, uni, unif))
    return {"completitud": round(comp, 4), "validez": round(val, 4), "unicidad": round(uni, 4), "uniformidad": round(unif, 4), "puntaje": score(comp, val, uni, unif)}


def quality_after(df: pd.DataFrame, flagged_cells: int, dup_rows: int = 0) -> dict:
    total = df.size or 1
    nn = int(df.notna().values.sum())
    comp = nn / total
    val = 1 - flagged_cells / max(nn, 1)
    uni = 1 - dup_rows / max(len(df), 1)
    unif = 1.0
    clamp = lambda x: max(0.0, min(1.0, x))
    comp, val, uni, unif = map(clamp, (comp, val, uni, unif))
    return {"completitud": round(comp, 4), "validez": round(val, 4), "unicidad": round(uni, 4), "uniformidad": round(unif, 4), "puntaje": score(comp, val, uni, unif)}
