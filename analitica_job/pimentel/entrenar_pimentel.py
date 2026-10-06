"""Entrenamiento/evaluación Sede Pimentel. Train: ene2023-jun2025. Test: jul2025-dic2025 + ene-jun2026."""
from __future__ import annotations
import re, warnings
import numpy as np, pandas as pd
from sklearn.ensemble import RandomForestRegressor, HistGradientBoostingRegressor, ExtraTreesRegressor
from cargar_pimentel import SALIDA, cargar
warnings.filterwarnings("ignore")

IDX = lambda a, m: (a - 2023) * 12 + m          # ene2023 = 1
CORTE = IDX(2025, 6)                             # 30
FIN = IDX(2026, 6)                               # 42

def panel() -> pd.DataFrame:
    d = cargar()
    d = d[(d.bloque < 4) & ~((d.anio == 2026) & (d.mes > 6))].copy()
    d = d[~d.producto_norm.str.fullmatch(r"[\d\s]+|ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO")]
    d["idx"] = IDX(d.anio, d.mes)
    g = d.groupby(["producto_norm", "idx"]).cantidad.sum().unstack("idx").reindex(columns=range(1, FIN + 1)).fillna(0)
    bl = d.groupby("producto_norm").bloque.agg(lambda s: s.mode().iat[0])
    p = g.stack().rename("y").reset_index()
    p["bloque"] = p.producto_norm.map(bl)
    p["mes"] = (p.idx - 1) % 12 + 1
    return p.sort_values(["producto_norm", "idx"]).reset_index(drop=True)

def features(p: pd.DataFrame) -> pd.DataFrame:
    p = p.copy()
    g = p.groupby("producto_norm").y
    for l in (1, 2, 3, 12, 13, 14, 24):
        p[f"lag{l}"] = g.shift(l)
    p["mm3"] = g.transform(lambda s: s.shift(1).rolling(3).mean())
    p["mm12"] = g.transform(lambda s: s.shift(1).rolling(12, min_periods=1).mean())
    p["est12"] = p[["lag12", "lag13", "lag14"]].mean(axis=1)          # vecindad estacional (h<=12)
    p["est_prom"] = p[["lag12", "lag24"]].mean(axis=1)
    p["sin"] = np.sin(2 * np.pi * p.mes / 12); p["cos"] = np.cos(2 * np.pi * p.mes / 12)
    return p

def metricas(y, f):
    y = np.asarray(y, float); f = np.clip(np.asarray(f, float), 0, None)
    pos = y > 0
    mape = np.mean(np.abs(y[pos] - f[pos]) / y[pos]) * 100
    den = (y + f) / 2; ok = den > 0
    smape = np.mean(np.abs(y[ok] - f[ok]) / den[ok]) * 100
    wape = np.abs(y - f).sum() / y.sum() * 100
    return mape, smape, wape

def main():
    p = features(panel())
    train_prod = set(p[(p.idx <= CORTE) & (p.y > 0)].producto_norm)
    p = p[p.producto_norm.isin(train_prod)].copy()
    # volumen de ventas del test cubierto por productos conocidos
    pa = panel(); t = pa[pa.idx > CORTE]
    print(f"Productos con venta en train: {len(train_prod)}; cobertura de unidades en test: {t[t.producto_norm.isin(train_prod)].y.sum()/t.y.sum():.1%}")

    tr = p[p.idx <= CORTE]; te = p[p.idx > CORTE].copy()
    # escala MASE: MAE ingenuo (mes anterior) en train
    esc = tr.groupby("producto_norm").y.apply(lambda s: np.abs(np.diff(s.values)).mean())

    res = {}
    # --- Escenario A: un paso adelante (lags reales del mes anterior) ---
    A = ["lag1", "lag2", "lag3", "lag12", "mm3", "mm12", "est12", "sin", "cos", "bloque"]
    # --- Escenario B: pronóstico 12 meses desde junio-2025 (solo info <= jun 2025) ---
    B = ["lag12", "lag13", "lag14", "sin", "cos", "bloque", "mes"]
    def fit(cols, kind, trn):
        trn = trn.dropna(subset=cols)
        X, y = trn[cols], trn.y
        m = {"RF": RandomForestRegressor(400, min_samples_leaf=2, random_state=0, n_jobs=-1),
             "ET": ExtraTreesRegressor(400, min_samples_leaf=2, random_state=0, n_jobs=-1),
             "HGB": HistGradientBoostingRegressor(loss="poisson", max_iter=300, learning_rate=0.05, max_depth=4, random_state=0)}[kind]
        return m.fit(X, y)
    out = []
    for esc_name, cols, base_cols in [("A_un_paso", A, ["lag1", "lag12"]), ("B_12_meses", B, ["lag12"])]:
        preds = {}
        te2 = te.copy()
        if esc_name == "A_un_paso":
            preds["Ingenuo (mes anterior)"] = te2.lag1.fillna(0)
            preds["Promedio movil 3m"] = te2.mm3.fillna(0)
        preds["Estacional ingenuo (mismo mes año ant.)"] = te2.lag12.fillna(0)
        preds["Promedio estacional (lag12-14)"] = te2.est12.fillna(0)
        for k in ("RF", "ET", "HGB"):
            m = fit(cols, k, tr)
            preds[k] = pd.Series(m.predict(te2[cols].fillna(0)), index=te2.index)
        preds["Ensamble RF+HGB"] = (preds["RF"] + preds["HGB"]) / 2
        for nm, f in preds.items():
            for per, mask in [("Jul-Dic 2025", te2.idx <= IDX(2025, 12)), ("Ene-Jun 2026", te2.idx > IDX(2025, 12)), ("Total 12m", te2.idx > 0)]:
                mape, smape, wape = metricas(te2.y[mask], f[mask])
                sc = te2.producto_norm[mask].map(esc); v = sc > 0
                mase = (np.abs(te2.y[mask] - f[mask].clip(lower=0))[v] / sc[v]).mean()
                # MAPE agregado: por producto sumando los 6/12 meses
                ag = pd.DataFrame({"p": te2.producto_norm[mask], "y": te2.y[mask], "f": f[mask].clip(lower=0)}).groupby("p").sum()
                ag = ag[ag.y > 0]; mape_ag = (np.abs(ag.y - ag.f) / ag.y).mean() * 100
                # MAPE de la serie total mensual de la sede
                tm = pd.DataFrame({"i": te2.idx[mask], "y": te2.y[mask], "f": f[mask].clip(lower=0)}).groupby("i").sum()
                mape_tot = (np.abs(tm.y - tm.f) / tm.y).mean() * 100
                out.append((esc_name, nm, per, round(mape, 1), round(smape, 1), round(wape, 1), round(mase, 3), round(mape_ag, 1), round(mape_tot, 1)))
        te[[f"pred_{esc_name}_{k}" for k in ("RF",)]] = pd.DataFrame({f"pred_{esc_name}_RF": preds["RF"]})
        te[f"pred_{esc_name}_HGB"] = preds["HGB"]
    r = pd.DataFrame(out, columns=["escenario", "modelo", "periodo", "MAPE_fila_%", "sMAPE_%", "WAPE_%", "MASE", "MAPE_prod_agregado_%", "MAPE_total_mensual_%"])
    r.to_csv(SALIDA / "resultados_pimentel.csv", index=False)
    te.to_csv(SALIDA / "predicciones_test.csv", index=False)
    pd.set_option("display.width", 250, "display.max_rows", 200)
    print(r[r.periodo == "Total 12m"].to_string(index=False))
    print(r[r.periodo != "Total 12m"].to_string(index=False))

if __name__ == "__main__":
    main()
