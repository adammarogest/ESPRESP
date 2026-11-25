"""
Étude statistique de l'historique de la courbe (ténors BKAM).

Tout est recalculé à partir de la base SQLite : si la base change (nouvelle courbe
téléchargée), les résultats changent aussi. Les calculs sont mis en cache par
(période, seuil, exclusion, version de la base) pour que l'interface reste fluide.

Conventions
- Variations quotidiennes en points de base, entre deux dates consécutives de la base.
- Annualisation par le nombre d'observations par an constaté sur la période
  (≈ 250 jours de cotation), et non par une constante fixe.
- Kurtosis = excès de kurtosis (0 pour une loi normale).
"""

from __future__ import annotations

import warnings
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy import stats as sps
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import adfuller, kpss

import base_courbe as db

TENORS = db.TENOR_COLS

# Avertissements de statsmodels (p-value KPSS hors table, évolution d'API) : sans effet sur les
# résultats. Déclarés après l'import de statsmodels, qui installe ses propres filtres.
warnings.filterwarnings("ignore", message=r".*(adfuller|kpss) currently returns")
warnings.filterwarnings("ignore", message=r"The test statistic is outside of the range")
FACTEURS = ["Niveau", "Pente", "Convexité"]
Z_99 = sps.norm.ppf(0.99)


# ---------------------------------------------------------------------------
# Données
# ---------------------------------------------------------------------------

def charger(conn, debut=None, fin=None) -> pd.DataFrame:
    return db.lire_historique(conn, debut, fin).set_index("Date")[TENORS]


def facteur_annualisation(index: pd.DatetimeIndex) -> float:
    """Observations par an constatées sur la période (252 si la période est trop courte)."""
    if len(index) < 20 or (index[-1] - index[0]).days < 90:
        return 252.0
    return (len(index) - 1) / ((index[-1] - index[0]).days / 365.25)


def variations(niv: pd.DataFrame) -> pd.DataFrame:
    """Variations quotidiennes en points de base."""
    return niv.diff().iloc[1:] * 100


# ---------------------------------------------------------------------------
# Cas aberrants
# ---------------------------------------------------------------------------

def echelle_aberrants(var: pd.DataFrame) -> pd.Series:
    """σw : écart-type des variations winsorisées à 1 % / 99 %, par ténor."""
    bas, haut = var.quantile(0.01), var.quantile(0.99)
    return var.clip(bas, haut, axis=1).std().replace(0, np.nan)


def scores_aberrants(var: pd.DataFrame) -> pd.DataFrame:
    """
    Score z = (x - médiane) / σw, où σw est l'écart-type des variations winsorisées à 1 % / 99 %.
    - La winsorisation empêche les cas extrêmes de gonfler l'échelle (contrairement au z classique).
    - La MAD ne convient pas ici : 30 à 80 % des variations quotidiennes BKAM sont nulles,
      ce qui l'écrase vers zéro et ferait passer presque tout pour aberrant.
    """
    return (var - var.median()) / echelle_aberrants(var)


def liste_aberrants(niv: pd.DataFrame, var: pd.DataFrame, z: pd.DataFrame,
                    masque: pd.DataFrame) -> pd.DataFrame:
    """
    Un cas par (date, ténor). « Retour le lendemain » : la variation du jour suivant est de
    sens opposé et efface au moins 70 % du mouvement — signe possible d'une erreur de donnée.
    """
    lignes = []
    suivant = var.shift(-1)
    precedent = niv.shift(1)
    for t in TENORS:
        for d in var.index[masque[t].fillna(False).to_numpy()]:
            v, s = var.at[d, t], suivant.at[d, t]
            retour = bool(pd.notna(s) and np.sign(s) == -np.sign(v) and abs(s) >= 0.7 * abs(v))
            lignes.append({"Date": d, "Ténor": t, "Variation (pb)": v, "Score z": z.at[d, t],
                           "Niveau veille (%)": precedent.at[d, t], "Niveau (%)": niv.at[d, t],
                           "Retour le lendemain": retour})
    df = pd.DataFrame(lignes, columns=["Date", "Ténor", "Variation (pb)", "Score z",
                                       "Niveau veille (%)", "Niveau (%)", "Retour le lendemain"])
    if df.empty:
        return df
    return df.reindex(df["Score z"].abs().sort_values(ascending=False).index).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Statistiques descriptives
# ---------------------------------------------------------------------------

def stats_descriptives(niv: pd.DataFrame, var: pd.DataFrame, k: float) -> pd.DataFrame:
    return pd.DataFrame({
        "Niveau moyen (%)": niv.mean(),
        "Niveau min (%)": niv.min(),
        "Niveau max (%)": niv.max(),
        "Dernier (%)": niv.iloc[-1],
        "Variation période (pb)": (niv.iloc[-1] - niv.iloc[0]) * 100,
        "Moyenne ann. (pb)": var.mean() * k,
        "Volatilité ann. (pb)": var.std() * np.sqrt(k),
        "Volatilité quot. (pb)": var.std(),
        "Skewness": var.skew(),
        "Kurtosis": var.kurt(),
        "Hausse max (pb)": var.max(),
        "Baisse max (pb)": var.min(),
    }).rename_axis("Ténor")


# ---------------------------------------------------------------------------
# Marche aléatoire
# ---------------------------------------------------------------------------

def variance_ratio(r: np.ndarray, q: int) -> tuple[float, float, float]:
    """
    Test du ratio de variance de Lo-MacKinlay (1988), robuste à l'hétéroscédasticité.
    VR(q) = Var(somme de q variations) / (q × Var(1 variation)) ; = 1 sous marche aléatoire.
    Renvoie (VR, z*, p-value bilatérale).
    """
    r = np.asarray(r, dtype=float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < 5 * q:
        return np.nan, np.nan, np.nan
    e = r - r.mean()
    s1 = (e ** 2).sum() / (n - 1)
    if s1 == 0:
        return np.nan, np.nan, np.nan
    rq = np.convolve(r, np.ones(q), mode="valid")                 # sommes glissantes
    m = q * (n - q + 1) * (1 - q / n)
    vr = ((rq - q * r.mean()) ** 2).sum() / m / s1
    e2 = e ** 2
    denom = e2.sum() ** 2
    theta = sum((2 * (q - j) / q) ** 2 * n * (e2[j:] * e2[:-j]).sum() / denom for j in range(1, q))
    z = np.sqrt(n) * (vr - 1) / np.sqrt(theta)     # z*(q) de Lo-MacKinlay
    return vr, z, 2 * (1 - sps.norm.cdf(abs(z)))


def tests_marche_aleatoire(niv: pd.DataFrame, var: pd.DataFrame) -> pd.DataFrame:
    """
    Par ténor :
    - ADF (niveaux)   H0 racine unitaire : ne pas rejeter est compatible avec une marche aléatoire
    - KPSS (niveaux)  H0 stationnarité : rejeter est compatible avec une marche aléatoire
    - Ljung-Box (variations, 10 retards) H0 pas d'autocorrélation des variations
    - Ratio de variance q = 5 et 10      H0 VR = 1
    Verdict au seuil de 5 % : ADF pour le niveau, ratios de variance pour les variations.
    - Retour à la moyenne         : ADF rejette la racine unitaire (niveau stationnaire)
    - Marche aléatoire            : racine unitaire et VR(5), VR(10) non significativement ≠ 1
    - Variations qui se corrigent : VR < 1 significatif (autocorrélation négative)
    - Variations persistantes     : VR > 1 significatif (tendances, autocorrélation positive)
    """
    lignes = []
    for t in TENORS:
        y, r = niv[t].dropna(), var[t].dropna()
        ligne = {"Ténor": t}
        if len(y) < 30 or y.std() == 0 or r.std() == 0:
            ligne["Verdict"] = "Non testable"
            lignes.append(ligne)
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            adf_stat, adf_p, *_ = adfuller(y.to_numpy(), regression="c", autolag="AIC")
            kpss_p = kpss(y.to_numpy(), regression="c", nlags="auto")[1]
            lb_p = float(acorr_ljungbox(r.to_numpy(), lags=[10], return_df=True)["lb_pvalue"].iloc[0])
        vr5, _, p5 = variance_ratio(r.to_numpy(), 5)
        vr10, _, p10 = variance_ratio(r.to_numpy(), 10)

        racine_unitaire = adf_p >= 0.05
        # Ljung-Box est indicatif : sur des milliers d'observations, il rejette au moindre écart
        increments_libres = (np.isnan(p5) or p5 >= 0.05) and (np.isnan(p10) or p10 >= 0.05)
        if not racine_unitaire:
            verdict = "Retour à la moyenne"          # le niveau est stationnaire
        elif increments_libres:
            verdict = "Marche aléatoire"
        else:
            vr_rejete = vr5 if (not np.isnan(p5) and p5 < 0.05) else vr10
            verdict = "Variations qui se corrigent" if vr_rejete < 1 else "Variations persistantes"
        ligne.update({"ADF stat": adf_stat, "ADF p": adf_p, "KPSS p": kpss_p,
                      "Ljung-Box p": lb_p, "VR(5)": vr5, "VR(5) p": p5,
                      "VR(10)": vr10, "VR(10) p": p10, "Verdict": verdict})
        lignes.append(ligne)
    return pd.DataFrame(lignes).set_index("Ténor")


# ---------------------------------------------------------------------------
# Analyse en composantes principales
# ---------------------------------------------------------------------------

def acp(var: pd.DataFrame, k: float) -> dict | None:
    """
    ACP sur la matrice de covariance des variations quotidiennes (pb), lignes complètes.
    Signes normalisés : Niveau = hausse parallèle, Pente = pentification (long - court > 0),
    Convexité = extrémités en hausse par rapport au ventre (5 ans).
    """
    X = var.dropna()
    if len(X) < 30:
        return None
    C = np.cov(X.to_numpy(), rowvar=False)
    w, v = np.linalg.eigh(C)
    ordre = np.argsort(w)[::-1]
    w, v = np.clip(w[ordre], 0, None), v[:, ordre]

    i5 = TENORS.index("5ans")
    if v[:, 0].sum() < 0:
        v[:, 0] *= -1
    if v[-1, 1] - v[0, 1] < 0:
        v[:, 1] *= -1
    if (v[0, 2] + v[-1, 2]) / 2 - v[i5, 2] < 0:
        v[:, 2] *= -1

    part = w / w.sum() if w.sum() > 0 else w
    sig = np.sqrt(w[:3])
    facteurs = pd.DataFrame({
        "Part de variance": part[:3],
        "Part cumulée": np.cumsum(part)[:3],
        "Volatilité quot. (pb)": sig,
        "Volatilité ann. (pb)": sig * np.sqrt(k),
        "VaR 99 % 1 jour (pb)": Z_99 * sig,
    }, index=FACTEURS)
    chargements = pd.DataFrame(v[:, :3], index=TENORS, columns=FACTEURS)
    chocs = chargements * sig                       # mouvement de chaque ténor pour 1 écart-type
    scores = pd.DataFrame((X - X.mean()).to_numpy() @ v[:, :3], index=X.index, columns=FACTEURS)
    return {"facteurs": facteurs, "chargements": chargements, "chocs": chocs,
            "scores_cumules": scores.cumsum(), "parts": part, "n": len(X)}


# ---------------------------------------------------------------------------
# Résumé annuel
# ---------------------------------------------------------------------------

METRIQUES_ANNUELLES = {
    "Variation annuelle (pb)": "variation",
    "Volatilité annualisée (pb)": "vol",
    "Niveau moyen (%)": "niveau",
    "Skewness": "skew",
    "Kurtosis": "kurt",
}


def resume_annuel(niv: pd.DataFrame, var: pd.DataFrame, masque: pd.DataFrame,
                  td: pd.Series, k: float) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    synthese, par_metrique = [], {m: {} for m in METRIQUES_ANNUELLES.values()}
    for annee in sorted(set(niv.index.year)):
        n_a = niv[niv.index.year == annee]
        v_a = var[var.index.year == annee]
        avant = niv[niv.index.year < annee]
        base = avant.iloc[-1] if len(avant) else n_a.iloc[0]
        vol = v_a.std() * np.sqrt(k)

        par_metrique["variation"][annee] = (n_a.iloc[-1] - base) * 100
        par_metrique["vol"][annee] = vol
        par_metrique["niveau"][annee] = n_a.mean()
        par_metrique["skew"][annee] = v_a.skew()
        par_metrique["kurt"][annee] = v_a.kurt()

        a = acp(v_a, k) if len(v_a) >= 30 else None
        td_fin = td.get(n_a.index[-1], np.nan)
        td_debut = td.get(avant.index[-1], np.nan) if len(avant) else td.get(n_a.index[0], np.nan)
        synthese.append({
            "Année": annee,
            "Observations": len(n_a),
            "Taux directeur fin (%)": td_fin,
            "Δ taux directeur (pb)": (td_fin - td_debut) * 100 if pd.notna(td_fin) and pd.notna(td_debut) else np.nan,
            "Part niveau": a["parts"][0] if a else np.nan,
            "Part pente": a["parts"][1] if a else np.nan,
            "Part convexité": a["parts"][2] if a else np.nan,
            "Cas aberrants": int(masque[masque.index.year == annee].to_numpy().sum()),
            "Ténor le plus volatil": vol.idxmax() if vol.notna().any() else "—",
            "Volatilité max (pb)": vol.max(),
        })
    tables = {nom: pd.DataFrame(par_metrique[cle]).T.rename_axis("Année")[TENORS]
              for nom, cle in METRIQUES_ANNUELLES.items()}
    return pd.DataFrame(synthese), tables


# ---------------------------------------------------------------------------
# Point d'entrée unique, mis en cache
# ---------------------------------------------------------------------------

@lru_cache(maxsize=12)
def analyse(debut: str | None, fin: str | None, seuil: float, exclure: bool, version: int) -> dict:
    """
    Calcule toute l'étude. `version` (= version_base()) invalide le cache quand la base change.
    Les objets renvoyés sont partagés : ne pas les modifier.
    """
    with db.connexion() as conn:
        niv = charger(conn, debut, fin)
        td = db.taux_directeur_journalier(conn, niv.index) if len(niv) else pd.Series(dtype=float)
    if len(niv) < 30:
        return {"vide": True, "n": len(niv)}

    var = variations(niv)
    z = scores_aberrants(var)
    masque = z.abs() > seuil
    echelle = echelle_aberrants(var)
    bornes = (var.median() - seuil * echelle, var.median() + seuil * echelle)
    var_stats = var.mask(masque) if exclure else var
    k = facteur_annualisation(niv.index)
    synthese, par_tenor = resume_annuel(niv, var_stats, masque, td, k)

    return {
        "vide": False, "niv": niv, "var": var, "var_stats": var_stats, "z": z, "masque": masque,
        "k": k, "td": td, "bornes": bornes,
        "stats": stats_descriptives(niv, var_stats, k),
        "correlations": var_stats.corr(),
        "tests": tests_marche_aleatoire(niv, var_stats),
        "acp": acp(var_stats, k),
        "aberrants": liste_aberrants(niv, var, z, masque),
        "synthese": synthese,
        "par_tenor": par_tenor,
        "pentes": etude_pentes(niv),
    }


# ---------------------------------------------------------------------------
# Pentification de la courbe
# ---------------------------------------------------------------------------

PENTES = {"10 ans − 2 ans": ("10ans", "2ans"), "10 ans − 5 ans": ("10ans", "5ans")}
FENETRES_PENTE = {"1A": 1, "3A": 3, "5A": 5, "10A": 10}


def series_pentes(niv: pd.DataFrame) -> pd.DataFrame:
    """Pentes en points de base."""
    return pd.DataFrame({nom: (niv[a] - niv[b]) * 100 for nom, (a, b) in PENTES.items()})


def etude_pentes(niv: pd.DataFrame) -> dict:
    """
    Pente actuelle (dernière date de la période) comparée à son historique sur la période :
    moyenne, dispersion, z-score, percentile, extrêmes, variations récentes, moyennes par fenêtre
    et résumé annuel.
    """
    p = series_pentes(niv)
    fin = p.index[-1]
    avant_annee = p[p.index.year < fin.year]

    def variation(s, n):
        return s.iloc[-1] - s.iloc[-1 - n] if len(s) > n else np.nan

    lignes = []
    for nom in p.columns:
        s = p[nom].dropna()
        act, moy, et = s.iloc[-1], s.mean(), s.std()
        lignes.append({
            "Pente": nom, "Actuelle (pb)": act, "Moyenne (pb)": moy, "Médiane (pb)": s.median(),
            "Écart-type (pb)": et, "Écart à la moyenne (pb)": act - moy,
            "z-score": (act - moy) / et if et > 0 else np.nan,
            "Percentile": (s <= act).mean(),
            "Min (pb)": s.min(), "Date min": s.idxmin(), "Max (pb)": s.max(), "Date max": s.idxmax(),
            "Var. 1 mois (pb)": variation(s, 21), "Var. 3 mois (pb)": variation(s, 63),
            "Var. YTD (pb)": act - avant_annee[nom].iloc[-1] if len(avant_annee) else np.nan,
        })
    synthese = pd.DataFrame(lignes).set_index("Pente")

    # Moyennes par fenêtre se terminant à la fin de la période (— si la période est trop courte)
    fen = {}
    for lib, ans in FENETRES_PENTE.items():
        debut = fin - pd.DateOffset(years=ans)
        fen[lib] = p[p.index > debut].mean() if p.index[0] <= debut + pd.Timedelta(days=7) \
            else pd.Series(np.nan, index=p.columns)
    fen["Période"] = p.mean()
    moyennes = pd.DataFrame(fen)

    # Résumé annuel
    g = p.groupby(p.index.year)
    annuel = pd.concat({"Moyenne": g.mean(), "Fin d'année": g.last(), "Min": g.min(), "Max": g.max()}, axis=1)
    annuel.index.name = "Année"
    return {"series": p, "synthese": synthese, "moyennes": moyennes, "annuel": annuel, "fin": fin}
