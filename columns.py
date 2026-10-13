"""Tipificación de columnas: fecha / entero / decimal / id / categoría / texto."""
from __future__ import annotations

import datetime as _dt
import difflib
import re
from collections import Counter

import numpy as np
import pandas as pd

from .changelog import ChangeLog
from .config import ID_HINTS, CleaningConfig
from .normalize import clean_text_value, parse_date, parse_number, strip_accents


def _nonnull(s: pd.Series) -> pd.Series:
    return s[s.notna()]


def clean_text_columns(df: pd.DataFrame, log: ChangeLog) -> pd.DataFrame:
    """Limpia espacios, mojibake, tokens nulos. Registra cuántas celdas cambiaron."""
    out = df.copy()
    for col in out.columns:
        s = out[col]
        changed, ejemplo = 0, None
        new = []
        for idx, v in s.items():
            nv = clean_text_value(v)
            same = (nv is None and (v is None or (isinstance(v, float) and v != v))) or (nv is not None and nv == v and type(nv) == type(v))
            if not same:
                changed += 1
                if ejemplo is None:
                    ejemplo = (idx, v, nv)
            new.append(nv)
        out[col] = pd.Series(new, index=s.index, dtype=object)
        if changed:
            nulls = sum(1 for v in s.tolist() if isinstance(v, str) and (v.strip().lower() in {"-", "--", "n/a", "na", "s/d", "nan", "null", "none"} or v.strip().startswith("#")))
            log.add("texto_normalizado", str(col), changed, ejemplo[0], ejemplo[1], ejemplo[2],
                    "Espacios múltiples/NBSP, codificación y marcadores de nulo (-, N/A, #REF!, #N/A) estandarizados")
    return out


def _is_id_name(name: str) -> bool:
    toks = set(name.split("_"))
    return bool(toks & set(ID_HINTS))


def infer_type(s: pd.Series, name: str, cfg: CleaningConfig) -> str:
    nn = _nonnull(s)
    if nn.empty:
        return "vacia"
    vals = nn.tolist()
    n = len(vals)
    dt_native = sum(isinstance(v, (_dt.datetime, _dt.date, pd.Timestamp)) for v in vals)
    if dt_native / n >= cfg.umbral_fecha:
        return "fecha"
    name_dateish = bool({"fecha", "date", "periodo", "inicio", "cese", "vencimiento", "emision"} & set(name.split("_")))
    if name_dateish:
        ok = sum(parse_date(v, cfg.dayfirst, allow_serial=True) is not None for v in vals)
        if ok / n >= cfg.umbral_fecha:
            return "fecha"
    strs = [v for v in vals if isinstance(v, str)]
    if strs and len(strs) / n > 0.5:
        ok = sum(parse_date(v, cfg.dayfirst) is not None for v in strs)
        if ok / len(strs) >= cfg.umbral_fecha and any(re.search(r"\d", v) for v in strs):
            # evita confundir códigos numéricos cortos con fechas
            if not all(re.fullmatch(r"\d{1,4}", v) for v in strs):
                return "fecha"
    # ids con ceros a la izquierda o largos
    leading0 = sum(isinstance(v, str) and re.fullmatch(r"0\d+", v) is not None for v in vals)
    if leading0 / n > 0.05:
        return "id"
    nums = [parse_number(v) for v in vals]
    ok = sum(x is not None for x in nums)
    if ok / n >= cfg.umbral_numerico:
        if _is_id_name(name) and all(x is not None and float(x).is_integer() for x in nums if x is not None):
            return "id"
        xs = [x for x in nums if x is not None]
        if xs and all(float(x).is_integer() for x in xs):
            if max(abs(x) for x in xs) >= 1e7 and len(set(xs)) / len(xs) > 0.9 and _is_id_name(name):
                return "id"
            return "entero"
        return "decimal"
    uniq = len(set(map(str, vals)))
    if uniq <= 60 or uniq / n <= 0.3:
        return "categoria"
    return "texto"


def _fmt_id(v) -> str | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def convert_column(s: pd.Series, name: str, tipo: str, cfg: CleaningConfig, log: ChangeLog) -> pd.Series:
    if tipo in ("vacia", "texto", "categoria"):
        return s.map(lambda v: None if v is None or (isinstance(v, float) and v != v) else str(v))
    if tipo == "id":
        return s.map(_fmt_id)
    if tipo == "fecha":
        parsed = s.map(lambda v: parse_date(v, cfg.dayfirst, allow_serial=True))
        bad = [(i, v) for i, v, p in zip(s.index, s.tolist(), parsed.tolist()) if not pd.isna(v) and pd.isna(p)]
        n_conv = sum(isinstance(v, str) for v in s.tolist())
        if n_conv:
            ex = next(((i, v, p) for i, v, p in zip(s.index, s.tolist(), parsed.tolist()) if isinstance(v, str) and not pd.isna(p)), None)
            if ex:
                log.add("fecha_estandarizada", name, n_conv, ex[0], ex[1], ex[2].strftime("%Y-%m-%d"), "Fechas en texto convertidas a ISO (AAAA-MM-DD)")
        for i, v in bad[:5]:
            log.issue(i, name, v, "fecha_invalida", "anulado", "Texto no interpretable como fecha")
        if bad:
            log.add("fecha_invalida_anulada", name, len(bad), bad[0][0], bad[0][1], "", "Valores que no son fecha válida pasaron a vacío", "aviso")
        return pd.to_datetime(pd.Series(parsed.tolist(), index=s.index), errors="coerce")
    # numérico
    parsed = s.map(parse_number)
    as_text = [(i, v) for i, v in zip(s.index, s.tolist()) if isinstance(v, str)]
    conv = [(i, v, parsed[i]) for i, v in as_text if pd.notna(parsed[i])]
    if conv:
        log.add("numero_convertido", name, len(conv), conv[0][0], conv[0][1], conv[0][2], "Números guardados como texto convertidos a numérico (separadores de miles/decimales, moneda, %)")
    bad = [(i, v) for i, v in as_text if pd.isna(parsed[i])]
    for i, v in bad[:8]:
        log.issue(i, name, v, "no_numerico", "anulado", "Texto en columna numérica")
    if bad:
        log.add("no_numerico_anulado", name, len(bad), bad[0][0], bad[0][1], "", "Texto dentro de columna numérica pasó a vacío (no se inventa dato)", "aviso")
    num = pd.to_numeric(pd.Series(parsed.tolist(), index=s.index), errors="coerce")
    if tipo == "entero":
        num = num.round().astype("Int64")
    return num


def unify_categories(s: pd.Series, name: str, cfg: CleaningConfig, log: ChangeLog) -> pd.Series:
    """Unifica variantes de mayúsculas/tildes/espacios y typos casi idénticos."""
    vals = s.dropna()
    if vals.empty:
        return s
    cnt = Counter(vals.tolist())
    groups: dict[str, list[str]] = {}
    for v in cnt:
        key = re.sub(r"[^a-z0-9]+", "", strip_accents(v).lower())
        groups.setdefault(key, []).append(v)
    mapping: dict[str, str] = {}
    for key, variants in groups.items():
        if len(variants) > 1:
            best = max(variants, key=lambda x: (cnt[x], x.isupper(), x))
            for v in variants:
                if v != best:
                    mapping[v] = best
    n_exact = sum(cnt[v] for v in mapping)
    if n_exact:
        ex = next(iter(mapping.items()))
        log.add("categoria_unificada", name, n_exact, None, ex[0], ex[1], f"{len(mapping)} variantes de escritura unificadas (mayúsculas, tildes, espacios)")
    if cfg.fuzzy_corte < 1 and len(cnt) <= 120:
        keys = sorted({mapping.get(v, v) for v in cnt}, key=lambda x: -cnt.get(x, 0))
        fuzzy: dict[str, str] = {}
        for i, a in enumerate(keys):
            if a in fuzzy:
                continue
            for b in keys[i + 1:]:
                if b in fuzzy or len(a) < 5 or len(b) < 5:
                    continue
                if re.search(r"\d", a) or re.search(r"\d", b):
                    continue
                if cnt.get(b, 0) > 0.2 * cnt.get(a, 1):
                    continue
                if difflib.SequenceMatcher(None, strip_accents(a).lower(), strip_accents(b).lower()).ratio() >= cfg.fuzzy_corte:
                    fuzzy[b] = a
        if fuzzy:
            mapping.update(fuzzy)
            mapping = {k: fuzzy.get(v, v) for k, v in mapping.items()}
            ex = next(iter(fuzzy.items()))
            log.add("categoria_typo_corregida", name, sum(cnt.get(k, 0) for k in fuzzy), None, ex[0], ex[1], f"{len(fuzzy)} posibles errores de digitación unificados (similitud ≥ {cfg.fuzzy_corte})", "aviso")
    return s.map(lambda v: mapping.get(v, v) if isinstance(v, str) else v) if mapping else s
