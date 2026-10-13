"""Normalización de nombres, texto, números y fechas."""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata

import pandas as pd

from .config import MESES, NULL_TOKENS, TERM_MAP, UNIT_PATTERNS

_WS = re.compile(r"\s+")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def fix_mojibake(s: str) -> str:
    if "Ã" in s or "Â" in s or "â€" in s:
        try:
            fixed = s.encode("cp1252", errors="strict").decode("utf-8")
            return fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            try:
                return s.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                return s
    return s


def clean_text_value(v):
    """Limpia un valor de texto. Devuelve None si equivale a nulo."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    if not isinstance(v, str):
        return v
    s = v.translate(_ZERO_WIDTH).replace("\u00a0", " ")
    s = fix_mojibake(s)
    s = unicodedata.normalize("NFC", s)
    s = _WS.sub(" ", s).strip()
    if s.lower() in NULL_TOKENS:
        return None
    return s


# ---------------------------------------------------------------- nombres
_MONTH_YEAR = re.compile(r"^([a-zñ]+)[\s_\-./]*(\d{4}|\d{2})$", re.I)


def parse_month_year(token) -> tuple[int, int] | None:
    """'Jan_2012' / 'Enero 2012' / 'Ene-12' → (2012, 1)."""
    if isinstance(token, (_dt.datetime, pd.Timestamp)):
        return (token.year, token.month)
    if not isinstance(token, str):
        return None
    t = strip_accents(token.strip().lower())
    m = _MONTH_YEAR.match(t)
    if not m or m.group(1) not in MESES:
        return None
    y = int(m.group(2))
    if y < 100:
        y += 2000
    return (y, MESES[m.group(1)])


def norm_name(name, translate: bool = True) -> str:
    """Nombre de columna → snake_case sin tildes, con términos estandarizados."""
    if isinstance(name, (_dt.datetime, pd.Timestamp)):
        return f"{name.year}_{name.month:02d}"
    if isinstance(name, float) and name == int(name):
        name = int(name)
    s = str(name).strip()
    parts = re.split(r"[_\s]+", s, maxsplit=2)
    my = parse_month_year("_".join(parts[:2])) if len(parts) >= 2 else None
    prefix = ""
    if my:
        prefix = f"{my[0]}_{my[1]:02d}"
        s = parts[2] if len(parts) > 2 else ""
    s = strip_accents(s).lower()
    s = s.replace("us$", "usd ").replace("%", " pct ").replace("$", " usd ").replace("/", "_")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    toks = [t for t in s.split("_") if t]
    if translate:
        toks = [TERM_MAP.get(t, t) for t in toks]
    out = "_".join(([prefix] if prefix else []) + toks)
    out = re.sub(r"_+", "_", out).strip("_")
    return out or "sin_nombre"


def dedupe_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}_{seen[n]}")
        else:
            seen[n] = 1
            out.append(n)
    return out


def guess_unit(original_name: str) -> str:
    s = strip_accents(str(original_name).lower())
    for pat, unit in UNIT_PATTERNS:
        if re.search(pat, s):
            return unit
    return ""


# ---------------------------------------------------------------- números
_CURRENCY = re.compile(r"(us\$|usd|s/\.?|pen|\$|€|soles|dolares)", re.I)
_UNIT_TAIL = re.compile(r"\s*(kg|tm|tn|t|m3|m|hrs?|h|g/t|gr/t|ppm|km/h|min|mm|%)$", re.I)


def parse_number(v):
    """Convierte texto numérico peruano/inglés a float. None si no es número."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and v != v) else float(v)
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    s = _CURRENCY.sub("", s).strip()
    s = _UNIT_TAIL.sub("", s).strip()
    s = s.replace(" ", "")
    if s.startswith("-"):
        neg, s = True, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if not s or not re.fullmatch(r"[0-9.,]+", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
        else:
            return None
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


# ---------------------------------------------------------------- fechas
_ISO = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?$")
_DMY = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})(?:[ T].*)?$")


def _mk(y, m, d):
    try:
        return pd.Timestamp(_dt.datetime(y, m, d))
    except ValueError:
        return None


def parse_date(v, dayfirst: bool = True, allow_serial: bool = False):
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.normalize()
    if isinstance(v, _dt.datetime):
        return pd.Timestamp(v).normalize()
    if isinstance(v, _dt.date):
        return pd.Timestamp(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if allow_serial and 20000 < float(v) < 65000:
            return (pd.Timestamp("1899-12-30") + pd.to_timedelta(float(v), unit="D")).normalize()
        return None
    if not isinstance(v, str):
        return None
    s = v.strip()
    m = _ISO.match(s)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DMY.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000 if y < 70 else 1900
        d, mo = (a, b) if dayfirst else (b, a)
        r = _mk(y, mo, d)
        return r if r is not None else _mk(y, d, mo)  # intercambia si inválido
    my = parse_month_year(s)
    if my:
        return _mk(my[0], my[1], 1)
    return None
