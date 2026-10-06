"""
Experimento comparativo (solo analisis, no toca la app): que enfoque de
pronostico de demanda da mejores resultados para los productos BASE de
Agrofalma (sin presentaciones, sin comida balanceada), con validacion de
origen movil (rolling origin) sobre ene-2025 a ago-2026.

Metodos: ingenuo, promedio movil 3, estacional ingenuo, SES con indice de
campana, TSB (intermitente), modelo global HistGradientBoosting (Poisson y
cuantiles) y una combinacion. Horizontes: 1 mes, 3 meses (trimestre) y 6
meses (campana, solo a nivel categoria / top-down).

Requiere datos_procesados/mapa_producto_base.csv (mapeo nombre -> producto base).

Uso:
    python -m analitica_job.experimento_pronostico_comparativo
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from analitica_job.preparar_datos import (
    CARPETA_SALIDA,
    ULTIMO_MES_REAL_2026,
    calcular_unidades_vendidas,
    cargar_ventas,
    filtrar_categoria_excluida,
    filtrar_meses_no_confiables,
)

warnings.filterwarnings("ignore")

N = 12 + ULTIMO_MES_REAL_2026  # 20 meses reales: ene-25 .. ago-26
CATS = ["QUIMICOS", "FOLIARES", "FERTILIZANTES"]
CAT_CODE = {c: i for i, c in enumerate(CATS)}
ALPHAS = [0.1, 0.2, 0.3, 0.5]
CUANTILES = [0.1, 0.5, 0.8, 0.9]


# ----------------------------------------------------------------- datos
def cal_mes(m: int) -> int:
    return (m - 1) % 12 + 1


def es_grande(m: int) -> bool:
    return cal_mes(m) <= 6


def cargar_panel():
    df = filtrar_meses_no_confiables(filtrar_categoria_excluida(cargar_ventas()))
    df["unidades"] = calcular_unidades_vendidas(df)
    df["n"] = df["producto_original"].astype(str).str.upper().str.replace(r"\s+", " ", regex=True).str.strip()
    mapa = pd.read_csv(CARPETA_SALIDA / "mapa_producto_base.csv")[["n", "base", "categoria_base"]].drop_duplicates("n")
    df = df.merge(mapa, on="n", how="inner")
    df = df[df["categoria_base"].isin(CATS)]
    df["mes_idx"] = (df["anio"] - 2025) * 12 + df["mes_numero"].astype(int)
    Y = (
        df.pivot_table(index="base", columns="mes_idx", values="unidades", aggfunc="sum", fill_value=0)
        .reindex(columns=range(1, N + 1), fill_value=0)
        .clip(lower=0)
    )
    Y = Y[(Y > 0).any(axis=1)]
    cat = df.groupby("base")["categoria_base"].agg(lambda s: s.mode().iat[0]).reindex(Y.index)
    ingresos = df.groupby("base")["total_soles_calculado"].sum()
    unid = df.groupby("base")["unidades"].sum()
    precio = (ingresos / unid.where(unid > 0)).reindex(Y.index).fillna(0).clip(lower=0)
    return Y.index.to_numpy(), Y.to_numpy(float), cat.to_numpy(), precio.to_numpy(float)


PRODUCTOS, Y, CATEGORIA, PRECIO = cargar_panel()
P = len(PRODUCTOS)
T0 = np.array([int(np.argmax(Y[i] > 0)) + 1 for i in range(P)])  # primer mes con venta (1-based)
CODIGO = np.array([CAT_CODE[c] for c in CATEGORIA])


def y(i: int, m: int) -> float:
    return Y[i, m - 1] if 1 <= m <= N else 0.0


# --------------------------------------------- indices estacionales / SES
def indices_campana(t: int) -> dict:
    """Indice de campana por categoria con la ventana de los ultimos 12 meses hasta t."""
    ventana = range(max(1, t - 11), t + 1)
    out = {}
    for c in CATS:
        mask = CATEGORIA == c
        tot = {m: Y[mask, m - 1].sum() for m in ventana}
        media = np.mean(list(tot.values()))
        g = [tot[m] for m in ventana if es_grande(m)]
        ch = [tot[m] for m in ventana if not es_grande(m)]
        if media <= 0 or not g or not ch:
            out[c] = (1.0, 1.0)
        else:
            out[c] = (max(np.mean(g) / media, 0.2), max(np.mean(ch) / media, 0.2))
    return out


def idx_mes(idx: dict, cat: str, m: int) -> float:
    return idx[cat][0] if es_grande(m) else idx[cat][1]


def ses_nivel(z: np.ndarray) -> float:
    if len(z) == 1:
        return float(z[0])
    inicio = float(np.mean(z[:3]))
    mejor, nivel_mejor = np.inf, inicio
    for a in ALPHAS:
        nivel, sse = inicio, 0.0
        for v in z:
            sse += (v - nivel) ** 2
            nivel = a * v + (1 - a) * nivel
        if sse < mejor:
            mejor, nivel_mejor = sse, nivel
    return float(nivel_mejor)


def ses_estacional(serie: np.ndarray, t0: int, t: int, cat: str, idx: dict, meses_obj: list[int]) -> float:
    meses = range(t0, t + 1)
    z = np.array([serie[m - 1] / idx_mes(idx, cat, m) for m in meses])
    nivel = ses_nivel(z)
    return float(sum(nivel * idx_mes(idx, cat, m) for m in meses_obj))


def tsb(serie: np.ndarray, t0: int, t: int, h: int, a: float = 0.1, b: float = 0.1) -> float:
    ys = serie[t0 - 1 : t]
    z = (ys > 0).astype(float)
    p = float(z[: min(6, len(z))].mean())
    positivos = ys[ys > 0]
    s = float(positivos[0]) if len(positivos) else 0.0
    for v, zz in zip(ys, z):
        p = p + b * (zz - p)
        if v > 0:
            s = s + a * (v - s)
    return h * p * s


# ------------------------------------------------- modelo global (features)
COLS_X = ["lag1", "lag2", "lag3", "lag4", "lag5", "lag6", "mean3", "mean6", "mean12", "nz12",
          "recencia", "edad", "cat", "mes_obj", "n_grande_h1", "n_grande_h3", "max12"]


def construir_features() -> pd.DataFrame:
    filas = []
    for i in range(P):
        for t in range(T0[i], N):
            lags = [y(i, t - k) for k in range(6)]
            ventana12 = [y(i, m) for m in range(max(1, t - 11), t + 1)]
            rec = 0
            for m in range(t, 0, -1):
                if y(i, m) > 0:
                    break
                rec += 1
            fila = {
                "i": i, "t": t,
                "lag1": lags[0], "lag2": lags[1], "lag3": lags[2], "lag4": lags[3], "lag5": lags[4], "lag6": lags[5],
                "mean3": np.mean(lags[:3]), "mean6": np.mean(lags), "mean12": np.mean(ventana12),
                "nz12": sum(v > 0 for v in ventana12), "recencia": rec, "edad": t - T0[i],
                "cat": CODIGO[i], "mes_obj": cal_mes(t + 1),
                "n_grande_h1": int(es_grande(t + 1)),
                "n_grande_h3": sum(es_grande(t + k) for k in (1, 2, 3)),
                "max12": max(ventana12),
                "y1": y(i, t + 1) if t + 1 <= N else np.nan,
                "y3": sum(y(i, t + k) for k in (1, 2, 3)) if t + 3 <= N else np.nan,
            }
            filas.append(fila)
    return pd.DataFrame(filas)


def hgb(loss: str, quantile: float | None = None) -> HistGradientBoostingRegressor:
    kw = dict(loss=loss, max_iter=150, learning_rate=0.06, max_depth=4, min_samples_leaf=15,
              l2_regularization=1.0, random_state=0, categorical_features=[COLS_X.index("cat")])
    if quantile is not None:
        kw["quantile"] = quantile
    return HistGradientBoostingRegressor(**kw)


# ------------------------------------------------------------- clases ABC
def clases_abc(t: int, existentes: np.ndarray) -> dict:
    tot = {i: sum(y(i, m) for m in range(max(1, t - 11), t + 1)) for i in existentes}
    orden = sorted(tot, key=tot.get, reverse=True)
    total = sum(tot.values()) or 1.0
    acum, clases = 0.0, {}
    for i in orden:
        clases[i] = "A" if acum < 0.80 * total else ("B" if acum < 0.95 * total else "C")
        acum += tot[i]
    return clases


# --------------------------------------------------------------- metricas
def wape(real, pred):
    real, pred = np.asarray(real, float), np.asarray(pred, float)
    return np.abs(real - pred).sum() / real.sum() * 100 if real.sum() > 0 else np.nan


def sesgo(real, pred):
    real, pred = np.asarray(real, float), np.asarray(pred, float)
    return (pred.sum() - real.sum()) / real.sum() * 100 if real.sum() > 0 else np.nan


def mape(real, pred):
    real, pred = np.asarray(real, float), np.asarray(pred, float)
    m = real > 0
    return np.mean(np.abs(real[m] - pred[m]) / real[m]) * 100 if m.sum() else np.nan


def pinball(real, pred, q):
    d = np.asarray(real, float) - np.asarray(pred, float)
    return np.maximum(q * d, (q - 1) * d)


# ---------------------------------------------------------------- corrida
def main() -> None:
    print(f"Productos base: {P} | meses: {N} (ene-25 .. ago-26)\n")
    F = construir_features()
    registros = []          # h=1 y h=3 por producto
    registros_cat = []      # nivel categoria
    quant_h1 = {}           # cuantiles HGB h=1 por origen

    for T in range(12, N):
        idx = indices_campana(T)
        existentes = np.where(T0 <= T)[0]
        clases = clases_abc(T, existentes)
        base_rows = F[F["t"] == T].set_index("i")

        modelos = {}
        for h, col in ((1, "y1"), (3, "y3")):
            tr = F[(F["t"] + h <= T)]
            X_tr, y_tr = tr[COLS_X], tr[col]
            modelos[(h, "mean")] = hgb("poisson").fit(X_tr, y_tr)
            if h == 1:
                for q in CUANTILES:
                    modelos[(h, q)] = hgb("quantile", q).fit(X_tr, y_tr)

        # forecasts por producto
        for h in (1, 3, 6):
            if T + h > N:
                continue
            meses_obj = [T + k for k in range(1, h + 1)]
            for i in existentes:
                real = sum(y(i, m) for m in meses_obj)
                cat = CATEGORIA[i]
                fila = {"origen": T, "h": h, "i": i, "cat": cat, "clase": clases[i], "real": real,
                        "precio": PRECIO[i], "mean6": base_rows.loc[i, "mean6"] if i in base_rows.index else 0.0}
                serie = Y[i]
                fila["ingenuo"] = h * y(i, T)
                fila["ma3"] = h * np.mean([y(i, T - k) for k in range(3)])
                # estacional ingenuo: mismos meses del anio anterior (si el producto ya existia)
                previos = [m - 12 for m in meses_obj]
                fila["est_ingenuo"] = (
                    sum(y(i, m) for m in previos) if all(m >= T0[i] for m in previos) else np.nan
                )
                fila["ses_est"] = ses_estacional(serie, T0[i], T, cat, idx, meses_obj)
                fila["tsb"] = tsb(serie, T0[i], T, h)
                fila["ultimo"] = y(i, T)
                fila["max3m"] = max(y(i, T - k) for k in range(3))
                if h in (1, 3):
                    xr = base_rows.loc[[i], COLS_X]
                    fila["hgb"] = float(np.clip(modelos[(h, "mean")].predict(xr)[0], 0, None))
                    fila["ens"] = np.mean([fila["ses_est"], fila["tsb"], fila["hgb"]])
                    if h == 1:
                        for q in CUANTILES:
                            fila[f"hgb_q{int(q * 100)}"] = float(np.clip(modelos[(h, q)].predict(xr)[0], 0, None))
                registros.append(fila)

        # nivel categoria y top-down
        for h in (1, 3, 6):
            if T + h > N:
                continue
            meses_obj = [T + k for k in range(1, h + 1)]
            for c in CATS:
                mask = (CATEGORIA == c) & (T0 <= T)
                if not mask.any():
                    continue
                serie_c = Y[CATEGORIA == c].sum(axis=0)
                inicio_c = int(np.argmax(serie_c > 0)) + 1
                real = sum(serie_c[m - 1] for m in meses_obj)
                previos = [m - 12 for m in meses_obj]
                registros_cat.append({
                    "origen": T, "h": h, "cat": c, "real": real,
                    "ses_est": ses_estacional(serie_c, inicio_c, T, c, idx, meses_obj),
                    "est_ingenuo": sum(serie_c[m - 1] for m in previos) if all(m >= 1 for m in previos) else np.nan,
                })

    R = pd.DataFrame(registros)
    C = pd.DataFrame(registros_cat)

    # bottom-up sumado a categoria (h=1,3)
    bu = (R[R["h"].isin([1, 3])].groupby(["origen", "h", "cat"])[["ens", "hgb"]].sum().reset_index()
          .rename(columns={"ens": "ens_bottom_up", "hgb": "hgb_bottom_up"}))
    C = C.merge(bu, on=["origen", "h", "cat"], how="left")

    # top-down a producto: pronostico de la categoria repartido por participacion de 12 meses
    def participacion(row_group: pd.DataFrame) -> pd.Series:
        T = int(row_group["origen"].iat[0])
        tot = pd.Series({i: sum(y(i, m) for m in range(max(1, T - 11), T + 1)) for i in row_group["i"]})
        return tot / tot.sum() if tot.sum() > 0 else tot * 0

    R["share"] = 0.0
    for (T, h, c), g in R.groupby(["origen", "h", "cat"]):
        R.loc[g.index, "share"] = participacion(g).reindex(g["i"]).to_numpy()
    R = R.merge(C[["origen", "h", "cat", "ses_est"]].rename(columns={"ses_est": "cat_ses_est"}),
                on=["origen", "h", "cat"], how="left")
    R["top_down"] = R["cat_ses_est"] * R["share"]

    # ------------------------------------------------------------ RESULTADOS
    def tabla_metodos(D: pd.DataFrame, metodos: list[str], titulo: str, con_mase=False) -> pd.DataFrame:
        filas = []
        for m in metodos:
            d = D[D[m].notna()]
            fila = {"metodo": m, "n_filas": len(d), "WAPE_%": round(wape(d["real"], d[m]), 1),
                    "sesgo_%": round(sesgo(d["real"], d[m]), 1), "MAPE_%(y>0)": round(mape(d["real"], d[m]), 1)}
            for cl in "ABC":
                dc = d[d["clase"] == cl]
                fila[f"WAPE_{cl}"] = round(wape(dc["real"], dc[m]), 1)
            filas.append(fila)
        t = pd.DataFrame(filas)
        print(f"=== {titulo} ===")
        print(t.to_string(index=False), "\n")
        return t

    # misma poblacion para todos los metodos: filas donde estacional ingenuo existe
    R1 = R[(R["h"] == 1)].copy()
    comun1 = R1.dropna(subset=["est_ingenuo"])
    print(f"Origenes h=1: {sorted(R1['origen'].unique())} | filas comparables: {len(comun1)} de {len(R1)}\n")
    t1 = tabla_metodos(comun1, ["ingenuo", "ma3", "est_ingenuo", "ses_est", "tsb", "hgb", "ens"],
                       "PROXIMO MES por producto (h=1), origen movil")
    ANOMALOS = {10, 11, 12, 15, 16}  # oct-dic 2025 y mar-abr 2026: pocos productos con venta registrada
    sens = comun1[~comun1["origen"].isin(ANOMALOS) & ~(comun1["origen"] + 1).isin(ANOMALOS)]
    print(f"Sensibilidad: origenes {sorted(sens['origen'].unique())}, {len(sens)} filas")
    t1s = tabla_metodos(sens, ["ingenuo", "ma3", "est_ingenuo", "ses_est", "tsb", "hgb", "ens"],
                        "SENSIBILIDAD h=1: solo meses 'normales' (sin oct-dic 2025 ni mar-abr 2026)")
    R3 = R[(R["h"] == 3)].copy()
    comun3 = R3.dropna(subset=["est_ingenuo"])
    print(f"Origenes h=3: {sorted(R3['origen'].unique())} | filas comparables: {len(comun3)} de {len(R3)}\n")
    t3 = tabla_metodos(comun3, ["ingenuo", "ma3", "est_ingenuo", "ses_est", "tsb", "hgb", "ens", "top_down"],
                       "PROXIMO TRIMESTRE por producto (h=3)")
    R6 = R[(R["h"] == 6)].copy()
    comun6 = R6.dropna(subset=["est_ingenuo"])
    print(f"Origenes h=6 (campana): {sorted(R6['origen'].unique())} | filas comparables: {len(comun6)}\n")
    t6 = tabla_metodos(comun6, ["ingenuo", "ma3", "est_ingenuo", "ses_est", "tsb", "top_down"],
                       "CAMPANA / SEMESTRE por producto (h=6)")

    # MAPE solo para productos que venden (casi) todos los meses
    for minimo in (20, 18):
        siempre = {i for i in range(P) if (Y[i] > 0).sum() >= minimo}
        print(f"=== MAPE solo en productos con venta en >= {minimo} de 20 meses ({len(siempre)} productos: "
              f"{sorted(PRODUCTOS[list(siempre)])[:12]}) ===")
        for h, D in ((1, R[R["h"] == 1]), (3, R[R["h"] == 3])):
            d = D[D["i"].isin(siempre)]
            fila = {m: f"{mape(d['real'], d[m]):.1f}" for m in ["ingenuo", "ma3", "ses_est", "hgb", "ens"]}
            print(f"  h={h} (n={len(d)}) MAPE% por metodo: {fila}")
            pp = {m: d.groupby("i").apply(lambda g: mape(g["real"], g[m])) for m in ["ingenuo", "hgb", "ens"]}
            print(f"     MAPE por producto (ens): min={pp['ens'].min():.0f} mediana={pp['ens'].median():.0f} max={pp['ens'].max():.0f} | productos con MAPE<=15%: "
                  f"ens={(pp['ens'] <= 15).sum()}, hgb={(pp['hgb'] <= 15).sum()}, ingenuo={(pp['ingenuo'] <= 15).sum()}")
    print()

    # MASE h=1
    def escala(i, T):
        s = Y[i, T0[i] - 1 : T]
        d = np.abs(np.diff(s))
        return d.mean() if len(d) and d.mean() > 0 else np.nan

    comun1 = comun1.assign(escala=[escala(i, T) for i, T in zip(comun1["i"], comun1["origen"])])
    v = comun1[comun1["escala"].notna()]
    print("=== MASE h=1 (menor es mejor; <1 = mejor que ingenuo del mes previo) ===")
    for m in ["ingenuo", "ma3", "est_ingenuo", "ses_est", "tsb", "hgb", "ens"]:
        print(f"  {m:12s} {np.mean(np.abs(v['real'] - v[m]) / v['escala']):.3f}")
    print()

    # nivel categoria
    print("=== NIVEL CATEGORIA (lo que se podria prometer para trimestre / campana) ===")
    filas = []
    for h in (1, 3, 6):
        d = C[(C["h"] == h)].dropna(subset=["est_ingenuo"])
        metodos = ["est_ingenuo", "ses_est"] + (["ens_bottom_up", "hgb_bottom_up"] if h in (1, 3) else [])
        for m in metodos:
            dd = d[d[m].notna()]
            filas.append({"horizonte": f"h={h}", "n": len(dd), "metodo": m,
                          "WAPE_%": round(wape(dd["real"], dd[m]), 1), "MAPE_%": round(mape(dd["real"], dd[m]), 1),
                          "sesgo_%": round(sesgo(dd["real"], dd[m]), 1)})
    tc = pd.DataFrame(filas)
    print(tc.to_string(index=False), "\n")

    # ------------------------------------------------- intervalos h=1
    print("=== INTERVALOS h=1 (origenes >=14, con calibracion conformal expansiva) ===")
    R1 = R1.copy()
    R1["escala_n"] = np.maximum(R1["mean6"], 1.0)
    R1["resid_n"] = (R1["real"] - R1["ens"]) / R1["escala_n"]
    for q in (0.1, 0.5, 0.8, 0.9):
        R1[f"conf_q{int(q * 100)}"] = np.nan
    for T in sorted(R1["origen"].unique()):
        cal = R1[R1["origen"] < T]
        if len(cal) < 100:
            continue
        for cl in "ABC":
            pool = cal[cal["clase"] == cl]["resid_n"]
            if len(pool) < 30:
                pool = cal["resid_n"]
            idx_fila = R1[(R1["origen"] == T) & (R1["clase"] == cl)].index
            for q in (0.1, 0.5, 0.8, 0.9):
                R1.loc[idx_fila, f"conf_q{int(q * 100)}"] = np.clip(
                    R1.loc[idx_fila, "ens"] + R1.loc[idx_fila, "escala_n"] * np.quantile(pool, q), 0, None)
    ev = R1.dropna(subset=["conf_q10"])
    filas = []
    for nombre, cols in {"HGB cuantiles (directo)": ("hgb_q10", "hgb_q50", "hgb_q90"),
                         "Ensamble + conformal": ("conf_q10", "conf_q50", "conf_q90")}.items():
        lo, med, hi = (ev[c] for c in cols)
        cobertura = ((ev["real"] >= lo) & (ev["real"] <= hi)).mean() * 100
        pin = np.mean([pinball(ev["real"], p, q).sum() for p, q in zip((lo, med, hi), (0.1, 0.5, 0.9))]) / ev["real"].sum() * 100
        filas.append({"metodo": nombre, "n": len(ev), "cobertura_80%_objetivo=80": round(cobertura, 1),
                      "pinball_ponderado_%": round(pin, 1),
                      "ancho_medio_rel_%": round((hi - lo).sum() / ev["real"].sum() * 100, 1)})
    ti = pd.DataFrame(filas)
    print(ti.to_string(index=False), "\n")

    # ------------------------------------------ simulacion de reposicion h=1
    print("=== SIMULACION DE REPOSICION mensual (stock disponible = pronostico; sin arrastre entre meses) ===")
    sim = ev.copy()
    politicas = {
        "Ultimo mes (ingenuo)": sim["ultimo"],
        "Maximo ultimos 3 meses": sim["max3m"],
        "Promedio 3 meses": sim["ma3"],
        "Ensamble (punto)": sim["ens"],
        "Ensamble conformal P80": sim["conf_q80"],
        "Ensamble conformal P90": sim["conf_q90"],
        "HGB cuantil P80": sim["hgb_q80"],
        "HGB cuantil P90": sim["hgb_q90"],
    }
    filas = []
    for nombre, stock in politicas.items():
        stock = np.ceil(np.clip(stock, 0, None))
        d = sim["real"]
        vendido = np.minimum(stock, d)
        exceso = np.clip(stock - d, 0, None)
        con_dem = d > 0
        filas.append({
            "politica": nombre,
            "tasa_de_servicio_%": round(vendido.sum() / d.sum() * 100, 1),
            "prods-mes_con_quiebre_%": round((stock[con_dem] < d[con_dem]).mean() * 100, 1),
            "stock_total_vs_demanda_%": round(stock.sum() / d.sum() * 100, 1),
            "exceso_unid": int(exceso.sum()),
            "exceso_S/": int((exceso * sim["precio"]).sum()),
        })
    ts = pd.DataFrame(filas)
    print(ts.to_string(index=False), "\n")

    salida = {"h1_producto": t1, "h1_meses_normales": t1s, "h3_producto": t3, "h6_producto": t6, "categoria": tc, "intervalos": ti, "simulacion": ts}
    with pd.ExcelWriter(CARPETA_SALIDA / "resultados_experimento_pronostico.xlsx") as w:
        for nombre, tabla in salida.items():
            tabla.to_excel(w, sheet_name=nombre, index=False)
    print(f"Guardado: {CARPETA_SALIDA / 'resultados_experimento_pronostico.xlsx'}")


if __name__ == "__main__":
    main()
