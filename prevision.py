"""
Prévision de la courbe des taux à partir de l'historique de la base.

1. Pentes historiques (moyennes sur une fenêtre de référence) :
   - Pentes inter-ténors     : on part du 13 semaines observé et on enchaîne les écarts moyens
                               entre ténors adjacents (26s − 13s, 52s − 26s, ...).
   - Pente vs taux directeur : taux directeur en vigueur + écart moyen (ténor − taux directeur).
   - Moyenne des variations  : courbe du jour + h × variation quotidienne moyenne (dérive).
   Référence « marche aléatoire » : la courbe du jour reste inchangée.

2. Nelson-Siegel (approche Diebold-Li) : λ estimé sur la fenêtre, facteurs β1 (niveau),
   β2 (pente), β3 (courbure) ajustés chaque jour, prévus par un AR(1).

Backtest depuis le début de l'année : à chaque date t, la prévision n'utilise que les données
connues en t (fenêtre glissante qui se termine en t), puis on la compare à la courbe observée en t + h.
Exception : une fenêtre personnalisée est fixe ; si elle déborde sur l'année testée, le backtest
utilise de l'information future (signalé dans l'interface).
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

import base_courbe as db

TENORS = db.TENOR_COLS
MATURITES = np.array([db.TENORS[t] / 365.25 for t in TENORS])      # en années
FENETRES = {"1A": 1, "3A": 3, "5A": 5, "10A": 10, "Tout": None, "Perso": "perso"}
METHODES = {"inter": "Pentes inter-ténors", "td": "Pente vs taux directeur",
            "derive": "Moyenne des variations"}
HORIZONS = {"1 jour": 1, "1 semaine": 5, "1 mois": 21, "3 mois": 63}
MIN_OBS = 20


# ---------------------------------------------------------------------------
# Données
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4)
def donnees(version: int) -> tuple[pd.DataFrame, pd.Series]:
    """Historique complet des ténors (%) et taux directeur en vigueur à chaque date."""
    with db.connexion() as conn:
        niv = db.lire_historique(conn).set_index("Date")[TENORS]
        td = db.taux_directeur_journalier(conn, niv.index)
    return niv, td


def date_reference(niv: pd.DataFrame, ref) -> pd.Timestamp:
    """Dernière date de la base antérieure ou égale à ref."""
    ref = pd.Timestamp(ref) if ref else niv.index[-1]
    avant = niv.index[niv.index <= ref]
    return avant[-1] if len(avant) else niv.index[0]


# ---------------------------------------------------------------------------
# Moyennes de référence (glissantes, sans regard vers le futur)
# ---------------------------------------------------------------------------

def _moyenne(df: pd.DataFrame, fenetre, perso: tuple | None) -> pd.DataFrame:
    if fenetre == "perso":
        a, b = perso
        cte = df.loc[(df.index >= pd.Timestamp(a)) & (df.index <= pd.Timestamp(b))].mean()
        return pd.DataFrame([cte.to_numpy()] * len(df), index=df.index, columns=df.columns)
    if fenetre is None:
        return df.expanding(min_periods=MIN_OBS).mean()
    return df.rolling(f"{int(round(365.25 * fenetre))}D", min_periods=MIN_OBS).mean()


def moyennes(niv: pd.DataFrame, td: pd.Series, fenetre, perso=None) -> dict[str, pd.DataFrame]:
    ecart_td = niv.sub(td, axis=0)
    adj = niv.diff(axis=1)                      # ténor k − ténor k-1 (1re colonne vide)
    var = niv.diff()                            # variations quotidiennes (%)
    return {"td": _moyenne(ecart_td, fenetre, perso),
            "inter": _moyenne(adj, fenetre, perso),
            "derive": _moyenne(var, fenetre, perso)}


def predire(niv: pd.DataFrame, td: pd.Series, moy: dict, methode: str, h: int) -> pd.DataFrame:
    """Courbe prévue pour t + h, calculée en chaque date t (index = t)."""
    if methode == "td":
        return moy["td"].add(td, axis=0)
    if methode == "inter":
        ecarts = moy["inter"].copy()
        ecarts[TENORS[0]] = niv[TENORS[0]]      # ancrage : 13 semaines observé
        return ecarts.cumsum(axis=1)
    if methode == "derive":
        return niv + h * moy["derive"]
    if methode == "ma":
        return niv.copy()
    raise ValueError(methode)


# ---------------------------------------------------------------------------
# Courbes attendues et backtest
# ---------------------------------------------------------------------------

def _metriques(prev: pd.DataFrame, reel: pd.DataFrame, base: pd.DataFrame) -> dict:
    err = (prev - reel) * 100                                   # pb
    ok = err.notna() & reel.notna()
    e = err.where(ok)
    sens_prev, sens_reel = np.sign(prev - base), np.sign(reel - base)
    utile = ok & (sens_reel != 0) & (sens_prev != 0)
    return {
        "RMSE (pb)": float(np.sqrt(np.nanmean(e.to_numpy() ** 2))) if ok.any().any() else np.nan,
        "MAE (pb)": float(np.nanmean(np.abs(e.to_numpy()))) if ok.any().any() else np.nan,
        "Bon sens": float((sens_prev == sens_reel)[utile].sum().sum() / utile.sum().sum())
                    if utile.any().any() else np.nan,
        "RMSE par ténor": np.sqrt((e ** 2).mean()),
        "Biais (pb)": float(np.nanmean(e.to_numpy())) if ok.any().any() else np.nan,
    }


@lru_cache(maxsize=8)
def analyse_pentes(ref: str | None, h: int, perso_a: str | None, perso_b: str | None,
                   version: int) -> dict:
    niv, td = donnees(version)
    t0 = date_reference(niv, ref)
    perso = (perso_a, perso_b) if perso_a and perso_b else None

    debut_annee = pd.Timestamp(t0.year, 1, 1)
    idx = niv.index
    pos = np.arange(len(idx))
    tests = idx[(idx >= debut_annee) & (idx <= t0) & (pos + h < len(idx))]
    tests = tests[idx.get_indexer(tests) + h <= idx.get_loc(t0)]   # cible connue à la date de référence
    cibles = idx[idx.get_indexer(tests) + h]
    reel = niv.loc[cibles].set_axis(tests)
    base = niv.loc[tests]

    attendues = {m: {} for m in METHODES}
    backtest = {m: [] for m in METHODES}
    rmse_tenor = {m: {} for m in METHODES}
    prev_ma = predire(niv, td, {}, "ma", h)
    ma = _metriques(prev_ma.loc[tests], reel, base) if len(tests) else None

    for nom, fen in FENETRES.items():
        if fen == "perso" and perso is None:
            continue
        moy = moyennes(niv, td, fen, perso)
        for m in METHODES:
            prev = predire(niv, td, moy, m, h)
            attendues[m][nom] = prev.loc[t0]
            if len(tests):
                r = _metriques(prev.loc[tests], reel, base)
                backtest[m].append({"Fenêtre": nom, "RMSE (pb)": r["RMSE (pb)"], "MAE (pb)": r["MAE (pb)"],
                                    "Biais (pb)": r["Biais (pb)"], "Bon sens": r["Bon sens"],
                                    "Gain vs marche aléatoire": 1 - r["RMSE (pb)"] / ma["RMSE (pb)"]
                                    if ma and ma["RMSE (pb)"] else np.nan})
                rmse_tenor[m][nom] = r["RMSE par ténor"]

    fuite = bool(perso and pd.Timestamp(perso[1]) >= debut_annee)
    return {
        "t0": t0, "td0": td.get(t0, np.nan), "actuelle": niv.loc[t0],
        "attendues": {m: pd.DataFrame(v) for m, v in attendues.items()},
        "backtest": {m: pd.DataFrame(v).set_index("Fenêtre") if v else pd.DataFrame() for m, v in backtest.items()},
        "rmse_tenor": {m: pd.DataFrame(v) for m, v in rmse_tenor.items()},
        "ma": ma, "n_tests": len(tests), "debut_tests": tests[0] if len(tests) else None,
        "fuite_perso": fuite,
    }


# ---------------------------------------------------------------------------
# Nelson-Siegel
# ---------------------------------------------------------------------------

def ns_chargements(lam: float, tau: np.ndarray = MATURITES) -> np.ndarray:
    x = lam * np.asarray(tau, dtype=float)
    l2 = (1 - np.exp(-x)) / x
    return np.column_stack([np.ones_like(x), l2, l2 - np.exp(-x)])


def ns_ajuster(niv: pd.DataFrame, lam: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Facteurs β par moindres carrés pour chaque date (λ fixe) et courbe ajustée."""
    X = ns_chargements(lam)
    Y = niv.to_numpy()
    B = np.linalg.lstsq(X, Y.T, rcond=None)[0].T
    betas = pd.DataFrame(B, index=niv.index, columns=["β1 niveau", "β2 pente", "β3 courbure"])
    return betas, pd.DataFrame(B @ X.T, index=niv.index, columns=TENORS)


def ns_lambda(niv: pd.DataFrame, grille=np.arange(0.05, 3.0001, 0.01)) -> float:
    """λ minimisant l'erreur d'ajustement totale sur la période."""
    Y = niv.to_numpy().T
    meilleur, sse_min = grille[0], np.inf
    for lam in grille:
        X = ns_chargements(lam)
        B = np.linalg.lstsq(X, Y, rcond=None)[0]
        sse = float(((X @ B - Y) ** 2).sum())
        if sse < sse_min:
            meilleur, sse_min = lam, sse
    return float(meilleur)


def ar1(x: np.ndarray) -> tuple[float, float]:
    """x_{t+1} = c + φ x_t ; renvoie (moyenne de long terme μ, φ). φ borné à 0.9995."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < MIN_OBS:
        return (float(x[-1]) if len(x) else np.nan), 1.0
    a, b = x[:-1], x[1:]
    va = a.var()
    phi = float(((a - a.mean()) * (b - b.mean())).mean() / va) if va > 0 else 1.0
    phi = min(phi, 0.9995)
    c = b.mean() - phi * a.mean()
    return c / (1 - phi), phi


def ns_prevoir(beta_t: np.ndarray, params: list[tuple[float, float]], h: int) -> np.ndarray:
    return np.array([mu + phi ** h * (b - mu) for b, (mu, phi) in zip(beta_t, params)])


def _fenetre_masque(idx: pd.DatetimeIndex, t: pd.Timestamp, fen, perso) -> np.ndarray:
    if fen == "perso":
        return (idx >= pd.Timestamp(perso[0])) & (idx <= pd.Timestamp(perso[1]))
    if fen is None:
        return idx <= t
    return (idx > t - pd.Timedelta(days=int(round(365.25 * fen)))) & (idx <= t)


@lru_cache(maxsize=8)
def analyse_ns(ref: str | None, h: int, fenetre: str, perso_a: str | None, perso_b: str | None,
               version: int) -> dict:
    niv, _ = donnees(version)
    t0 = date_reference(niv, ref)
    perso = (perso_a, perso_b) if perso_a and perso_b else None
    fen = FENETRES.get(fenetre, 5)
    if fen == "perso" and perso is None:
        fen = 5

    # Prévision du jour : λ et AR(1) estimés sur la fenêtre choisie
    masque = _fenetre_masque(niv.index, t0, fen, perso)
    lam = ns_lambda(niv.loc[masque])
    betas, ajuste = ns_ajuster(niv, lam)
    params = [ar1(betas.loc[masque, c].to_numpy()) for c in betas.columns]
    b0 = betas.loc[t0].to_numpy()
    b_prev = ns_prevoir(b0, params, h)
    X = ns_chargements(lam)
    tau_fin = np.linspace(0.1, 30, 300)
    Xf = ns_chargements(lam, tau_fin)

    # Backtest depuis le début de l'année, pour chaque fenêtre (λ estimé avant l'année testée)
    debut_annee = pd.Timestamp(t0.year, 1, 1)
    idx = niv.index
    pos_t0 = idx.get_loc(t0)
    tests = [t for t in idx[(idx >= debut_annee) & (idx <= t0)] if idx.get_loc(t) + h <= pos_t0]
    lam_bt = ns_lambda(niv.loc[idx < debut_annee]) if (idx < debut_annee).sum() > MIN_OBS else lam
    betas_bt, _ = ns_ajuster(niv, lam_bt)
    X_bt = ns_chargements(lam_bt)
    lignes = []
    if tests:
        reel = np.array([niv.iloc[idx.get_loc(t) + h].to_numpy() for t in tests])
        base = np.array([niv.loc[t].to_numpy() for t in tests])
        rmse_ma = float(np.sqrt(np.mean(((base - reel) * 100) ** 2)))
        for nom, f in FENETRES.items():
            if f == "perso" and perso is None:
                continue
            prev = []
            for t in tests:
                m = _fenetre_masque(idx, t, f, perso)
                p = [ar1(betas_bt.loc[m, c].to_numpy()) for c in betas_bt.columns]
                prev.append(X_bt @ ns_prevoir(betas_bt.loc[t].to_numpy(), p, h))
            prev = np.array(prev)
            err = (prev - reel) * 100
            sp, sr = np.sign(prev - base), np.sign(reel - base)
            utile = (sr != 0) & (sp != 0)
            rmse = float(np.sqrt(np.mean(err ** 2)))
            lignes.append({"Fenêtre": nom, "RMSE (pb)": rmse, "MAE (pb)": float(np.mean(np.abs(err))),
                           "Biais (pb)": float(err.mean()),
                           "Bon sens": float((sp == sr)[utile].mean()) if utile.any() else np.nan,
                           "Gain vs marche aléatoire": 1 - rmse / rmse_ma if rmse_ma else np.nan})

    return {
        "t0": t0, "lambda": lam, "lambda_bt": lam_bt, "fenetre": fenetre,
        "betas": betas.loc[masque | (betas.index == t0)], "beta0": b0, "beta_prev": b_prev, "params": params,
        "actuelle": niv.loc[t0], "ajustee": ajuste.loc[t0],
        "prevue": pd.Series(X @ b_prev, index=TENORS),
        "rmse_ajustement": float(np.sqrt(np.mean(((ajuste.loc[t0] - niv.loc[t0]) * 100) ** 2))),
        "courbe_fine": (tau_fin, Xf @ b0, Xf @ b_prev),
        "backtest": pd.DataFrame(lignes).set_index("Fenêtre") if lignes else pd.DataFrame(),
        "n_tests": len(tests),
    }
