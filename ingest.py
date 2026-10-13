"""Lectura robusta de CSV / Excel / ZIP / carpetas → tablas crudas (sin encabezado)."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

SUPPORTED = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls")


@dataclass
class RawTable:
    source: str
    sheet: str
    df: pd.DataFrame  # sin encabezado, dtype object, índice = posición de fila original

    @property
    def table_id(self) -> str:
        stem = Path(self.source).stem
        return f"{stem}::{self.sheet}" if self.sheet else stem


def fix_zip_name(name: str) -> str:
    """Nombres de ZIP sin bandera UTF-8 llegan como '#U00d1' → 'Ñ'."""
    return re.sub(r"#U([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), name)


def decode_bytes(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1*"


def sniff_delimiter(text: str) -> str:
    lines = [ln for ln in text.splitlines()[:40] if ln.strip()]
    best, score = ",", -1
    for d in (",", ";", "\t", "|"):
        counts = [ln.count(d) for ln in lines]
        if not counts or max(counts) == 0:
            continue
        mode = max(set(counts), key=counts.count)
        sc = mode * counts.count(mode)
        if sc > score:
            best, score = d, sc
    return best


def read_csv_bytes(data: bytes, name: str = "") -> tuple[pd.DataFrame, list[str]]:
    notes = []
    text, enc = decode_bytes(data)
    if enc not in ("utf-8-sig", "utf-8"):
        notes.append(f"Codificación detectada: {enc}")
    delim = sniff_delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    width = max((len(r) for r in rows), default=0)
    ragged = sum(1 for r in rows if 0 < len(r) != width)
    if ragged:
        notes.append(f"{ragged} filas con número de columnas distinto; se completaron con vacío")
    rows = [r + [None] * (width - len(r)) for r in rows]
    df = pd.DataFrame(rows, dtype=object)
    return df, notes


def _read_excel(data: bytes, name: str) -> dict[str, pd.DataFrame]:
    return pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=object)


def load_bytes(name: str, data: bytes) -> tuple[list[RawTable], list[str]]:
    ext = Path(name).suffix.lower()
    tables: list[RawTable] = []
    warns: list[str] = []
    try:
        if ext in (".csv", ".tsv", ".txt"):
            df, notes = read_csv_bytes(data, name)
            warns += [f"{name}: {n}" for n in notes]
            tables.append(RawTable(name, "", df.astype(object)))
        elif ext in (".xlsx", ".xlsm", ".xls"):
            for sheet, df in _read_excel(data, name).items():
                if df.dropna(how="all").empty:
                    warns.append(f"{name} › {sheet}: hoja vacía, omitida")
                    continue
                tables.append(RawTable(name, str(sheet).strip(), df.astype(object)))
        elif ext == ".zip":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if info.is_dir() or info.filename.startswith("__MACOSX"):
                        continue
                    if Path(info.filename).suffix.lower() in SUPPORTED + (".zip",):
                        sub, w = load_bytes(fix_zip_name(info.filename), z.read(info))
                        tables += sub
                        warns += w
                    else:
                        warns.append(f"{info.filename}: formato no soportado (solo CSV/Excel), omitido")
        else:
            warns.append(f"{name}: formato '{ext}' no soportado")
    except Exception as exc:  # noqa: BLE001 - se reporta al usuario
        warns.append(f"{name}: no se pudo leer ({type(exc).__name__}: {exc})")
    return tables, warns


def load_inputs(items: list[tuple[str, bytes]]) -> tuple[list[RawTable], list[str]]:
    tables, warns = [], []
    for name, data in items:
        t, w = load_bytes(name, data)
        tables += t
        warns += w
    return tables, warns


def load_folder(path: str) -> tuple[list[RawTable], list[str]]:
    p = Path(path.strip().strip('"'))
    if not p.exists() or not p.is_dir():
        return [], [f"La carpeta '{path}' no existe"]
    items = []
    for f in sorted(p.rglob("*")):
        if f.is_file() and f.suffix.lower() in SUPPORTED + (".zip",) and not f.name.startswith("~$"):
            items.append((str(f.relative_to(p)), f.read_bytes()))
    if not items:
        return [], [f"No hay CSV/Excel/ZIP en '{path}'"]
    return load_inputs(items)
