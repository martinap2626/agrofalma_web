"""Parser de los Excel de Sede Pimentel (2023-2026) -> ventas largas (producto, anio, mes, unidades)."""
from __future__ import annotations
import re, unicodedata
from pathlib import Path
import openpyxl, pandas as pd

RAIZ = Path(__file__).resolve().parents[3]
CARPETA = RAIZ / "datos_empresa" / "Sede_Pimentel"
SALIDA = Path(__file__).resolve().parent / "datos"
MESES = ["ENERO","FEBRERO","MARZO","ABRIL","MAYO","JUNIO","JULIO","AGOST","SEPT","SEPTIEMBRE","AGOSTO","OCT","NOV","DICIEMBRE"]
MES_NUM = {"ENERO":1,"FEBRERO":2,"MARZO":3,"ABRIL":4,"MAYO":5,"JUNIO":6,"JULIO":7,"AGOST":8,"AGOSTO":8,
           "SEPT":9,"SEPTIEMBRE":9,"OCT":10,"OCTUBRE":10,"NOV":11,"NOVIEMBRE":11,"DICIEMBRE":12}

def meses_de_hoja(nombre: str) -> list[int]:
    n = nombre.upper()
    if "CREDITO" in n or "FERTILIZANTES" in n:
        return []
    return [m for k, m in MES_NUM.items() if re.search(rf"\b{k}", n)] and sorted({m for k, m in MES_NUM.items() if re.search(rf"\b{k}", n)})

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()
    return re.sub(r"\s+", " ", s).strip()

def bloques(header: list) -> list[dict]:
    h = [norm(c) if c is not None else "" for c in header]
    out = []
    for p, c in enumerate(h):
        if c != "PRODUCTO":
            continue
        cols = []
        j = p - 1
        while j >= 0 and h[j] in ("CONTADO", "CREDITO", "CANTIDAD", "TODO ENTREGADO"):
            if h[j] != "TODO ENTREGADO":
                cols.append((j, h[j]))
            j -= 1
        out.append({"prod": p, "cols": cols})
    return out

def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0

def cargar() -> pd.DataFrame:
    filas = []
    for f in sorted(CARPETA.glob("ventas_*.xlsx")):
        anio = int(re.search(r"(\d{4})", f.name).group(1))
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        for ws in wb.worksheets:
            meses = meses_de_hoja(ws.title)
            if not meses:
                continue
            vendedor = "JORGE" if "JORGE" in ws.title.upper() else "ALCANTARA"
            rows = list(ws.iter_rows(values_only=True))
            bl = bloques(list(rows[0]))
            for bi, b in enumerate(bl, start=1):
                for r in rows[1:]:
                    nom = r[b["prod"]] if b["prod"] < len(r) else None
                    if not isinstance(nom, str) or not nom.strip():
                        continue
                    for (j, tipo) in b["cols"]:
                        v = num(r[j]) if j < len(r) else 0.0
                        if v == 0:
                            continue
                        for m in meses:
                            filas.append((anio, m, vendedor, ws.title, bi, len(bl), nom.strip(), tipo, v / len(meses), num(r[b["prod"] + 1]) if b["prod"] + 1 < len(r) else 0.0))
    df = pd.DataFrame(filas, columns=["anio","mes","vendedor","hoja","bloque","n_bloques","producto","tipo","cantidad","precio"])
    df["producto_norm"] = df["producto"].map(norm)
    return df

if __name__ == "__main__":
    SALIDA.mkdir(exist_ok=True)
    df = cargar()
    df.to_csv(SALIDA / "ventas_largas.csv", index=False)
    print(df.shape)
    print(df.groupby(["anio","tipo"])["cantidad"].agg(["count","sum","min"]))
    print(df.groupby(["anio","mes"])["cantidad"].sum().unstack().round(0))
    print(df[df.bloque==4].producto_norm.value_counts().head(30))
