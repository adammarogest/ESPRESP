"""
Valeur relative des ténors : sensibilités, KRD, rendement attendu décomposé et points morts.

Chaque ténor est traité comme un BDT émis au pair aujourd'hui (coupon = taux du ténor) :
- ≤ 1 an  : convention monétaire, prix = 100 (1 + c n/360) / (1 + y n/360), n en jours
- > 1 an  : coupon annuel, actualisation actuarielle au taux du ténor (convention VL des BDT in fine)

Rendement attendu sur un horizon H (en % du prix) :
- Carry          : coupon couru sur H = y × H
- Glissement     : le titre vieillit (maturité T − H) ; à courbe inchangée, son taux devient y(T − H)
                   → effet prix ≈ − S(T − H) × [y(T − H) − y(T)]
- Effet courbe   : écart entre la courbe prévue et la courbe actuelle à la maturité T − H
                   → − S × Δy + ½ C × Δy²
Point mort : hausse de taux (pb) qui annule carry + glissement sur l'horizon.

KRD : sensibilité du prix à un choc de 1 pb sur chaque point clé de la courbe zéro-coupon
(obtenue par bootstrap de la courbe des ténors, interpolation linéaire entre points clés).
Avec la convention VL (actualisation au seul taux de l'échéance), le risque d'un BDT in fine
est entièrement porté par son point d'échéance ; les KRD montrent où il se situe économiquement.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import base_courbe as db

TENORS = db.TENOR_COLS
JOURS = np.array([db.TENORS[t] for t in TENORS])
MATURITES = JOURS / 365.25
HORIZONS_RV = {"3 mois": 0.25, "6 mois": 0.5, "1 an": 1.0}
CRITERES = {"rendement": "Rendement attendu", "point_mort": "Point mort / volatilité",
            "ratio": "Rendement / risque"}


# ---------------------------------------------------------------------------
# Prix et sensibilités
# ---------------------------------------------------------------------------

def prix(y: float, T: float, coupon: float) -> float:
    """Prix (base 100) d'un titre de maturité T ans, coupon et taux en %."""
    y, c = y / 100, coupon / 100
    if T <= 1.0 + 1e-9:
        n = T * 365.25
        return 100 * (1 + c * n / 360) / (1 + y * n / 360)
    k = int(round(T))
    t = np.arange(1, k + 1)
    flux = np.full(k, 100 * c)
    flux[-1] += 100
    return float((flux / (1 + y) ** t).sum())


def sensibilite(y: float, T: float, coupon: float | None = None, dy: float = 0.01) -> tuple[float, float]:
    """Sensibilité (duration modifiée, en années) et convexité, par différences finies (dy en %)."""
    c = y if coupon is None else coupon
    p0, pu, pd_ = prix(y, T, c), prix(y + dy, T, c), prix(y - dy, T, c)
    d = dy / 100
    return float((pd_ - pu) / (2 * d * p0)), float((pu + pd_ - 2 * p0) / (d ** 2 * p0))


def interpoler(courbe: pd.Series, x: float) -> float:
    """Taux à la maturité x (années), interpolation linéaire entre ténors, plat aux extrémités."""
    return float(np.interp(x, MATURITES, courbe[TENORS].to_numpy(dtype=float)))


def tableau_sensibilites(courbe: pd.Series) -> pd.DataFrame:
    lignes = []
    for t, T in zip(TENORS, MATURITES):
        y = float(courbe[t])
        T_calc = T if T <= 1 else round(T)
        s, c = sensibilite(y, T_calc)
        lignes.append({"Ténor": t, "Taux (%)": y, "Sensibilité": s, "Convexité": c,
                       "DV01 (MAD / 1 M)": s * 1e6 * 1e-4})
    return pd.DataFrame(lignes).set_index("Ténor")


# ---------------------------------------------------------------------------
# Courbe zéro-coupon et KRD
# ---------------------------------------------------------------------------

def courbe_zero(courbe: pd.Series) -> pd.Series:
    """Taux zéro-coupon actuariels (%) aux points clés, par bootstrap de la courbe des ténors."""
    zeros = {}
    for t, j in zip(TENORS, JOURS):
        if j <= 366:                                   # monétaire -> actuariel
            y = courbe[t] / 100
            zeros[t] = ((1 + y * j / 360) ** (365.25 / j) - 1) * 100
    # Facteurs d'actualisation annuels : coupons au pair interpolés sur la courbe
    annees = np.arange(1, int(round(MATURITES[-1])) + 1)
    df = {}
    for n in annees:
        c = interpoler(courbe, n) / 100
        if n == 1:
            df[n] = 1 / (1 + c)
        else:
            df[n] = (1 - c * sum(df[k] for k in range(1, n))) / (1 + c)
    for t, T in zip(TENORS, MATURITES):
        if t not in zeros:
            n = int(round(T))
            zeros[t] = (df[n] ** (-1 / n) - 1) * 100
    return pd.Series(zeros)[TENORS]


def _prix_zero(z_cles: np.ndarray, T: float, coupon: float) -> float:
    """Prix d'un titre au pair actualisé sur la courbe zéro (interpolée linéairement entre clés)."""
    if T <= 1.0 + 1e-9:
        n = T * 365.25
        flux, dates = np.array([100 * (1 + coupon / 100 * n / 360)]), np.array([T])
    else:
        k = int(round(T))
        dates = np.arange(1, k + 1, dtype=float)
        flux = np.full(k, coupon)
        flux[-1] += 100
    z = np.interp(dates, MATURITES, z_cles) / 100
    return float((flux / (1 + z) ** dates).sum())


def matrice_krd(courbe: pd.Series, choc_pb: float = 1.0) -> pd.DataFrame:
    """KRD (années) : lignes = titre au pair de chaque ténor, colonnes = point clé choqué."""
    z = courbe_zero(courbe).to_numpy(dtype=float)
    d = choc_pb / 100
    lignes = {}
    for t, T in zip(TENORS, MATURITES):
        T_calc = T if T <= 1 else round(T)
        c = float(courbe[t])
        p0 = _prix_zero(z, T_calc, c)
        krd = []
        for j in range(len(TENORS)):
            zu, zd = z.copy(), z.copy()
            zu[j] += d
            zd[j] -= d
            krd.append((_prix_zero(zd, T_calc, c) - _prix_zero(zu, T_calc, c)) / (2 * p0 * d / 100))
        lignes[t] = krd
    return pd.DataFrame(lignes, index=TENORS).T


# ---------------------------------------------------------------------------
# Rendement attendu, points morts et classement
# ---------------------------------------------------------------------------

def rendement_attendu(courbe: pd.Series, courbe_prevue: pd.Series | None, H: float,
                      vol_quot_pb: pd.Series, obs_par_an: float, critere: str = "point_mort") -> pd.DataFrame:
    """
    courbe, courbe_prevue : taux des ténors en % (courbe_prevue = None -> courbe inchangée)
    vol_quot_pb : volatilité quotidienne historique des variations (pb) par ténor
    Rendements en % du prix sur l'horizon H (années).
    """
    lignes = []
    for t, T in zip(TENORS, MATURITES):
        y = float(courbe[t])
        T_calc = T if T <= 1 else round(T)
        vol_h = float(vol_quot_pb.get(t, np.nan)) * np.sqrt(H * obs_par_an)
        if T_calc <= H + 1e-9:
            # Le titre arrive à échéance avant l'horizon : réinvesti au même taux, sans risque de prix
            carry, roll, effet, s_h = y * H, 0.0, 0.0, np.nan
        else:
            reste = T_calc - H
            y_roll = interpoler(courbe, reste)
            s_h, c_h = sensibilite(y, reste, coupon=y)
            carry = y * H
            roll = -s_h * (y_roll - y)
            if courbe_prevue is not None:
                dy = interpoler(courbe_prevue, reste) - y_roll
                effet = -s_h * dy + 0.5 * c_h * (dy / 100) ** 2 * 100
            else:
                effet = 0.0
        total = carry + roll + effet
        pm = (carry + roll) / s_h * 100 if s_h and not np.isnan(s_h) else np.nan
        risque = s_h * vol_h / 100 if s_h and not np.isnan(s_h) else np.nan
        lignes.append({
            "Ténor": t, "Taux (%)": y, "Carry (%)": carry, "Glissement (%)": roll,
            "Effet courbe (%)": effet, "Rendement attendu (%)": total,
            "Rendement annualisé (%)": total / H,
            "Point mort (pb)": pm, "Volatilité horizon (pb)": vol_h,
            "Point mort / vol": pm / vol_h if vol_h and not np.isnan(pm) else np.nan,
            "Rendement / risque": total / risque if risque and risque > 0 else np.nan,
        })
    df = pd.DataFrame(lignes).set_index("Ténor")
    col = {"rendement": "Rendement attendu (%)", "point_mort": "Point mort / vol",
           "ratio": "Rendement / risque"}[critere]
    # Titres sans risque de prix sur l'horizon : classés après les autres pour les critères de risque
    cle = df[col].fillna(-np.inf) if critere != "rendement" else df[col]
    df["Rang"] = cle.rank(ascending=False, method="min").astype(int)
    return df.sort_values("Rang")
