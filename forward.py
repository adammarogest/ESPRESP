"""
Taux forward implicites de la courbe BDT et comparaison avec les prévisions.

Courbe zéro-coupon : même logique que ZCP (taux_courbe.py), vectorisée sur tout l'historique.
- Ténors courts (13s, 26s, 52s) : taux monétaire converti en actuariel, (1 + y·j/360)^(365/j) − 1.
- 1 à 30 ans : taux actuariel de la maturité n (interpolation linéaire en jours sur la courbe,
  le 52 semaines étant d'abord converti en actuariel, comme taux_courbe au passage du seuil d'un an),
  puis bootstrap d'obligations au pair à coupon annuel :
      zc_n = [(1 + tt_n) / (1 − tt_n · Σ_{i<n} (1 + zc_i)^−i)]^(1/n) − 1
- Entre deux nœuds, interpolation linéaire des taux ZC ; facteur d'actualisation DF(t) = (1 + zc(t))^−t.

Forward entre t1 et t2 (actuariel) : (DF(t1) / DF(t2))^(1/(t2 − t1)) − 1.

Courbe implicite dans h (« courbe forward ») exprimée dans la convention de chaque ténor :
- ténor ≤ 52 semaines (j jours) : taux monétaire forward = (DF(h) / DF(h + j/365) − 1) · 360 / j
- ténor de n ans : rendement au pair forward = (1 − D(h+n)/D(h)) / Σ_{k=1..n} D(h+k)/D(h)
À h = 0, on retrouve la courbe des ténors : la courbe forward est donc directement comparable aux
courbes prévues (pentes historiques, Nelson-Siegel). Sous l'hypothèse des anticipations pures,
c'est la courbe que le marché « attend » ; en pratique elle inclut une prime de terme.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

import base_courbe as db
import prevision as PV

TENORS = db.TENOR_COLS
JOURS = np.array([db.TENORS[t] for t in TENORS], dtype=float)
COURTS = JOURS <= 364
ANNEES = np.arange(1, 31)

# Horizons : nombre d'observations (comme la fenêtre Prévision) et équivalent calendaire
HORIZONS = {"1 mois": (21, 30), "3 mois": (63, 91), "6 mois": (126, 182), "1 an": (252, 365)}
DEPARTS = {"Aujourd'hui": 0, "3 mois": 91, "6 mois": 182, "1 an": 365, "2 ans": 730, "3 ans": 1095,
           "5 ans": 1826, "7 ans": 2557, "10 ans": 3652}
PAIRES_KPI = [  # (libellé, départ en jours, ténor)
    ("13s dans 3 mois", 91, "13s"), ("52s dans 1 an", 365, "52s"), ("2 ans dans 1 an", 365, "2ans"),
    ("5 ans dans 5 ans", 1826, "5ans"), ("10 ans dans 10 ans", 3652, "10ans"),
]


# ---------------------------------------------------------------------------
# Courbe zéro-coupon
# ---------------------------------------------------------------------------

def _poids(grille: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Indices et poids d'interpolation linéaire (plat aux extrémités) sur une grille fixe."""
    x = np.clip(x, grille[0], grille[-1])
    i1 = np.clip(np.searchsorted(grille, x, side="left"), 1, len(grille) - 1)
    i0 = i1 - 1
    w = (x - grille[i0]) / (grille[i1] - grille[i0])
    return i0, i1, w


def zero_coupon(niv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    niv : taux des 9 ténors en %, forme (N, 9).
    Renvoie (nœuds en années, taux ZC actuariels en décimal de forme (N, K)).
    """
    y = np.atleast_2d(np.asarray(niv, dtype=float)) / 100
    j_c = JOURS[COURTS]
    act_c = (1 + y[:, COURTS] * j_c / 360) ** (365 / j_c) - 1

    # Courbe actuarielle en jours : 52s converti, puis ténors longs
    grille = np.concatenate([[364.0], JOURS[~COURTS]])
    valeurs = np.column_stack([act_c[:, -1], y[:, ~COURTS]])
    i0, i1, w = _poids(grille, 365.0 * ANNEES)
    tt = valeurs[:, i0] * (1 - w) + valeurs[:, i1] * w                  # (N, 30)

    zz = np.empty_like(tt)
    zz[:, 0] = tt[:, 0]
    for n in range(2, 31):
        somme = sum((1 + zz[:, i - 1]) ** (-i) for i in range(1, n))
        zz[:, n - 1] = ((1 + tt[:, n - 1]) / (1 - tt[:, n - 1] * somme)) ** (1 / n) - 1

    noeuds = np.concatenate([j_c / 365, ANNEES.astype(float)])
    return noeuds, np.column_stack([act_c, zz])


def actualisation(noeuds: np.ndarray, zc: np.ndarray, t) -> np.ndarray:
    """Facteurs d'actualisation aux maturités t (années), forme (N, len(t))."""
    t = np.atleast_1d(np.asarray(t, dtype=float))
    i0, i1, w = _poids(noeuds, t)
    z = zc[:, i0] * (1 - w) + zc[:, i1] * w
    return (1 + z) ** (-t)


def taux_zc(noeuds, zc, t) -> np.ndarray:
    t = np.atleast_1d(np.asarray(t, dtype=float))
    return actualisation(noeuds, zc, t) ** (-1 / t) - 1


def forward_actuariel(noeuds, zc, t1, t2) -> np.ndarray:
    d1, d2 = actualisation(noeuds, zc, t1), actualisation(noeuds, zc, t2)
    return (d1 / d2) ** (1 / (np.asarray(t2) - np.asarray(t1))) - 1


def courbe_implicite(niv: np.ndarray, h_jours: float) -> np.ndarray:
    """Courbe des 9 ténors attendue dans h jours (%), dans la convention de chaque ténor."""
    noeuds, zc = zero_coupon(niv)
    h = h_jours / 365
    dh = actualisation(noeuds, zc, [h]) if h > 0 else np.ones((zc.shape[0], 1))
    res = np.empty((zc.shape[0], len(TENORS)))
    for k, j in enumerate(JOURS):
        if j <= 364:
            d = actualisation(noeuds, zc, [h + j / 365])
            res[:, k] = ((dh / d - 1) * 360 / j)[:, 0]
        else:
            n = int(round(j / 365.25))
            d = actualisation(noeuds, zc, h + np.arange(1, n + 1)) / dh
            res[:, k] = (1 - d[:, -1]) / d.sum(axis=1)
    return res * 100


# ---------------------------------------------------------------------------
# Analyse à une date
# ---------------------------------------------------------------------------

def _metriques(prev: np.ndarray, reel: np.ndarray, base: np.ndarray) -> dict:
    err = (prev - reel) * 100
    sp, sr = np.sign(prev - base), np.sign(reel - base)
    utile = (sp != 0) & (sr != 0)
    rmse = float(np.sqrt(np.nanmean(err ** 2)))
    return {"RMSE (pb)": rmse, "MAE (pb)": float(np.nanmean(np.abs(err))), "Biais (pb)": float(np.nanmean(err)),
            "Bon sens": float((sp == sr)[utile].mean()) if utile.any() else np.nan,
            "RMSE par ténor": np.sqrt(np.nanmean(err ** 2, axis=0))}


@lru_cache(maxsize=4)
def historique_forward(h_cal: int, version: int) -> pd.DataFrame:
    """Courbe implicite dans h_cal jours, calculée à chaque date de la base."""
    niv, _ = PV.donnees(version)
    return pd.DataFrame(courbe_implicite(niv.to_numpy(), h_cal), index=niv.index, columns=TENORS)


@lru_cache(maxsize=8)
def analyse(ref: str | None, horizon: str, fenetre: str, version: int) -> dict:
    niv, td = PV.donnees(version)
    t0 = PV.date_reference(niv, ref)
    h_obs, h_cal = HORIZONS[horizon]
    actuelle = niv.loc[t0]
    x0 = actuelle.to_numpy()[None, :]
    noeuds, zc = zero_coupon(x0)

    # Courbes : observée, zéro-coupon, forward 1 an glissant
    t_fin = np.linspace(0.25, 29, 232)
    zc_fin = taux_zc(noeuds, zc, t_fin)[0] * 100
    fwd1 = forward_actuariel(noeuds, zc, t_fin, t_fin + 1)[0] * 100
    zc_tenors = taux_zc(noeuds, zc, JOURS / 365)[0] * 100

    # Matrice : courbe des ténors attendue à chaque date de départ
    matrice = pd.DataFrame({nom: courbe_implicite(x0, j)[0] for nom, j in DEPARTS.items()}, index=TENORS).T

    kpis = []
    for lib, depart, tenor in PAIRES_KPI:
        f = courbe_implicite(x0, depart)[0][TENORS.index(tenor)]
        kpis.append({"lib": lib, "forward": f, "actuel": actuelle[tenor], "ecart": (f - actuelle[tenor]) * 100})
    f1 = courbe_implicite(x0, 365)[0]
    pente_auj = (actuelle["10ans"] - actuelle["2ans"]) * 100
    pente_fwd = (f1[TENORS.index("10ans")] - f1[TENORS.index("2ans")]) * 100

    # Prévisions des autres méthodes au même horizon
    pentes = PV.analyse_pentes(t0.strftime("%Y-%m-%d"), h_obs, None, None, version)
    ns = PV.analyse_ns(t0.strftime("%Y-%m-%d"), h_obs, fenetre, None, None, version)
    forward_h = pd.Series(courbe_implicite(x0, h_cal)[0], index=TENORS)
    courbes = pd.DataFrame({"Actuelle": actuelle, "Forward implicite": forward_h})
    for m, lib in PV.METHODES.items():
        att = pentes["attendues"][m]
        if fenetre in att:
            courbes[lib] = att[fenetre]
    courbes["Nelson-Siegel"] = ns["prevue"]

    # Backtest depuis le début de l'année (mêmes dates de test que la fenêtre Prévision)
    hist = historique_forward(h_cal, version)
    idx = niv.index
    pos_t0 = idx.get_loc(t0)
    debut = pd.Timestamp(t0.year, 1, 1)
    tests = [t for t in idx[(idx >= debut) & (idx <= t0)] if idx.get_loc(t) + h_obs <= pos_t0]
    lignes, rmse_tenor = [], {}
    if tests:
        pos = idx.get_indexer(tests)
        reel = niv.iloc[pos + h_obs].to_numpy()
        base = niv.iloc[pos].to_numpy()
        ma = _metriques(base, reel, base)
        fw = _metriques(hist.iloc[pos].to_numpy(), reel, base)

        def ligne(nom, r):
            return {"Méthode": nom, "RMSE (pb)": r["RMSE (pb)"], "MAE (pb)": r["MAE (pb)"],
                    "Biais (pb)": r["Biais (pb)"], "Bon sens": r.get("Bon sens", np.nan),
                    "Gain vs marche aléatoire": 1 - r["RMSE (pb)"] / ma["RMSE (pb)"] if ma["RMSE (pb)"] else np.nan}
        lignes.append(ligne("Marche aléatoire", ma) | {"Bon sens": np.nan, "Gain vs marche aléatoire": 0.0})
        lignes.append(ligne("Forward implicite", fw))
        rmse_tenor["Marche aléatoire"] = ma["RMSE par ténor"]
        rmse_tenor["Forward implicite"] = fw["RMSE par ténor"]
        for m, lib in PV.METHODES.items():
            bt = pentes["backtest"][m]
            if not bt.empty and fenetre in bt.index:
                lignes.append({"Méthode": lib, **bt.loc[fenetre].to_dict()})
            rt = pentes["rmse_tenor"][m]
            if fenetre in rt:
                rmse_tenor[lib] = rt[fenetre].to_numpy()
        if not ns["backtest"].empty and fenetre in ns["backtest"].index:
            lignes.append({"Méthode": "Nelson-Siegel", **ns["backtest"].loc[fenetre].to_dict()})

    # Prime de terme ex post : forward − taux réalisé à l'échéance de l'horizon
    pos_all = np.arange(len(idx) - h_obs)
    ecart = (hist.iloc[pos_all].to_numpy() - niv.iloc[pos_all + h_obs].to_numpy()) * 100
    ecart = pd.DataFrame(ecart, index=idx[pos_all], columns=TENORS)
    ecart = ecart.loc[ecart.index <= idx[max(pos_t0 - h_obs, 0)]]
    prime = pd.DataFrame({
        "Tout l'historique": ecart.mean(),
        "5 dernières années": ecart.loc[ecart.index > t0 - pd.DateOffset(years=5)].mean(),
        "Depuis le début de l'année": ecart.loc[ecart.index >= debut].mean() if (ecart.index >= debut).any()
        else pd.Series(np.nan, index=TENORS),
    })

    # Précision sur 5 ans, par ténor : forward vs marche aléatoire (taux du jour inchangé)
    cinq = ecart.index > t0 - pd.DateOffset(years=5)
    p5 = idx.get_indexer(ecart.index[cinq])
    err_ma = (niv.iloc[p5].to_numpy() - niv.iloc[p5 + h_obs].to_numpy()) * 100
    rmse_fw5 = np.sqrt((ecart.loc[cinq] ** 2).mean())
    rmse_ma5 = pd.Series(np.sqrt(np.nanmean(err_ma ** 2, axis=0)), index=TENORS)
    prime["RMSE forward 5 ans"] = rmse_fw5
    prime["RMSE marche aléatoire 5 ans"] = rmse_ma5

    # Série : forward formé h observations plus tôt, comparé au réalisé
    serie_fwd = hist.shift(h_obs)

    return {
        "t0": t0, "horizon": horizon, "h_obs": h_obs, "h_cal": h_cal, "fenetre": fenetre,
        "actuelle": actuelle, "zc_tenors": pd.Series(zc_tenors, index=TENORS),
        "t_fin": t_fin, "zc_fin": zc_fin, "fwd1_fin": fwd1,
        "matrice": matrice, "kpis": kpis, "pente_auj": pente_auj, "pente_fwd": pente_fwd,
        "courbes": courbes, "backtest": pd.DataFrame(lignes).set_index("Méthode") if lignes else pd.DataFrame(),
        "rmse_tenor": pd.DataFrame(rmse_tenor, index=TENORS) if rmse_tenor else pd.DataFrame(),
        "n_tests": len(tests), "debut_tests": tests[0] if tests else None,
        "prime": prime, "serie_fwd": serie_fwd, "niv": niv, "td0": td.get(t0, np.nan),
    }
