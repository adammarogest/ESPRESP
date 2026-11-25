"""
Pentification de la courbe BDT : diagnostic, réaction historique après les conseils de BAM
et simulation de positionnement obligataire.

Trois blocs, tous calculés à partir de histo_courbe.db (ténors quotidiens + décisions de politique
monétaire) et de la base titres (fiches des lignes simulées) :

1. etat()        pente actuelle vs historique, concentration par segment, pentification depuis le
                 1er janvier, analogues historiques et ce qui a suivi.
2. evenements()  étude d'événements sur les conseils de BAM : pour chaque décision (hausse, statu quo,
                 baisse) et selon que la courbe était pentue ou non la veille, variations du court, du
                 long et de la pente à 1, 3, 6 et 12 mois, et mécanisme (court ↑, long ↓, …).
3. positionnement()  rendement à l'horizon de lignes BDT (portage, glissement, effet scénario) sous
                 plusieurs scénarios de courbe : stable, hausse du court, hausse du long, historique
                 post-conseil, pentes historiques AR(1), Nelson-Siegel, forward implicite.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

import base_courbe as db
import forward as F
import prevision as PV

TENORS = db.TENOR_COLS
ANS = np.array([db.TENORS[t] / 365.25 for t in TENORS])

PENTES = {  # libellé -> (ténor long, ténor court)
    "10a − 52s": ("10ans", "52s"), "10a − 2a": ("10ans", "2ans"), "30a − 13s": ("30ans", "13s"),
    "2a − 13s": ("2ans", "13s"), "5a − 2a": ("5ans", "2ans"), "30a − 10a": ("30ans", "10ans"),
}
SEGMENTS = [("13s", "52s"), ("52s", "2ans"), ("2ans", "5ans"), ("5ans", "10ans"),
            ("10ans", "15ans"), ("15ans", "20ans"), ("20ans", "30ans")]
NOMS_COURTS = {"13s": "13s", "26s": "26s", "52s": "52s", "2ans": "2a", "5ans": "5a", "10ans": "10a",
               "15ans": "15a", "20ans": "20a", "30ans": "30a"}
SEUILS = {"Moyenne": None, "60e centile": 0.60, "75e centile": 0.75}
FENETRES = {"Tout": None, "10A": 10, "5A": 5}
HORIZONS = {k: v[1] for k, v in F.HORIZONS.items()}          # libellé -> jours calendaires
H_OBS = {k: v[0] for k, v in F.HORIZONS.items()}             # libellé -> nb d'observations
DECISIONS = ["Hausse", "Statu quo", "Baisse"]
NEUTRE_PB = 5.0                                                # |Δ pente| sous ce seuil : pente stable
LIGNES_DEFAUT = ["201949", "201808", "201646", "201409"]


def nom_segment(a: str, b: str) -> str:
    return f"{NOMS_COURTS[a]} → {NOMS_COURTS[b]}"


# ---------------------------------------------------------------------------
# Données
# ---------------------------------------------------------------------------

def _niv_td(version: int) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    niv, td = PV.donnees(version)
    with db.connexion() as conn:
        conseils = db.lire_taux_directeur(conn)
    return niv, td, conseils


def _fenetre(idx: pd.DatetimeIndex, t0: pd.Timestamp, fenetre: str) -> np.ndarray:
    ans = FENETRES.get(fenetre)
    if ans is None:
        return np.asarray(idx <= t0)
    return np.asarray((idx > t0 - pd.DateOffset(years=ans)) & (idx <= t0))


def _seuil(serie: pd.Series, seuil: str) -> float:
    q = SEUILS.get(seuil)
    return float(serie.mean()) if q is None else float(serie.quantile(q))


def _a_date(niv: pd.DataFrame, d: pd.Timestamp) -> pd.Series | None:
    """Dernière courbe connue à la date d (None si d dépasse la base)."""
    if d > niv.index[-1]:
        return None
    pos = niv.index.searchsorted(d, side="right") - 1
    return niv.iloc[pos] if pos >= 0 else None


def serie_pente(niv: pd.DataFrame, pente: str) -> pd.Series:
    a, b = PENTES[pente]
    return (niv[a] - niv[b]) * 100


def segments(niv: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({nom_segment(a, b): (niv[b] - niv[a]) * 100 for a, b in SEGMENTS})


def segments_de(pente: str) -> list[str]:
    """Segments qui composent la pente de référence."""
    long_, court = PENTES[pente]
    i0, i1 = TENORS.index(court), TENORS.index(long_)
    return [nom_segment(a, b) for a, b in SEGMENTS if TENORS.index(a) >= i0 and TENORS.index(b) <= i1]


def mecanisme(d_court: float, d_long: float) -> str:
    """Nature du mouvement de pente (variations en pb)."""
    if any(pd.isna(x) for x in (d_court, d_long)):
        return "—"
    dp, niveau = d_long - d_court, (d_long + d_court) / 2
    if abs(dp) < NEUTRE_PB:
        return "Pente stable"
    if dp < 0:
        return "Aplatissement : court ↑" if niveau >= 0 else "Aplatissement : long ↓"
    return "Pentification : long ↑" if niveau >= 0 else "Pentification : court ↓"


MECANISMES = ["Aplatissement : court ↑", "Aplatissement : long ↓", "Pente stable",
              "Pentification : long ↑", "Pentification : court ↓"]


# ---------------------------------------------------------------------------
# 1. Pente actuelle, concentration, analogues
# ---------------------------------------------------------------------------

@lru_cache(maxsize=8)
def etat(ref: str | None, pente: str, seuil: str, fenetre: str, version: int) -> dict:
    niv, td, _ = _niv_td(version)
    t0 = PV.date_reference(niv, ref)
    m = _fenetre(niv.index, t0, fenetre)
    h = niv.loc[m]
    p = serie_pente(niv, pente)
    ph = p.loc[m]
    p0 = float(p.loc[t0])
    s_seuil = _seuil(ph, seuil)

    def stats(s: pd.Series, v0: float) -> dict:
        return {"Actuelle": v0, "Moyenne": s.mean(), "Médiane": s.median(), "Écart-type": s.std(),
                "Z-score": (v0 - s.mean()) / s.std() if s.std() else np.nan,
                "Centile": float((s < v0).mean()), "Min": s.min(), "Max": s.max()}

    # Toutes les pentes de référence
    tab_pentes = pd.DataFrame({nom: stats(serie_pente(h, nom), float(serie_pente(niv, nom).loc[t0]))
                               for nom in PENTES}).T

    # Concentration par segment
    seg = segments(niv)
    seg_h = seg.loc[m]
    s0 = seg.loc[t0]
    larg = np.array([ANS[TENORS.index(b)] - ANS[TENORS.index(a)] for a, b in SEGMENTS])
    dans = set(segments_de(pente))
    exces = s0 - seg_h.mean()
    exces_ref = exces[[c for c in seg.columns if c in dans]].sum()
    conc = pd.DataFrame({
        "Actuelle": s0, "Moyenne": seg_h.mean(), "Écart-type": seg_h.std(),
        "Écart": exces,
        "Z-score": exces / seg_h.std(),
        "Centile": [(seg_h[c] < s0[c]).mean() for c in seg.columns],
        "pb/an actuel": s0.to_numpy() / larg, "pb/an moyen": seg_h.mean().to_numpy() / larg,
        "Part de la courbe": s0 / s0.sum(), "Part moyenne": (seg_h.div(seg_h.sum(axis=1), axis=0)).mean(),
        "Part de l'excès": [exces[c] / exces_ref if c in dans and exces_ref else np.nan for c in seg.columns],
        "Dans la pente": [c in dans for c in seg.columns],
    })

    # Facteurs Nelson-Siegel (niveau, pente, courbure) : position actuelle dans l'historique
    lam = PV.ns_lambda(h)
    betas, _ = PV.ns_ajuster(niv, lam)
    bh = betas.loc[m]
    b0 = betas.loc[t0]
    ns = pd.DataFrame({"Actuel": b0, "Moyenne": bh.mean(), "Écart-type": bh.std(),
                       "Centile": [(bh[c] < b0[c]).mean() for c in betas.columns]})
    ns["Z-score"] = (ns["Actuel"] - ns["Moyenne"]) / ns["Écart-type"]

    # Pentification depuis le 1er janvier
    avant = niv.index[niv.index < pd.Timestamp(t0.year, 1, 1)]
    t_debut = avant[-1] if len(avant) else niv.index[0]
    c_debut, c0 = niv.loc[t_debut], niv.loc[t0]
    long_, court = PENTES[pente]
    d_court, d_long = (c0[court] - c_debut[court]) * 100, (c0[long_] - c_debut[long_]) * 100
    ytd = {"t_debut": t_debut, "courbe_debut": c_debut, "delta": (c0 - c_debut) * 100,
           "d_court": d_court, "d_long": d_long, "d_pente": d_long - d_court,
           "mecanisme": mecanisme(d_court, d_long),
           "pente_debut": float(p.loc[t_debut])}

    # Écart 13s − taux directeur : ce que le marché anticipe du prochain conseil
    ecart_td = (niv["13s"] - td) * 100
    eh = ecart_td.loc[m]
    td0 = float(td.loc[t0])
    marche = {"td": td0, "ecart": float(ecart_td.loc[t0]), "moyenne": float(eh.mean()),
              "centile": float((eh < ecart_td.loc[t0]).mean())}

    # Analogues : formes de courbe les plus proches (pentes de segment standardisées)
    an = analogues(niv, td, seg, p, t0, m, s_seuil)

    return {"t0": t0, "pente": pente, "p0": p0, "seuil": s_seuil, "serie": p, "masque": m,
            "td": td, "stats": stats(ph, p0), "tab_pentes": tab_pentes, "conc": conc, "ns": ns,
            "lambda": lam, "ytd": ytd, "marche": marche, "analogues": an, "courbe": niv.loc[t0],
            "n_obs": int(m.sum()), "debut": niv.index[m][0]}


def analogues(niv, td, seg, p, t0, masque, seuil, n=8, ecart_jours=120) -> dict:
    """Dates passées dont la forme de courbe ressemble le plus à celle de t0, et ce qui a suivi."""
    hist = seg.loc[masque & (seg.index <= t0 - pd.Timedelta(days=180))]
    if hist.empty:
        return {"table": pd.DataFrame(), "chemins": {}, "courbes": {}}
    sd = seg.loc[masque].std().replace(0, np.nan)
    dist = np.sqrt((((hist - seg.loc[t0]) / sd) ** 2).mean(axis=1)).sort_values()
    choisis = []
    for d in dist.index:
        if all(abs((d - c).days) >= ecart_jours for c in choisis):
            choisis.append(d)
        if len(choisis) == n:
            break
    lignes, chemins, courbes = [], {}, {}
    grille = np.arange(0, 366, 7)
    for d in choisis:
        c = niv.loc[d]
        r = {"Date": d, "Distance": float(dist.loc[d]), "Pente": float(p.loc[d]),
             "Taux directeur": float(td.loc[d]), "2a": c["2ans"], "10a": c["10ans"]}
        for lib, j in (("3m", 91), ("6m", 182), ("12m", 365)):
            cj = _a_date(niv, d + pd.Timedelta(days=j))
            r[f"Δ pente {lib}"] = (float(p.asof(d + pd.Timedelta(days=j))) - r["Pente"]) if cj is not None else np.nan
            r[f"Δ 52s {lib}"] = (cj["52s"] - c["52s"]) * 100 if cj is not None else np.nan
            r[f"Δ 10a {lib}"] = (cj["10ans"] - c["10ans"]) * 100 if cj is not None else np.nan
        cj = _a_date(niv, d + pd.Timedelta(days=182))
        td6 = td.asof(d + pd.Timedelta(days=182)) if cj is not None else np.nan
        r["Δ TD 6m"] = (td6 - r["Taux directeur"]) * 100 if cj is not None else np.nan
        lignes.append(r)
        fin = d + pd.Timedelta(days=365)
        s = p.loc[(p.index >= d) & (p.index <= min(fin, p.index[-1]))]
        jours = (s.index - d).days.to_numpy()
        chemins[d] = pd.Series(s.to_numpy() - r["Pente"], index=jours)
        courbes[d] = c
    table = pd.DataFrame(lignes).set_index("Date")
    return {"table": table, "chemins": chemins, "courbes": courbes, "grille": grille}


# ---------------------------------------------------------------------------
# 2. Étude d'événements : conseils de BAM
# ---------------------------------------------------------------------------

@lru_cache(maxsize=8)
def evenements(pente: str, seuil: str, fenetre: str, version: int) -> dict:
    niv, td, conseils = _niv_td(version)
    t_fin = niv.index[-1]
    p = serie_pente(niv, pente)
    m = _fenetre(niv.index, t_fin, fenetre)
    s_seuil = _seuil(p.loc[m], seuil)
    long_, court = PENTES[pente]

    conseils = conseils.sort_values("date").reset_index(drop=True)
    lignes, chemins = [], {}
    grille = np.arange(0, 366, 7)
    for i in range(1, len(conseils)):
        dc, taux, prec = conseils.loc[i, "date"], conseils.loc[i, "taux"], conseils.loc[i - 1, "taux"]
        avant = niv.index[niv.index < dc]
        if not len(avant) or avant[-1] < dc - pd.Timedelta(days=10):
            continue
        t0 = avant[-1]
        c0 = niv.loc[t0]
        decision = "Hausse" if taux > prec + 1e-9 else "Baisse" if taux < prec - 1e-9 else "Statu quo"
        p0 = float(p.loc[t0])
        suivants = conseils.loc[i + 1:]
        r = {"Conseil": dc, "Décision": decision, "Variation TD (pb)": (taux - prec) * 100, "Taux directeur": taux,
             "Veille": t0, "Pente veille": p0, "Pentue": p0 >= s_seuil,
             "Court veille": c0[court], "Long veille": c0[long_]}
        for lib, j in HORIZONS.items():
            cj = _a_date(niv, t0 + pd.Timedelta(days=j))
            if cj is None:
                r.update({f"Δ court {lib}": np.nan, f"Δ long {lib}": np.nan, f"Δ pente {lib}": np.nan,
                          f"Mécanisme {lib}": "—", f"Conseils suivants {lib}": ""})
                for t in TENORS:
                    r[f"{t} {lib}"] = np.nan
                continue
            dco, dlo = (cj[court] - c0[court]) * 100, (cj[long_] - c0[long_]) * 100
            r[f"Δ court {lib}"], r[f"Δ long {lib}"], r[f"Δ pente {lib}"] = dco, dlo, dlo - dco
            r[f"Mécanisme {lib}"] = mecanisme(dco, dlo)
            for t in TENORS:
                r[f"{t} {lib}"] = (cj[t] - c0[t]) * 100
            dans = suivants[suivants["date"] <= t0 + pd.Timedelta(days=j)]
            vars_ = (dans["taux"].diff().fillna(dans["taux"].iloc[0] - taux if len(dans) else 0) * 100) if len(dans) else []
            r[f"Conseils suivants {lib}"] = ", ".join("SQ" if abs(v) < 1e-9 else f"{v:+.0f}" for v in vars_) \
                if len(dans) else ""
        lignes.append(r)
        # Chemin moyen en temps d'événement (pb depuis la veille)
        fin = min(t0 + pd.Timedelta(days=365), t_fin)
        bloc = niv.loc[(niv.index >= t0) & (niv.index <= fin)]
        j = (bloc.index - t0).days.to_numpy()
        ch = pd.DataFrame({"court": (bloc[court] - c0[court]).to_numpy() * 100,
                           "long": (bloc[long_] - c0[long_]).to_numpy() * 100}, index=j)
        ch["pente"] = ch["long"] - ch["court"]
        ch = ch[~ch.index.duplicated()]
        ch = ch.reindex(ch.index.union(grille)).ffill().reindex(grille)
        ch.loc[ch.index > (fin - t0).days] = np.nan
        chemins[dc] = ch
    ev = pd.DataFrame(lignes)
    return {"ev": ev, "chemins": chemins, "grille": grille, "seuil": s_seuil, "pente": pente,
            "court": court, "long": long_}


def resume_evenements(E: dict, horizon: str) -> pd.DataFrame:
    """Moyennes par décision × état de la courbe à l'horizon donné."""
    ev = E["ev"]
    lignes = []
    for dec in DECISIONS:
        for pentue in (True, False):
            g = ev[(ev["Décision"] == dec) & (ev["Pentue"] == pentue)]
            g = g[g[f"Δ pente {horizon}"].notna()]
            n = len(g)
            mec = g[f"Mécanisme {horizon}"].value_counts()
            plat = g[f"Δ pente {horizon}"] < -NEUTRE_PB
            r = {"Décision": dec, "Courbe": "Pentue" if pentue else "Normale / plate", "N": n,
                 "Δ court": g[f"Δ court {horizon}"].mean() if n else np.nan,
                 "Δ long": g[f"Δ long {horizon}"].mean() if n else np.nan,
                 "Δ pente": g[f"Δ pente {horizon}"].mean() if n else np.nan,
                 "Δ pente médiane": g[f"Δ pente {horizon}"].median() if n else np.nan,
                 "% aplatissement": plat.mean() if n else np.nan}
            for k in MECANISMES:
                r[k] = mec.get(k, 0) / n if n else np.nan
            lignes.append(r)
    return pd.DataFrame(lignes)


def chemin_moyen(E: dict, decision: str, pentue: bool | None = True) -> pd.DataFrame:
    ev = E["ev"]
    g = ev[ev["Décision"] == decision]
    if pentue is not None:
        g = g[g["Pentue"] == pentue]
    if g.empty:
        return pd.DataFrame()
    blocs = [E["chemins"][d] for d in g["Conseil"]]
    pile = pd.concat(blocs, keys=range(len(blocs)))
    moy = pile.groupby(level=1).mean()
    moy["n"] = pile["pente"].notna().groupby(level=1).sum()
    return moy


MIN_CAS = 3


def delta_historique(E: dict, horizon: str, decision: str) -> tuple[pd.Series, int, str]:
    """
    Variation moyenne par ténor (pb) à l'horizon après un conseil de ce type, courbe pentue la veille.
    Moins de MIN_CAS cas : on élargit à toutes les courbes (la base retenue est renvoyée).
    """
    ev = E["ev"]
    cols = [f"{t} {horizon}" for t in TENORS]
    g = ev[ev["Décision"] == decision].dropna(subset=cols)
    pentu = g[g["Pentue"]]
    if len(pentu) >= MIN_CAS:
        g, base = pentu, "courbe pentue"
    else:
        base = "toutes courbes"
    if g.empty:
        return pd.Series(0.0, index=TENORS), 0, base
    return pd.Series(g[cols].mean().to_numpy(), index=TENORS), len(g), base


# ---------------------------------------------------------------------------
# 3. Scénarios de courbe et positionnement
# ---------------------------------------------------------------------------

def _choc_court(choc: float) -> pd.Series:
    w = {"13s": 1, "26s": 1, "52s": 1, "2ans": 0.6, "5ans": 0.2, "10ans": 0, "15ans": 0, "20ans": 0, "30ans": 0}
    return pd.Series({t: choc * w[t] for t in TENORS})


def _choc_long(choc: float) -> pd.Series:
    w = {"13s": 0, "26s": 0, "52s": 0, "2ans": 0, "5ans": 0.4, "10ans": 1, "15ans": 1, "20ans": 1, "30ans": 1}
    return pd.Series({t: choc * w[t] for t in TENORS})


def _pentes_ar1(niv: pd.DataFrame, t0: pd.Timestamp, masque: np.ndarray, h_obs: int) -> pd.Series:
    """Écarts entre ténors adjacents prévus par AR(1) à h observations, 13s ancré (statu quo de BAM)."""
    adj = niv.diff(axis=1).iloc[:, 1:]
    prev = {}
    for c in adj.columns:
        mu, phi = PV.ar1(adj.loc[masque, c].to_numpy())
        prev[c] = mu + phi ** h_obs * (adj.loc[t0, c] - mu)
    courbe = [niv.loc[t0, "13s"]]
    for c in adj.columns:
        courbe.append(courbe[-1] + prev[c])
    return pd.Series(courbe, index=TENORS)


@lru_cache(maxsize=8)
def scenarios(ref: str | None, pente: str, seuil: str, fenetre: str, horizon: str, choc: float,
              decision: str, version: int) -> dict:
    """Courbes des ténors à l'horizon (%) pour chaque scénario."""
    niv, td, _ = _niv_td(version)
    t0 = PV.date_reference(niv, ref)
    c0 = niv.loc[t0]
    m = _fenetre(niv.index, t0, fenetre)
    h_cal, h_obs = HORIZONS[horizon], H_OBS[horizon]
    E = evenements(pente, seuil, fenetre, version)
    d_hist, n_hist, base_hist = delta_historique(E, horizon, decision)
    fen_ns = fenetre if fenetre in PV.FENETRES else "Tout"
    ns = PV.analyse_ns(t0.strftime("%Y-%m-%d"), h_obs, fen_ns, None, None, version)
    fwd = pd.Series(F.courbe_implicite(c0.to_numpy()[None, :], h_cal)[0], index=TENORS)
    sc = {
        "Courbe stable": c0.copy(),
        f"Court terme +{choc:.0f} pb": c0 + _choc_court(choc) / 100,
        f"Long terme +{choc:.0f} pb": c0 + _choc_long(choc) / 100,
        f"Historique : {decision.lower()}": c0 + d_hist / 100,
        "Pentes historiques (AR1)": _pentes_ar1(niv, t0, m, h_obs),
        "Nelson-Siegel (AR1)": c0 + (ns["prevue"] - ns["ajustee"]).astype(float),
        "Forward implicite": fwd,
    }
    courbes = pd.DataFrame(sc)
    return {"t0": t0, "courbes": courbes, "actuelle": c0, "n_hist": n_hist, "base_hist": base_hist,
            "decision": decision, "lambda_ns": ns["lambda"], "h_cal": h_cal, "h_obs": h_obs,
            "delta": courbes.sub(c0, axis=0) * 100}


def _valoriser(code: str, valo: date, courbe_tenors: pd.Series) -> dict:
    import base_titre as BT
    import pricer as PR
    import swap as SW
    f = BT.fiche(code)
    if not f:
        raise ValueError(f"{code} absent de la base titres")
    if not f.get("valorisable"):
        raise ValueError(f"{code} non valorisable")
    courbe = PR.df_tenors({t: float(courbe_tenors[t]) for t in TENORS})
    return f, PR.valoriser(SW._parametres(f, valo), courbe)


def _coupons_entre(tab: pd.DataFrame, d0: date, d1: date) -> float:
    if tab is None or tab.empty:
        return 0.0
    dates = pd.to_datetime(tab["date_de_reglement"]).dt.date
    return float(tab.loc[(dates > d0) & (dates <= d1), "flux"].sum())


def _mtime_titres() -> int:
    import base_titre as BT
    try:
        return BT.CHEMIN.stat().st_mtime_ns
    except Exception:
        return 0


@lru_cache(maxsize=8)
def positionnement(ref: str | None, pente: str, seuil: str, fenetre: str, horizon: str, choc: float,
                   decision: str, codes: tuple[str, ...], version: int, mtime_titres: int) -> dict:
    S = scenarios(ref, pente, seuil, fenetre, horizon, choc, decision, version)
    t0, courbes, h_cal = S["t0"], S["courbes"], S["h_cal"]
    valo0 = (t0 + pd.Timedelta(days=1)).date()           # convention VL : J+1 sur la courbe de J
    valo_h = valo0 + timedelta(days=h_cal)
    c13 = float(S["actuelle"]["13s"])
    monetaire = c13 / 100 * h_cal / 360

    caract, rend, decomp, erreurs, roll = [], {}, [], [], {}
    for code in codes:
        try:
            f, v0 = _valoriser(code, valo0, S["actuelle"])
        except Exception as e:
            erreurs.append(f"{code} : {e}")
            continue
        V0, y0 = v0["valeur"], v0["taux"]
        if f["ech"] and f["ech"] <= valo_h:
            erreurs.append(f"{code} : échéance avant l'horizon")
            continue
        coupons = _coupons_entre(v0["tab"], valo0, valo_h)
        portage = (1 + y0 / 100) ** (h_cal / 365) - 1
        res = {}
        for nom in courbes.columns:
            _, vh = _valoriser(code, valo_h, courbes[nom])
            res[nom] = {"R": (vh["valeur"] + coupons) / V0 - 1, "taux_h": vh["taux"],
                        "sens_h": vh["sensibilite"], "conv_h": vh["convexite"], "V_h": vh["valeur"]}
        st = res["Courbe stable"]
        glissement = st["R"] - portage
        # Seuil de rentabilité : hausse parallèle du taux de la ligne qui annule le rendement (et celle
        # qui ramène au rendement monétaire), à partir de la sensibilité et de la convexité à l'horizon.
        k = st["V_h"] / V0

        def hausse_nulle(cible):
            a, b, c = 0.5 * st["conv_h"] * k, -st["sens_h"] * k, st["R"] - cible
            if abs(a) < 1e-12:
                return -c / b * 1e4
            disc = b * b - 4 * a * c
            return ((-b - np.sqrt(disc)) / (2 * a)) * 1e4 if disc >= 0 else np.nan
        be0, be_m = hausse_nulle(0.0), hausse_nulle(monetaire)
        caract.append({
            "Code": code, "Description": f["description"], "Échéance": pd.Timestamp(f["ech"]),
            "Maturité (ans)": v0["jours"] / 365.25, "Coupon": f["tf"], "TRI": y0,
            "Prix pied de coupon": v0["ppc_pct"], "Coupon couru": v0["cc"], "Valeur": V0,
            "Sensibilité": v0["sensibilite"], "Convexité": v0["convexite"], "DV01": v0.get("dv01", np.nan),
            "Taux à l'horizon (stable)": st["taux_h"],
            "Glissement du taux (pb)": (st["taux_h"] - y0) * 100,
            "Portage": portage, "Glissement": glissement, "Portage + glissement": st["R"],
            "Annualisé": (1 + st["R"]) ** (365 / h_cal) - 1,
            "Excès vs monétaire": st["R"] - monetaire,
            "Seuil de rentabilité (pb)": be0, "Seuil vs monétaire (pb)": be_m,
            "Coupons encaissés": coupons,
        })
        rend[code] = {nom: r["R"] for nom, r in res.items()}
        for nom, r in res.items():
            decomp.append({"Code": code, "Scénario": nom, "Portage": portage, "Glissement": glissement,
                           "Effet scénario": r["R"] - st["R"], "Total": r["R"],
                           "Annualisé": (1 + r["R"]) ** (365 / h_cal) - 1 if r["R"] > -1 else np.nan,
                           "Taux à l'horizon": r["taux_h"], "Δ taux ligne (pb)": (r["taux_h"] - y0) * 100})
        # Portage + glissement selon l'horizon (courbe stable)
        roll[code] = {}
        for lib, j in HORIZONS.items():
            vh_d = valo0 + timedelta(days=j)
            if f["ech"] and f["ech"] <= vh_d:
                continue
            _, vh = _valoriser(code, vh_d, S["actuelle"])
            cp = _coupons_entre(v0["tab"], valo0, vh_d)
            r_tot = (vh["valeur"] + cp) / V0 - 1
            r_por = (1 + y0 / 100) ** (j / 365) - 1
            roll[code][lib] = {"Portage": r_por, "Glissement": r_tot - r_por, "Total": r_tot,
                               "Annualisé": (1 + r_tot) ** (365 / j) - 1}

    car = pd.DataFrame(caract).set_index("Code") if caract else pd.DataFrame()
    mat = pd.DataFrame(rend).T if rend else pd.DataFrame()
    if not mat.empty:
        mat = mat[list(courbes.columns)]
        mat["Moyenne"] = mat[list(courbes.columns)].mean(axis=1)
        mat["Pire"] = mat[list(courbes.columns)].min(axis=1)
        mat["Écart-type"] = mat[list(courbes.columns)].std(axis=1)
    return {"t0": t0, "valo0": valo0, "valo_h": valo_h, "h_cal": h_cal, "scen": S, "caract": car, "rend": mat,
            "decomp": pd.DataFrame(decomp), "erreurs": erreurs, "monetaire": monetaire, "roll": roll}


# ---------------------------------------------------------------------------
# 4. Synthèse
# ---------------------------------------------------------------------------

def _pb(v, signe=True) -> str:
    return "—" if v is None or pd.isna(v) else (f"{v:+.0f} pb" if signe else f"{v:.0f} pb")


def _pct(v, d=2) -> str:
    return "—" if v is None or pd.isna(v) else f"{v * 100:.{d}f} %"


def synthese(A: dict, E: dict, P: dict | None, horizon: str) -> dict[str, list[str]]:
    """Conclusions rédigées automatiquement, par thème."""
    out = {"Diagnostic": [], "Réaction après conseil": [], "Positionnement": []}
    st = A["stats"]
    conc = A["conc"]
    dans = conc[conc["Dans la pente"]]
    top = dans.sort_values("Écart", ascending=False)
    out["Diagnostic"].append(
        f"La pente {A['pente']} est de {A['p0']:.0f} pb au {A['t0']:%d/%m/%Y}, contre une moyenne de "
        f"{st['Moyenne']:.0f} pb depuis le {A['debut']:%d/%m/%Y} (z-score {st['Z-score']:+.2f}, "
        f"{st['Centile'] * 100:.0f}e centile).")
    if len(top):
        noms = [f"{i} ({r['Écart']:+.0f} pb vs moyenne, {r['Centile'] * 100:.0f}e centile)"
                for i, r in top.head(2).iterrows() if r["Écart"] > 0]
        if noms:
            part = top.head(2)["Part de l'excès"].clip(lower=0).sum()
            out["Diagnostic"].append(
                "L'excès de pente est concentré sur " + " et ".join(noms) +
                (f" : ces deux segments portent {part * 100:.0f} % de l'excès." if pd.notna(part) else "."))
    hors = conc[~conc["Dans la pente"]]
    if len(hors):
        loin = hors.loc[hors["Écart"].abs().idxmax()]
        out["Diagnostic"].append(
            f"Hors pente de référence, le segment le plus atypique est {loin.name} ({loin['Écart']:+.0f} pb, "
            f"{loin['Centile'] * 100:.0f}e centile).")
    y = A["ytd"]
    out["Diagnostic"].append(
        f"Depuis le {y['t_debut']:%d/%m/%Y} la pente a varié de {_pb(y['d_pente'])} : court {_pb(y['d_court'])}, "
        f"long {_pb(y['d_long'])}. Mécanisme : {y['mecanisme'].lower()}.")
    mk = A["marche"]
    out["Diagnostic"].append(
        f"Le 13 semaines cote {_pb(mk['ecart'])} par rapport au taux directeur ({mk['td']:.2f} %), "
        f"contre {_pb(mk['moyenne'])} en moyenne : "
        + ("le marché ne price pas de hausse au prochain conseil." if mk["ecart"] < mk["moyenne"] + 10
           else "le marché anticipe un resserrement."))
    an = A["analogues"]["table"]
    if not an.empty and an["Δ pente 6m"].notna().any():
        a6 = an["Δ pente 6m"].dropna()
        out["Diagnostic"].append(
            f"Sur les {len(an)} analogues historiques les plus proches, la pente {A['pente']} a baissé dans "
            f"{(a6 < 0).sum()} cas sur {len(a6)} à 6 mois (variation moyenne {_pb(a6.mean())}).")

    R = resume_evenements(E, horizon)
    for dec in ("Statu quo", "Hausse"):
        r = R[(R["Décision"] == dec) & (R["Courbe"] == "Pentue")].iloc[0]
        if not r["N"]:
            out["Réaction après conseil"].append(f"{dec} avec une courbe pentue : aucun cas dans l'historique.")
            continue
        mec = max(MECANISMES, key=lambda k: r[k] if pd.notna(r[k]) else -1)
        out["Réaction après conseil"].append(
            f"{dec} avec une courbe pentue ({int(r['N'])} cas) : à {horizon}, court {_pb(r['Δ court'])}, "
            f"long {_pb(r['Δ long'])}, pente {_pb(r['Δ pente'])} en moyenne ; aplatissement dans "
            f"{r['% aplatissement'] * 100:.0f} % des cas. Mécanisme le plus fréquent : {mec.lower()} "
            f"({r[mec] * 100:.0f} %).")
    rs = R[(R["Décision"] == "Statu quo") & (R["Courbe"] == "Pentue")].iloc[0]
    if rs["N"]:
        dp, dc, dl = rs["Δ pente"], rs["Δ court"], rs["Δ long"]
        if dp < 0:
            qui = "la baisse du long terme" if dl < 0 and abs(dl) >= abs(dc) else "la remontée du court terme" \
                if dc > 0 else "une baisse du long terme plus forte que celle du court"
            force = "s'est résorbée" if dp < -NEUTRE_PB else "s'est légèrement réduite"
            out["Réaction après conseil"].append(
                f"Après un statu quo sur courbe pentue, la pente {force} en moyenne ({_pb(dp)} à {horizon}) "
                f"et ce, par {qui} : le scénario « le court remonte et annule la pentification » n'est pas "
                f"celui de l'historique." if dc <= 0 else
                f"Après un statu quo sur courbe pentue, la pente {force} en moyenne ({_pb(dp)} à {horizon}) "
                f"par {qui}.")
        else:
            out["Réaction après conseil"].append(
                f"Après un statu quo sur courbe pentue, la pente s'est accentuée en moyenne ({_pb(dp)} à {horizon}).")
    if E["ev"]["Pentue"].sum() and (E["ev"]["Décision"] == "Hausse").sum() < 6:
        out["Réaction après conseil"].append(
            f"Prudence : seulement {(E['ev']['Décision'] == 'Hausse').sum()} hausses dans l'historique, "
            "les moyennes par décision reposent sur peu de cas.")

    if P and not P["rend"].empty:
        mat, car = P["rend"], P["caract"]
        sc = [c for c in mat.columns if c not in ("Moyenne", "Pire", "Écart-type")]
        best_st = mat["Courbe stable"].idxmax()
        best_m = mat["Moyenne"].idxmax()
        best_p = mat["Pire"].idxmax()
        out["Positionnement"].append(
            f"Courbe stable à {horizon} : meilleur portage + glissement sur {best_st} "
            f"({_pct(mat.loc[best_st, 'Courbe stable'])}, soit {_pct(car.loc[best_st, 'Annualisé'])} annualisé, "
            f"dont glissement {_pct(car.loc[best_st, 'Glissement'])}).")
        g = car["Glissement"].idxmax()
        taux_h = car.loc[g, "Taux à l'horizon (stable)"]
        out["Positionnement"].append(
            f"Le glissement le plus fort est sur {g} : son taux passe de {car.loc[g, 'TRI']:.3f} % à "
            f"{taux_h:.3f} % en roulant sur la courbe "
            f"({car.loc[g, 'Glissement du taux (pb)']:+.1f} pb).")
        out["Positionnement"].append(
            f"Moyenne des {len(sc)} scénarios : {best_m} ({_pct(mat.loc[best_m, 'Moyenne'])}). "
            f"Ligne la plus robuste (meilleur pire cas) : {best_p} ({_pct(mat.loc[best_p, 'Pire'])}, "
            f"pire scénario : {mat.loc[best_p, sc].idxmin().lower()}).")
        be = car["Seuil de rentabilité (pb)"]
        out["Positionnement"].append(
            "Coussin avant perte (hausse du taux de la ligne qui annule le rendement à l'horizon) : "
            + ", ".join(f"{c} {be[c]:.0f} pb" for c in car.index) + ".")
        bm = car["Seuil vs monétaire (pb)"]
        out["Positionnement"].append(
            f"Pour faire mieux que le 13 semaines ({_pct(P['monetaire'])} sur la période), le taux de la ligne ne "
            "doit pas monter de plus de : " + ", ".join(f"{c} {bm[c]:.0f} pb" for c in car.index) + ".")
    out["Comment l'exploiter"] = pistes(A, E, P, horizon)
    return out


def pistes(A: dict, E: dict, P: dict | None, horizon: str) -> list[str]:
    """Lecture opérationnelle : où se placer sur la courbe, avec quel biais, et quel risque."""
    out = []
    conc = A["conc"]
    seg_max = conc["Écart"].idxmax()
    R = resume_evenements(E, horizon)
    rs = R[(R["Décision"] == "Statu quo") & (R["Courbe"] == "Pentue")].iloc[0]
    rh = R[R["Décision"] == "Hausse"]
    mk = A["marche"]
    an = A["analogues"]["table"]
    baisse_an = (an["Δ pente 6m"].dropna() < 0).mean() if not an.empty and an["Δ pente 6m"].notna().any() else np.nan

    out.append(
        f"Le surplus de pente se trouve sur le segment {seg_max} ({conc.loc[seg_max, 'Écart']:+.0f} pb vs moyenne, "
        f"{conc.loc[seg_max, 'pb/an actuel']:.1f} pb par année de maturité contre {conc.loc[seg_max, 'pb/an moyen']:.1f} "
        "en moyenne) : c'est là que le glissement rapporte le plus, en détenant des lignes dont la maturité se situe "
        "sur ce segment ou juste au-dessus, qui le descendent en vieillissant.")
    plat_long = conc.loc[[c for c in conc.index if c.startswith(("15a", "20a"))], "pb/an actuel"].mean()
    if pd.notna(plat_long) and plat_long < conc.loc[seg_max, "pb/an actuel"] / 2:
      out.append(
        f"Au-delà de 15 ans la courbe ne gagne que {plat_long:.1f} pb par année : le très long terme apporte du "
        "portage mais peu de glissement pour une sensibilité plus forte. À sensibilité égale, une position centrée "
        "sur le ventre (bullet) capte mieux cette pentification qu'un haltère court terme + 30 ans.")
    if mk["ecart"] < mk["moyenne"] + 10:
        out.append(
            f"Le 13 semaines sous le taux directeur ({mk['ecart']:+.0f} pb) indique que le marché attend un statu quo "
            "en décembre ; une hausse serait une surprise, dont le premier effet porterait sur le court terme.")
    if rs["N"]:
        sens = "baisse du long" if rs["Δ long"] < 0 and abs(rs["Δ long"]) >= abs(rs["Δ court"]) else "remontée du court"
        txt = (f"Si BAM maintient son taux (scénario central), l'historique sur courbe pentue donne une pente de "
               f"{rs['Δ pente']:+.0f} pb à {horizon}, obtenue surtout par la {sens}")
        if pd.notna(baisse_an):
            txt += f", et la pente a reculé après {baisse_an * 100:.0f} % des analogues"
        txt += (" : le biais est de détenir de la duration sur le ventre et le long terme, qui profiterait d'une "
                "détente du long, plutôt que d'attendre une remontée du court." if sens == "baisse du long" else
                " : le biais est de privilégier les maturités intermédiaires et de limiter le très court.")
        out.append(txt)
    n_h = int(rh["N"].sum())
    if n_h:
        out.append(
            f"En cas de hausse ({n_h} précédents seulement), le risque principal est sur le court terme : les lignes "
            "longues sont moins exposées à un choc court que les lignes de 2 à 5 ans (voir le scénario « Court terme »).")
    if P and not P["rend"].empty:
        mat, car = P["rend"], P["caract"]
        lc = [c for c in mat.columns if c.startswith("Long terme")]
        best = mat["Moyenne"].idxmax()
        robuste = mat["Pire"].idxmax()
        txt = (f"Parmi les lignes simulées, {best} offre le meilleur rendement moyen sur les scénarios "
               f"({mat.loc[best, 'Moyenne'] * 100:.2f} % à {horizon}, coussin de {car.loc[best, 'Seuil de rentabilité (pb)']:.0f} pb)")
        txt += (f" ; {robuste} est la plus défensive (pire cas {mat.loc[robuste, 'Pire'] * 100:.2f} %)."
                if robuste != best else " et reste la plus robuste dans le pire scénario.")
        out.append(txt)
        if lc:
            pert = mat[lc[0]]
            neg = [c for c in pert.index if pert[c] < 0]
            if neg:
                out.append(
                    f"Risque à surveiller : une nouvelle hausse du long terme ({lc[0].split(' ', 2)[2]}) rendrait négatif "
                    "le rendement de " + " et ".join([", ".join(neg[:-1]), neg[-1]] if len(neg) > 1 else neg) + " sur l'horizon ; c'est le mouvement qui a créé la pentification de cette année. "
                    "Une entrée progressive limite ce risque.")
    return out
