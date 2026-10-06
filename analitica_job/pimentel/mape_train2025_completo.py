"""Train ene2023-dic2025, test ene-jun2026 (campaña grande). MAPE total, por categoría y clase ABC."""
import numpy as np, pandas as pd, warnings
from sklearn.ensemble import RandomForestRegressor, HistGradientBoostingRegressor, ExtraTreesRegressor
import entrenar_pimentel as E
from cargar_pimentel import SALIDA, cargar
warnings.filterwarnings("ignore")
CORTE = E.IDX(2025, 12)
CAT = {1: "Agroquímicos", 2: "Foliares/bioestimulantes", 3: "Fertilizantes"}
d = cargar(); d = d[(d.bloque < 4) & ~((d.anio == 2026) & (d.mes > 6))]; d["idx"] = E.IDX(d.anio, d.mes)
ing = d[(d.idx > CORTE - 12) & (d.idx <= CORTE)].assign(s=lambda x: x.cantidad * x.precio).groupby("producto_norm").s.sum().sort_values(ascending=False)
ing = ing[ing > 0]; cum = ing.cumsum() / ing.sum(); prev = cum.shift(fill_value=0)
abc = pd.Series(np.where(prev < .8, "A", np.where(prev < .95, "B", "C")), index=ing.index)
p = E.features(E.panel())
conoc = set(p[(p.idx <= CORTE) & (p.y > 0)].producto_norm); p = p[p.producto_norm.isin(conoc)].copy()
pa = E.panel(); t = pa[pa.idx > CORTE]
print(f"Productos train: {len(conoc)}; cobertura unidades test: {t[t.producto_norm.isin(conoc)].y.sum()/t.y.sum():.1%}")
p["categoria"] = p.bloque.map(CAT); p["abc"] = p.producto_norm.map(abc).fillna("C")
tr = p[p.idx <= CORTE]; te = p[p.idx > CORTE].copy()
def mk(k):
    return {"RF": RandomForestRegressor(400, min_samples_leaf=2, random_state=0, n_jobs=-1),
            "Extra Trees": ExtraTreesRegressor(400, min_samples_leaf=2, random_state=0, n_jobs=-1),
            "HGB": HistGradientBoostingRegressor(loss="poisson", max_iter=300, learning_rate=0.05, max_depth=4, random_state=0)}[k]
B = ["lag12", "lag13", "lag14", "lag24", "sin", "cos", "bloque", "mes"]
A = ["lag1", "lag2", "lag3", "lag12", "mm3", "mm12", "est12", "sin", "cos", "bloque"]
pred = {"Estacional ingenuo": te.lag12.fillna(0), "Ingenuo mes anterior": te.lag1.fillna(0)}
for tag, cols in [("12m", B), ("1paso", A)]:
    t2 = tr.dropna(subset=[c for c in cols if c != "lag24"]) if tag == "12m" else tr.dropna(subset=cols)
    for k in ("RF", "Extra Trees", "HGB"):
        pred[f"{k} [{tag}]"] = pd.Series(mk(k).fit(t2[cols].fillna(0) if tag == "12m" else t2[cols], t2.y).predict(te[cols].fillna(0)), index=te.index)
def fila(sub, f):
    y = sub.y.values; f = f.loc[sub.index].clip(lower=0).values; pos = y > 0
    ag = pd.DataFrame({"p": sub.producto_norm.values, "y": y, "f": f}).groupby("p").sum(); ag = ag[ag.y > 0]
    tm = pd.DataFrame({"i": sub.idx.values, "y": y, "f": f}).groupby("i").sum()
    return (round(np.mean(abs(y[pos]-f[pos])/y[pos])*100,1), round(abs(y-f).sum()/y.sum()*100,1),
            round((abs(ag.y-ag.f)/ag.y).mean()*100,1), round((abs(tm.y-tm.f)/tm.y).mean()*100,1), len(ag))
rows = []
for st, col in [("Total", None), ("Categoría", "categoria"), ("Clase ABC", "abc")]:
    for g, sub in ([("Todos", te)] if col is None else te.groupby(col)):
        for mn, f in pred.items(): rows.append((st, g, mn) + fila(sub, f))
r = pd.DataFrame(rows, columns=["segmento", "grupo", "modelo", "MAPE_fila_%", "WAPE_%", "MAPE_prod_agregado_%", "MAPE_total_mensual_%", "n_productos"])
r.to_csv(SALIDA / "mape_train2023_2025_test_ene_jun_2026.csv", index=False)
pd.set_option("display.width", 250, "display.max_rows", 300)
print("ABC:", abc.value_counts().to_dict())
for m in ["MAPE_fila_%", "WAPE_%", "MAPE_prod_agregado_%", "MAPE_total_mensual_%"]:
    print("\n--", m); print(r.pivot_table(index=["segmento", "grupo"], columns="modelo", values=m, sort=False).round(1).to_string())

# ---------------- MASE + Excel de real vs predicho ----------------
esc = tr.groupby("producto_norm").y.apply(lambda s: np.abs(np.diff(s.values)).mean())
MODELOS = ["Estacional ingenuo", "RF [12m]", "Extra Trees [12m]", "HGB [12m]", "HGB [1paso]"]
def mase(sub, f):
    sc = sub.producto_norm.map(esc); v = sc > 0
    return round((np.abs(sub.y - f.loc[sub.index].clip(lower=0))[v] / sc[v]).mean(), 3)
met = []
for st, col in [("Total", None), ("Categoría", "categoria"), ("Clase ABC", "abc")]:
    for g, sub in ([("Todos", te)] if col is None else te.groupby(col)):
        for mn in MODELOS:
            m, w, ag, tot, n = fila(sub, pred[mn])
            met.append((st, g, mn, n, mase(sub, pred[mn]), w, m, ag, tot))
met = pd.DataFrame(met, columns=["Segmento", "Grupo", "Modelo", "N productos", "MASE", "WAPE %", "MAPE fila %", "MAPE por producto (campaña) %", "MAPE venta total mensual %"])
pd.set_option("display.width", 250, "display.max_rows", 300)
print("\n===== MASE y WAPE ====="); print(met.pivot_table(index=["Segmento", "Grupo"], columns="Modelo", values="MASE", sort=False).round(2).to_string())
print(met.pivot_table(index=["Segmento", "Grupo"], columns="Modelo", values="WAPE %", sort=False).round(1).to_string())

MES = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun"}
det = te[["producto_norm", "categoria", "abc", "mes", "y"]].copy()
det["mes"] = det.mes.map(MES); det = det.rename(columns={"producto_norm": "Producto", "categoria": "Categoría", "abc": "Clase ABC", "mes": "Mes 2026", "y": "REAL vendido (unid.)"})
for mn in MODELOS:
    f = pred[mn].clip(lower=0).round(1)
    det[f"PRED {mn}"] = f
    det[f"Error abs {mn}"] = (det["REAL vendido (unid.)"] - f).abs().round(1)
det["REAL vendido (unid.)"] = det["REAL vendido (unid.)"].round(1)
det = det.sort_values(["Clase ABC", "Producto"], kind="stable")
orden_mes = {v: k for k, v in MES.items()}
det = det.assign(_m=det["Mes 2026"].map(orden_mes)).sort_values(["Clase ABC", "Producto", "_m"]).drop(columns="_m")

agp = det.groupby(["Producto", "Categoría", "Clase ABC"], as_index=False).sum(numeric_only=True)
for mn in MODELOS:  # error % sobre el real de la campaña
    real = agp["REAL vendido (unid.)"]
    agp[f"Error % {mn}"] = ((real - agp[f"PRED {mn}"]).abs() / real.where(real > 0) * 100).round(1)
    agp = agp.drop(columns=f"Error abs {mn}")
agp = agp.sort_values(["Clase ABC", "REAL vendido (unid.)"], ascending=[True, False])

mensual = te.assign(Mes=te.mes.map(MES)).groupby(["idx", "Mes"]).agg(real=("y", "sum")).reset_index()
for mn in MODELOS: mensual[f"PRED {mn}"] = pred[mn].clip(lower=0).groupby(te.idx).sum().values.round(1)
mensual = mensual.drop(columns="idx").rename(columns={"real": "REAL vendido (unid.)"}); mensual["REAL vendido (unid.)"] = mensual["REAL vendido (unid.)"].round(1)

notas = pd.DataFrame({"Notas": [
    "Entrenamiento: ene-2023 a dic-2025 (Sede Pimentel). Prueba: ene-jun 2026 (campaña grande). Unidades = contado + cantidad de fertilizantes; sin alimento animal ni TODO ENTREGADO.",
    "REAL = lo registrado en los Excel de la sede. PRED = lo que predice el modelo con información hasta dic-2025 ([12m] = sin usar el mes anterior real; [1paso] = usa el mes anterior real).",
    "Categoría = bloque de columnas del Excel (aprox.). Clase ABC = por ingresos S/ de 2025 (A hasta 80% acumulado, B hasta 95%, C resto).",
    "La hoja 'ING ALCANTARA - MARZO y ABRIL' viene junta: se repartió 50/50 entre marzo y abril (por eso hay decimales).",
    "MASE < 1 = mejor que repetir el mes anterior (escala calculada en entrenamiento). WAPE = suma de errores / suma de ventas reales.",
    "Productos nuevos de 2026 (sin ventas en el entrenamiento) no están: el modelo no tiene qué predecirles (12% de las unidades).",
]})
ruta = SALIDA / "Pimentel_real_vs_predicho_ene_jun_2026.xlsx"
with pd.ExcelWriter(ruta, engine="openpyxl") as xw:
    notas.to_excel(xw, sheet_name="Notas", index=False)
    met.to_excel(xw, sheet_name="Metricas (MASE-WAPE-MAPE)", index=False)
    mensual.to_excel(xw, sheet_name="Real vs pred por mes", index=False)
    agp.to_excel(xw, sheet_name="Por producto (campaña)", index=False)
    det.to_excel(xw, sheet_name="Detalle producto-mes", index=False)
    for ws in xw.book.worksheets:
        ws.freeze_panes = "A2"
        for c in ws[1]:
            c.font = c.font.copy(bold=True)
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(60 if ws.title == "Notas" else 24, max(len(str(col[0].value)) + 2, 12) if ws.title != "Notas" else 150)
print("Excel:", ruta)
