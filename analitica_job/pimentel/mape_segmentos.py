"""MAPE de Sede Pimentel por campaña, categoría y clase ABC (escenario 12 meses desde jun-2025)."""
import numpy as np, pandas as pd, warnings
from sklearn.ensemble import RandomForestRegressor, HistGradientBoostingRegressor, ExtraTreesRegressor
import entrenar_pimentel as E
from cargar_pimentel import SALIDA, cargar
warnings.filterwarnings("ignore")
CAT = {1: "Agroquímicos", 2: "Foliares/bioestimulantes", 3: "Fertilizantes"}

d = cargar(); d = d[(d.bloque < 4) & ~((d.anio == 2026) & (d.mes > 6))]
d["idx"] = E.IDX(d.anio, d.mes)
# ABC por ingresos (S/) de los últimos 12 meses de entrenamiento (jul-2024..jun-2025)
ing = d[(d.idx > E.CORTE - 12) & (d.idx <= E.CORTE)].assign(s=lambda x: x.cantidad * x.precio).groupby("producto_norm").s.sum().sort_values(ascending=False)
ing = ing[ing > 0]; cum = ing.cumsum() / ing.sum()
abc = pd.Series(np.where(cum.shift(fill_value=0) < 0.8, "A", np.where(cum.shift(fill_value=0) < 0.95, "B", "C")), index=ing.index)

p = E.features(E.panel())
conocidos = set(p[(p.idx <= E.CORTE) & (p.y > 0)].producto_norm); p = p[p.producto_norm.isin(conocidos)].copy()
p["categoria"] = p.bloque.map(CAT); p["abc"] = p.producto_norm.map(abc).fillna("C")
tr = p[p.idx <= E.CORTE]; te = p[p.idx > E.CORTE].copy()
cols = ["lag12", "lag13", "lag14", "sin", "cos", "bloque", "mes"]
t2 = tr.dropna(subset=cols)
mods = {"RF": RandomForestRegressor(400, min_samples_leaf=2, random_state=0, n_jobs=-1),
        "Extra Trees": ExtraTreesRegressor(400, min_samples_leaf=2, random_state=0, n_jobs=-1),
        "HGB": HistGradientBoostingRegressor(loss="poisson", max_iter=300, learning_rate=0.05, max_depth=4, random_state=0)}
pred = {"Estacional ingenuo": te.lag12.fillna(0)}
for k, m in mods.items():
    pred[k] = pd.Series(m.fit(t2[cols], t2.y).predict(te[cols].fillna(0)), index=te.index)
pred["Ensamble RF+HGB"] = (pred["RF"] + pred["HGB"]) / 2

camp = {"Campaña chica 2025 (jul-dic)": te.idx <= 36, "Campaña grande 2026 (ene-jun)": te.idx > 36}
def fila(sub, f):
    y = sub.y.values; f = f.loc[sub.index].clip(lower=0).values; pos = y > 0
    mape = np.mean(np.abs(y[pos] - f[pos]) / y[pos]) * 100
    wape = np.abs(y - f).sum() / y.sum() * 100
    ag = pd.DataFrame({"p": sub.producto_norm.values, "y": y, "f": f}).groupby("p").sum(); ag = ag[ag.y > 0]
    mag = (np.abs(ag.y - ag.f) / ag.y).mean() * 100
    return round(mape, 1), round(wape, 1), round(mag, 1), len(ag)
rows = []
for cn, cm in camp.items():
    for seg_tipo, seg_col in [("Campaña (todo)", None), ("Categoría", "categoria"), ("Clase ABC", "abc")]:
        grupos = [("Todos", te[cm])] if seg_col is None else [(g, s[cm.loc[s.index]]) for g, s in te.groupby(seg_col)]
        for g, sub in grupos:
            if len(sub) == 0 or sub.y.sum() == 0: continue
            for mn, f in pred.items():
                rows.append((cn, seg_tipo, g, mn) + fila(sub, f))
r = pd.DataFrame(rows, columns=["campaña", "segmento", "grupo", "modelo", "MAPE_fila_%", "WAPE_%", "MAPE_prod_agregado_%", "n_productos"])
r.to_csv(SALIDA / "mape_por_campana_categoria_abc.csv", index=False)
pd.set_option("display.width", 250, "display.max_rows", 300)
print("ABC:", abc.value_counts().to_dict(), "| categorias:", p.drop_duplicates("producto_norm").categoria.value_counts().to_dict())
for cn in camp:
    print("\n=====", cn)
    print(r[r.campaña == cn].pivot_table(index=["segmento", "grupo"], columns="modelo", values="MAPE_prod_agregado_%").round(1).to_string())
    print("-- MAPE fila"); print(r[r.campaña == cn].pivot_table(index=["segmento", "grupo"], columns="modelo", values="MAPE_fila_%").round(1).to_string())
    print("-- WAPE"); print(r[r.campaña == cn].pivot_table(index=["segmento", "grupo"], columns="modelo", values="WAPE_%").round(1).to_string())
