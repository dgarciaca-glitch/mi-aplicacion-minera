"""Configuración y diccionarios del dominio minero (La Arena)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

NULL_TOKENS = frozenset(
    {
        "", "-", "--", "---", "—", "–", "n/a", "na", "n.a.", "n.d.", "nd", "s/d", "sd",
        "s/i", "nulo", "null", "none", "nan", "nat", "#n/a", "#ref!", "#div/0!",
        "#value!", "#name?", "#num!", "#null!", "#¡ref!", "#¡valor!", "#¡div/0!",
    }
)
EXCEL_ERRORS = frozenset(t for t in NULL_TOKENS if t.startswith("#"))

MESES = {
    "ene": 1, "enero": 1, "jan": 1, "january": 1,
    "feb": 2, "febrero": 2, "february": 2,
    "mar": 3, "marzo": 3, "march": 3,
    "abr": 4, "abril": 4, "apr": 4, "april": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6, "june": 6,
    "jul": 7, "julio": 7, "july": 7,
    "ago": 8, "agosto": 8, "aug": 8, "august": 8,
    "set": 9, "sep": 9, "sept": 9, "setiembre": 9, "septiembre": 9, "september": 9,
    "oct": 10, "octubre": 10, "october": 10,
    "nov": 11, "noviembre": 11, "november": 11,
    "dic": 12, "diciembre": 12, "dec": 12, "december": 12,
}

# Traducción/estandarización de términos técnicos (inglés → español de planeamiento minero)
TERM_MAP = {
    "ore": "mineral", "waste": "desmonte", "tonnes": "toneladas", "tonne": "toneladas",
    "tn": "toneladas", "tm": "toneladas", "tons": "toneladas", "grade": "ley",
    "grades": "ley", "gr": "g", "hrs": "horas", "hr": "horas", "hours": "horas",
    "shovel": "pala", "truck": "camion", "loader": "cargador", "pit": "tajo",
    "bench": "banco", "fase": "fase", "phase": "fase", "stripping": "stripping",
    "dia": "dia", "desc": "descripcion", "und": "unidad", "unid": "unidad",
    "cant": "cantidad", "nro": "numero", "n": "n", "cod": "codigo",
}

# Rutas de columnas que deben conservarse como texto (identificadores)
ID_HINTS = (
    "codigo", "cod", "id", "item", "dni", "ruc", "partida", "frente", "banco",
    "bench", "nro", "numero", "telefono", "celular", "placa", "ot", "serie",
)

UNIT_PATTERNS = [
    (r"g/t|gr/t|g_t", "g/t"), (r"\bppm\b", "ppm"), (r"us\$|usd|dolar", "USD"),
    (r"\bs/\.?|\bpen\b|soles", "PEN"), (r"m3|m³", "m³"), (r"\bkm/h\b", "km/h"),
    (r"toneladas|tonnes?|\btn\b|\btm\b|\bton\b", "t"), (r"\bkg\b", "kg"),
    (r"horas|\bhrs?\b", "h"), (r"\bmin\b", "min"), (r"precipitaci", "mm"),
    (r"%|porcentaje|ratio", "%"),
]

# Reglas de dominio: (patrón de nombre de columna limpio, mínimo, máximo, acción)
# acción: "anular" = pasa a vacío y se registra; "marcar" = solo se reporta
DOMAIN_RULES = [
    (r"(^|_)(au|ag)(_|$)|ley_(au|ag)", 0, 100, "anular", "Ley Au/Ag fuera de 0–100 g/t"),
    (r"(^|_)cu(_|$)|(^|_)mo(_|$)", 0, 1_000_000, "anular", "Ley Cu/Mo fuera de 0–1 000 000 ppm"),
    (r"precipitacion", 0, 3000, "anular", "Precipitación negativa o fuera de rango"),
    (r"(^|_)horas?(_|$)", 0, 8800, "anular", "Horas negativas o > 8 800 (año)"),
    (r"toneladas|mineral|desmonte|tonelaje", 0, None, "anular", "Tonelaje negativo"),
    (r"porcentaje|cumplim|recuperacion", 0, 100, "marcar", "Porcentaje fuera de 0–100"),
    (r"edad", 14, 80, "marcar", "Edad laboral fuera de 14–80"),
    (r"stock|saldo|consumo|cantidad", 0, None, "marcar", "Cantidad/stock negativo"),
]

PII_NAME_TOKENS = {
    "dni", "nombre", "nombres", "apellido", "apellidos", "telefono", "celular",
    "correo", "email", "direccion", "domicilio", "presentado", "licencia", "carnet",
    "conyuge", "hijos", "familiar", "documento", "telefonos", "celulares", "sanguineo",
    "operador", "operadores", "conductor", "responsable", "supervisor", "trabajador", "solicitante", "firma",
}
PII_NAME_EXCLUDE = {
    "puesto", "area", "producto", "equipo", "proyecto", "archivo", "hoja", "descripcion",
    "categoria", "cargo", "unidad", "empresa", "zona", "lugar", "partida", "recurso",
}


@dataclass
class CleaningConfig:
    pii_modo: str = "excluir"          # excluir | anonimizar | conservar
    pii_sal: str = "la-arena-2026"
    umbral_numerico: float = 0.85
    umbral_fecha: float = 0.85
    dayfirst: bool = True
    dividir_bloques: bool = True
    quitar_duplicados: bool = True
    quitar_columnas_vacias: bool = True
    umbral_columna_vacia: float = 1.0
    quitar_totales: bool = True
    unificar_categorias: bool = True
    fuzzy_corte: float = 0.93
    anular_imposibles: bool = True
    detectar_atipicos: bool = True
    z_atipico: float = 8.0
    traducir_terminos: bool = True
    ancho_a_largo: bool = True
    ordenar_columnas: bool = True
    min_filas_bloque: int = 5

    def to_dict(self) -> dict:
        return asdict(self)
