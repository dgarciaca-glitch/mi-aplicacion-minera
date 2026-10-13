"""Datos personales (DNI, nombres, teléfonos…): detección por nombre y por patrón de valores."""
from __future__ import annotations

import hashlib
import re

import pandas as pd

from .changelog import ChangeLog
from .config import PII_NAME_EXCLUDE, PII_NAME_TOKENS, CleaningConfig
from .normalize import norm_name

_DNI = re.compile(r"^\d{8}$")
_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")
_PHONE = re.compile(r"^(\+?51)?9\d{8}$")
_FULLNAME = re.compile(r"^[A-ZÁÉÍÓÚÑ]{2,}(\s+[A-ZÁÉÍÓÚÑ]{2,}){2,4}$")


def detect_pii_columns(df: pd.DataFrame, clean_names: list[str]) -> dict[str, str]:
    """Devuelve {nombre_limpio: motivo}."""
    found: dict[str, str] = {}
    for j, name in enumerate(clean_names):
        toks = set(name.split("_"))
        col = df.iloc[:, j]
        vals = [str(v).strip() for v in col.dropna().tolist()][:400]
        if not vals:
            continue
        by_name = bool(toks & PII_NAME_TOKENS) and not (toks & PII_NAME_EXCLUDE)
        if "presentado" in toks or name.startswith("nombre_y_apellido"):
            by_name = True
        if "fecha" in toks and "nacimiento" in toks:
            found[name] = "fecha de nacimiento"
            continue
        if by_name:
            found[name] = "nombre de columna"
            continue
        n = len(vals)
        if n < 20:
            continue  # los patrones de valor solo son fiables con suficientes filas
        v_clean = [re.sub(r"[\s.-]", "", v) for v in vals]
        if sum(bool(_EMAIL.match(v)) for v in vals) / n > 0.5:
            found[name] = "patrón correo electrónico"
        elif sum(bool(_PHONE.match(v)) for v in v_clean) / n > 0.6:
            found[name] = "patrón teléfono móvil"
        elif sum(bool(_FULLNAME.match(v)) for v in vals) / n > 0.7 and len(set(vals)) / n > 0.8:
            found[name] = "patrón nombre completo"
    return found


def hash_value(v, salt: str) -> str:
    return "P_" + hashlib.sha256(f"{salt}|{v}".encode()).hexdigest()[:10]


def age_band(birth: pd.Series, ref: pd.Timestamp) -> pd.Series:
    years = ((ref - birth).dt.days / 365.25)
    bins = [0, 18, 25, 35, 45, 55, 65, 200]
    labels = ["<18", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
    return pd.cut(years, bins=bins, labels=labels, right=False).astype(object)


def apply_pii(df: pd.DataFrame, pii: dict[str, str], cfg: CleaningConfig, log: ChangeLog) -> pd.DataFrame:
    if not pii or cfg.pii_modo == "conservar":
        if pii:
            log.add("pii_conservado", celdas=len(pii), detalle="PII detectado pero conservado por configuración: " + ", ".join(pii), severidad="alerta")
        return df
    out = df.copy()
    for col, why in pii.items():
        if col not in out.columns:
            continue
        if why == "fecha de nacimiento" and cfg.pii_modo != "conservar":
            dt = pd.to_datetime(out[col], errors="coerce")
            if dt.notna().any():
                ref = dt.max().normalize() if False else pd.Timestamp.today().normalize()
                out["rango_edad"] = age_band(dt, ref)
                log.add("pii_generalizado", col, int(dt.notna().sum()), detalle="Fecha de nacimiento sustituida por rango de edad (minimización de datos)")
            out = out.drop(columns=[col])
            continue
        if cfg.pii_modo == "anonimizar":
            out[col] = out[col].map(lambda v: hash_value(v, cfg.pii_sal) if pd.notna(v) else v)
            log.add("pii_anonimizado", col, int(out[col].notna().sum()), detalle=f"Pseudonimizado con hash SHA-256 salado ({why})")
        else:
            log.add("pii_excluido", col, int(out[col].notna().sum()), detalle=f"Columna excluida del CSV de salida ({why})", severidad="alerta")
            out = out.drop(columns=[col])
    return out
