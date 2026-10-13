"""Bitácora de trazabilidad: cada corrección y cada anomalía quedan registradas."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

LOG_COLS = ["tabla", "regla", "columna", "celdas", "fila_ejemplo", "antes", "despues", "detalle", "severidad"]
ISSUE_COLS = ["tabla", "fila_origen", "columna", "valor", "regla", "accion", "detalle"]


def _short(v, n=80):
    if v is None:
        return ""
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class ChangeLog:
    tabla: str
    records: list = field(default_factory=list)
    issues: list = field(default_factory=list)

    def add(self, regla, columna="", celdas=1, fila=None, antes=None, despues=None, detalle="", severidad="info"):
        self.records.append(
            {
                "tabla": self.tabla, "regla": regla, "columna": columna, "celdas": int(celdas),
                "fila_ejemplo": (int(fila) + 1) if fila is not None else "",
                "antes": _short(antes), "despues": _short(despues),
                "detalle": detalle, "severidad": severidad,
            }
        )

    def issue(self, fila, columna, valor, regla, accion, detalle=""):
        self.issues.append(
            {
                "tabla": self.tabla, "fila_origen": (int(fila) + 1) if fila is not None else "",
                "columna": columna, "valor": _short(valor), "regla": regla,
                "accion": accion, "detalle": detalle,
            }
        )

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.records, columns=LOG_COLS)

    def issues_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.issues, columns=ISSUE_COLS)
