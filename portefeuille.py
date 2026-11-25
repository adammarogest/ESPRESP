"""
Portefeuille : chargement d'un portefeuille depuis la base Manar (Oracle) et revalorisation des titres.

1. connect_to_manar : connexion Oracle (oracledb, mode « thin », sans client Oracle à installer).
2. charger_portefeuille : requête de composition du portefeuille à une date (type d'actif D).
3. separer : les lignes sans DATE_EMISSION, DATE_JOUISSANCE ni DATE_ECHEANCE sont des OPCVM externes.
4. valoriser : chaque titre est retrouvé dans base_titre.xlsx par son CODE_ISIN, valorisé avec les
   règles du Pricer (prix plein unitaire), multiplié par QUANTITE -> TOTAL_VALO, comparé à VALORISATION.
5. charger_vl / calcul_vl : opérations en cours (tp.valo_engagement), position espèce du jour
   (tp.histo_operation), banque, charges, agios, actif net et parts Manar (tp.histo_portef_fp_group), puis
   calcul de la VL : banque actif, espèce actif, actif brut, base et frais de gestion, actif net, VL.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache

import numpy as np
import pandas as pd

import base_titre as BT
import controle as C
import pricer as PR
import swap as SW

HOTE, PORT, SID = "192.168.7.80", 1521, "MANARPRO"

REQUETE = """
SELECT tit.code_isin,
       tit.description Libelle,
       tit.date_emission,
       tit.date_jouissance,
       tit.date_echeance,
       tit.valeur_taux Taux_Facial,
       (MONTHS_BETWEEN(tit.date_echeance, tit.date_emission) / 12) Maturite_Initial,
       tit.nominal,
       (SELECT c.tri FROM tp.histo_analyse_taux c
         WHERE c.titre = a.titre AND c.date_analyse = a.date_fp) taux_rendement,
       tit.spread_emission Prime_risque,
       a.qte_actif Quantite,
       a.montant_5 Valorisation,
       DECODE((SELECT SUM(fp.montant_5) FROM tp.histo_portef_fp fp
                WHERE fp.portefeuille = a.portefeuille AND fp.type_actif = 'D' AND fp.date_fp = a.date_fp), 0, 0,
              a.montant_5 / (SELECT SUM(fp.montant_5) FROM tp.histo_portef_fp fp
                              WHERE fp.portefeuille = a.portefeuille AND fp.type_actif = 'D' AND fp.date_fp = a.date_fp)) Poid,
       tit.periodicite_rembou Mode_Amortissement,
       a.titre Code_Titre
FROM tp.titre tit, tp.histo_portef_fp a
WHERE a.titre = tit.code
  AND a.type_actif = 'D'
  AND a.date_fp = TO_DATE(:date_fp, 'dd/mm/yyyy')
  AND a.portefeuille = :portefeuille
"""


def connect_to_manar(query: str, username: str, password: str, params: dict | None = None) -> pd.DataFrame:
    """
    Connexion à la base MANAR et exécution d'une requête.
    Args:
        query (str): requête SQL (variables liées :nom possibles)
        username (str): utilisateur
        password (str): mot de passe
        params (dict): valeurs des variables liées de la requête
    Returns:
        pd.DataFrame: résultat de la requête
    """
    import oracledb                                     # importé ici : l'application démarre sans

    dsn = f"{HOTE}:{PORT}/{SID}"
    conn = oracledb.connect(user=username, password=password, dsn=dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or {})
            colonnes = [d[0].upper() for d in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=colonnes)
    finally:
        conn.close()
    return df


def executer_requetes(requetes: dict[str, tuple[str, dict]], username: str, password: str) -> dict[str, pd.DataFrame]:
    """Plusieurs requêtes sur une seule connexion Manar : {nom: (sql, variables liées)} -> {nom: DataFrame}."""
    import oracledb

    conn = oracledb.connect(user=username, password=password, dsn=f"{HOTE}:{PORT}/{SID}")
    res = {}
    try:
        for nom, (sql, params) in requetes.items():
            with conn.cursor() as cur:
                cur.execute(sql, params or {})
                colonnes = [d[0].upper() for d in cur.description]
                res[nom] = pd.DataFrame(cur.fetchall(), columns=colonnes)
    finally:
        conn.close()
    return res


def charger_portefeuille(username: str, password: str, date_fp: date, portefeuille: str = "FMGC") -> pd.DataFrame:
    """Composition du portefeuille à la date (lignes de type D) depuis Manar."""
    return connect_to_manar(REQUETE, username, password,
                            {"date_fp": date_fp.strftime("%d/%m/%Y"), "portefeuille": portefeuille})


# ---------------------------------------------------------------------------
# Traitement
# ---------------------------------------------------------------------------

DATES = ["DATE_EMISSION", "DATE_JOUISSANCE", "DATE_ECHEANCE"]
NUMERIQUES = ["TAUX_FACIAL", "MATURITE_INITIAL", "NOMINAL", "TAUX_RENDEMENT", "PRIME_RISQUE", "QUANTITE",
              "VALORISATION", "POID"]


def normaliser(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().upper() for c in df.columns]
    for c in DATES:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in NUMERIQUES:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "CODE_ISIN" in df.columns:
        df["CODE_ISIN"] = df["CODE_ISIN"].astype(str).str.strip()
    return df


def separer(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(titres, OPCVM externes) : OPCVM = lignes dont les trois dates sont vides."""
    df = normaliser(df)
    vides = df[[c for c in DATES if c in df.columns]].isna().all(axis=1)
    return df[~vides].reset_index(drop=True), df[vides].reset_index(drop=True)


@lru_cache(maxsize=2)
def _isin_vers_code(mtime: int) -> dict[str, str]:
    """CODE_ISIN -> CODE de base_titre ; en cas de doublon : ligne active, valorisable, échéance la plus tardive."""
    b = BT.base()
    if b.empty or "CODE_ISIN" not in b.columns:
        return {}
    t = b[["CODE", "CODE_ISIN"]].copy()
    t["isin"] = t["CODE_ISIN"].astype(str).str.strip()
    t["actif"] = (b.get("FLAG_ACTIF", pd.Series("O", index=b.index)).astype(str).str.upper() == "O").astype(int)
    t["valo"] = b.get("TYPE_TAUX", pd.Series(index=b.index)).notna().astype(int)
    t["ech"] = pd.to_datetime(b.get("DATE_ECHEANCE"), errors="coerce").fillna(pd.Timestamp.min)
    t = t[t["isin"].ne("") & t["isin"].ne("nan")].sort_values(["actif", "valo", "ech"], ascending=False)
    return dict(zip(t["isin"], t["CODE"]))


def isin_vers_code(isin: str) -> str | None:
    m = _isin_vers_code(BT.CHEMIN.stat().st_mtime_ns if BT.CHEMIN.exists() else 0)
    return m.get(str(isin).strip())


def valoriser(titres: pd.DataFrame, valo: date, courbe: pd.DataFrame, tol: float) -> pd.DataFrame:
    """
    Revalorisation : prix plein unitaire (pricer) × QUANTITE = TOTAL_VALO, comparé à VALORISATION.
    Une ligne est conforme si l'écart par titre reste sous `tol` (MAD).
    """
    lignes = []
    for _, t in titres.iterrows():
        q, vm = t.get("QUANTITE"), t.get("VALORISATION")
        r = {"isin": t["CODE_ISIN"], "libelle": t.get("LIBELLE"), "quantite": q, "valorisation": vm,
             "poids": t.get("POID"), "taux_rendement": t.get("TAUX_RENDEMENT"), "echeance": t.get("DATE_ECHEANCE"),
             "taux_facial": t.get("TAUX_FACIAL"), "prime_risque": t.get("PRIME_RISQUE"),
             "mode_amortissement": t.get("MODE_AMORTISSEMENT"), "tol_prix": tol}
        code = isin_vers_code(t["CODE_ISIN"])
        f = BT.fiche(code) if code else None
        r["code"] = code
        if f is not None:
            r.update({"type": f["type"], "type_lib": PR.TYPES.get(f["type"], f["type"]) + (" amort." if f["rev_amorti"] else ""),
                      "rev_amorti": f["rev_amorti"], "categorie": f["categorie"], "emetteur": f["emetteur"],
                      "tf": f["tf"], "prime": f["prime"], "nominal": f["nominal"]})
        debut = (f["em"] or f["jouis"]) if f else None
        if f is not None:
            r["valo_zero"] = bool(f.get("valo_zero"))
        if f is not None and f.get("valo_zero"):
            # Règle de gestion : ligne toujours valorisée à 0, quelles que soient ses caractéristiques
            pm = vm / q if pd.notna(vm) and q else 0.0
            r.update({"prix_unitaire": 0.0, "total_valo": 0.0, "sensibilite": 0.0, "duration": 0.0, "crd": 0.0,
                      "ecart": -(vm if pd.notna(vm) else 0.0), "prix_manar": pm, "ecart_unitaire": -pm, "ecart_pb": np.nan,
                      "ech_eff": f["ech"], "statut": "ok" if abs(pm) <= tol else "prix"})
        elif f is None:
            r["statut"] = "absent"
        elif not f["valorisable"]:
            r["statut"] = "sans_carac"
        elif not (f["ech"] and f["ech"] > valo and debut and debut <= valo):
            r["statut"] = "hors"
        elif not (pd.notna(q) and q):
            r["statut"] = "non_valo"
        else:
            try:
                v = PR.valoriser(SW._parametres(f, valo), courbe)
            except Exception as ex:                                       # noqa: BLE001
                r.update(statut="erreur", erreur=str(ex))
            else:
                r.update({"prix_unitaire": v["valeur"], "total_valo": v["valeur"] * q, "sensibilite": v["sensibilite"],
                          "duration": v["duration"], "taux_calcul": v["taux"], "crd": v["crd"], "cc": v["cc"],
                          "ech_eff": v["ech_eff"]})
                r["ecart"] = r["total_valo"] - (vm if pd.notna(vm) else 0.0)
                r["prix_manar"] = vm / q if pd.notna(vm) and q else np.nan
                r["ecart_unitaire"] = r["prix_unitaire"] - r["prix_manar"]
                r["ecart_pb"] = r["ecart"] / vm * 1e4 if pd.notna(vm) and vm else np.nan
                r["statut"] = "ok" if pd.notna(r["ecart_unitaire"]) and abs(r["ecart_unitaire"]) <= tol else "prix"
        # Diagnostic : mêmes règles que le contrôle de valorisation, sur les prix unitaires
        r_diag = {**r, "valo_fichier": r.get("prix_manar"), "valo_calcul": r.get("prix_unitaire"),
                  "ecart_mad": r.get("ecart_unitaire"), "ecart_pb": r.get("ecart_pb"), "categorie": r.get("categorie", ""),
                  "type": r.get("type")}
        r["cause"] = C._cause(r_diag) if r["statut"] != "absent" else "ISIN absent de base_titre.xlsx"
        r["statut_lib"] = STATUTS[r["statut"]]
        lignes.append(r)
    df = pd.DataFrame(lignes)
    for c in ("prix_unitaire", "total_valo", "ecart", "ecart_unitaire", "ecart_pb", "prix_manar", "sensibilite",
              "duration", "taux_calcul", "crd", "type_lib", "categorie", "emetteur", "code", "ech_eff"):
        if c not in df.columns:
            df[c] = np.nan
    return df


STATUTS = {"ok": "Conforme", "prix": "Écart de valorisation", "absent": "ISIN absent de la base",
           "sans_carac": "Sans caractéristiques de pricing", "hors": "Échu ou pas encore émis",
           "non_valo": "Quantité nulle", "erreur": "Erreur de calcul"}
COMPAREES = ("ok", "prix")


def resume(valo_df: pd.DataFrame, opcvm: pd.DataFrame) -> dict:
    comp = valo_df[valo_df["statut"].isin(COMPAREES)]
    tot_manar = float(comp["valorisation"].sum())
    tot_calc = float(comp["total_valo"].sum())
    v_opc = float(pd.to_numeric(opcvm.get("VALORISATION"), errors="coerce").sum()) if len(opcvm) else 0.0
    v_tit = float(pd.to_numeric(valo_df["valorisation"], errors="coerce").sum())
    sens = float((comp["sensibilite"] * comp["total_valo"]).sum() / tot_calc) if tot_calc else np.nan
    dur = float((comp["duration"] * comp["total_valo"]).sum() / tot_calc) if tot_calc else np.nan
    return {
        "n_titres": len(valo_df), "n_opcvm": len(opcvm), "n_comparees": len(comp),
        "n_ok": int((valo_df["statut"] == "ok").sum()), "n_ecart": int((valo_df["statut"] == "prix").sum()),
        "n_non": int((~valo_df["statut"].isin(COMPAREES)).sum()),
        "total_manar": tot_manar, "total_calcul": tot_calc, "ecart": tot_calc - tot_manar,
        "ecart_pb": (tot_calc / tot_manar - 1) * 1e4 if tot_manar else np.nan,
        "valo_titres": v_tit, "valo_opcvm": v_opc, "actif_total": v_tit + v_opc,
        "sensibilite": sens, "duration": dur,
    }


# ---------------------------------------------------------------------------
# Analyses : maturité résiduelle, prime, émetteurs
# ---------------------------------------------------------------------------

TRANCHES = [(0, 0.25, "< 3 mois"), (0.25, 0.5, "3-6 mois"), (0.5, 1, "6 mois-1 an"), (1, 2, "1-2 ans"),
            (2, 5, "2-5 ans"), (5, 7, "5-7 ans"), (7, 10, "7-10 ans"), (10, 15, "10-15 ans"), (15, 20, "15-20 ans"),
            (20, np.inf, "> 20 ans")]
ETAT = {"EETAT", "ETAT", "TRESOR"}


def base_analyse(v: pd.DataFrame, valo: date, echeance: str = "retenue") -> pd.DataFrame:
    """
    Lignes titres avec maturité résiduelle (années), poids et prime.
    echeance = "retenue" : prochaine révision pour un révisable, échéance sinon ; "finale" : échéance du titre.
    Poids : valorisation Manar (total recalculé à défaut), en % des titres.
    """
    d = v.copy()
    d["valo_ref"] = pd.to_numeric(d["valorisation"], errors="coerce")
    d.loc[d["valo_ref"].isna(), "valo_ref"] = d["total_valo"]
    d.loc[d.get("valo_zero", False) == True, "valo_ref"] = 0.0          # noqa: E712  ligne forcée à 0
    fin = pd.to_datetime(d["echeance"], errors="coerce")
    if echeance == "retenue":
        fin = pd.to_datetime(d["ech_eff"], errors="coerce").fillna(fin)
    d["maturite"] = (fin - pd.Timestamp(valo)).dt.days / 365.25
    d["prime_pb"] = pd.to_numeric(d["prime"], errors="coerce").fillna(pd.to_numeric(d["prime_risque"], errors="coerce")).fillna(0.0)
    d["emetteur"] = d["emetteur"].fillna("Non renseigné")
    d = d[d["valo_ref"].fillna(0) > 0]
    d["poids"] = d["valo_ref"] / d["valo_ref"].sum() if len(d) else np.nan
    d["tranche"] = pd.cut(d["maturite"].clip(lower=0), [t[0] for t in TRANCHES] + [np.inf], right=False,
                          labels=[t[2] for t in TRANCHES])
    return d


def _moy(x: pd.Series, w: pd.Series) -> float:
    m = x.notna() & w.notna()
    return float((x[m] * w[m]).sum() / w[m].sum()) if w[m].sum() else np.nan


def repartition_maturite(d: pd.DataFrame) -> pd.DataFrame:
    prime_tot = float((d["poids"] * d["prime_pb"]).sum())
    lignes = []
    for _, _, lib in TRANCHES:
        g = d[d["tranche"] == lib]
        contrib = float((g["poids"] * g["prime_pb"]).sum())
        lignes.append({"Tranche": lib, "Lignes": len(g), "Valorisation": g["valo_ref"].sum(), "Poids": g["poids"].sum(),
                       "Prime moyenne (pb)": _moy(g["prime_pb"], g["valo_ref"]) if len(g) else np.nan,
                       "Part de la prime": contrib / prime_tot if prime_tot else np.nan,
                       "Sensibilité": _moy(g["sensibilite"], g["valo_ref"]) if len(g) else np.nan})
    t = pd.DataFrame(lignes).set_index("Tranche")
    t.loc["Total"] = [len(d), d["valo_ref"].sum(), d["poids"].sum(), _moy(d["prime_pb"], d["valo_ref"]),
                      1.0 if prime_tot else np.nan, _moy(d["sensibilite"], d["valo_ref"])]
    return t


def indicateurs_prime(d: pd.DataFrame) -> dict:
    t = repartition_maturite(d).drop(index="Total")
    prive = d[~d["emetteur"].str.upper().isin(ETAT)]
    conc = t["Part de la prime"].idxmax() if t["Part de la prime"].notna().any() else None
    return {
        "prime_moyenne": _moy(d["prime_pb"], d["valo_ref"]),
        "prime_hors_etat": _moy(prive["prime_pb"], prive["valo_ref"]) if len(prive) else np.nan,
        "part_hors_etat": float(prive["poids"].sum()) if len(prive) else 0.0,
        "tranche_concentration": conc,
        "part_concentration": float(t.loc[conc, "Part de la prime"]) if conc else np.nan,
        "poids_concentration": float(t.loc[conc, "Poids"]) if conc else np.nan,
        "maturite_moyenne": _moy(d["maturite"], d["valo_ref"]),
        "part_moins_1an": float(d.loc[d["maturite"] < 1, "poids"].sum()),
        "part_plus_10ans": float(d.loc[d["maturite"] >= 10, "poids"].sum()),
    }


def repartition_emetteur(d: pd.DataFrame) -> pd.DataFrame:
    g = d.groupby("emetteur")
    t = pd.DataFrame({
        "Lignes": g.size(), "Valorisation": g["valo_ref"].sum(), "Poids": g["poids"].sum(),
        "Prime moyenne (pb)": g.apply(lambda x: _moy(x["prime_pb"], x["valo_ref"]), include_groups=False),
        "Maturité moyenne (ans)": g.apply(lambda x: _moy(x["maturite"], x["valo_ref"]), include_groups=False),
        "Sensibilité": g.apply(lambda x: _moy(x["sensibilite"], x["valo_ref"]), include_groups=False),
    }).sort_values("Poids", ascending=False)
    t.loc["Total"] = [len(d), d["valo_ref"].sum(), d["poids"].sum(), _moy(d["prime_pb"], d["valo_ref"]),
                      _moy(d["maturite"], d["valo_ref"]), _moy(d["sensibilite"], d["valo_ref"])]
    return t


# ---------------------------------------------------------------------------
# Calcul de la VL
# ---------------------------------------------------------------------------

REQ_ENGAGEMENTS = """
SELECT * FROM tp.valo_engagement
WHERE date_valo = TO_DATE(:d, 'DD/MM/YYYY') AND portefeuille = :p
"""
REQ_OPERATIONS = """
SELECT * FROM tp.histo_operation
WHERE (date_valeur = TO_DATE(:d, 'DD/MM/YYYY') OR date_echeance = TO_DATE(:d, 'DD/MM/YYYY'))
  AND entite = :p
"""
REQ_FP_GROUP = """
SELECT * FROM tp.histo_portef_fp_group
WHERE date_fp = TO_DATE(:d, 'DD/MM/YYYY') AND portefeuille = :p
"""
EXCLUS_OPERATIONS = ("FRGES-", "PROCDVM-")
# Postes dont le montant entre en négatif dans la position espèce
POSTES_NEG_VALEUR = ["PENSRO", "PENSRT", "ABDTS", "ABDTP", "ATCNP", "ATCNS", "AOBLS", "AOBLP"]   # MONTANT_2, valeur J
POSTES_NEG_ECHEANCE = ["PENSDOB", "PENSRTOB"]                                                     # MONTANT_4, échéance J
# OPCVM gérés en interne : retirés de la base de calcul des frais de gestion
OPCVM_INTERNES = ["FCM", "FMGC", "FML", "FCOB", "FMS", "FCO", "FCOR", "FMO", "FMB", "FMSR", "FCA", "FSO", "FMA",
                  "FIF", "SMC", "FMI", "FMC", "FTS"]


def charger_vl(username: str, password: str, date_valo: date, portefeuille: str) -> dict[str, pd.DataFrame]:
    """Engagements, opérations du jour et ligne de groupe du portefeuille à la date de valorisation."""
    p = {"d": date_valo.strftime("%d/%m/%Y"), "p": portefeuille}
    return executer_requetes({"engagements": (REQ_ENGAGEMENTS, p), "operations": (REQ_OPERATIONS, p),
                              "fp": (REQ_FP_GROUP, p)}, username, password)


def _col(df: pd.DataFrame, *noms) -> pd.Series:
    """Première colonne présente parmi les noms (numérique) ; zéros sinon."""
    for n in noms:
        if n in df.columns:
            return pd.to_numeric(df[n], errors="coerce").fillna(0.0)
    return pd.Series(0.0, index=df.index)


def _jour(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.date


def operations_exclues(ops: pd.DataFrame) -> pd.Series:
    """Lignes FRGES- / PROCDVM- : une colonne texte de la ligne commence par l'un de ces préfixes."""
    if ops.empty:
        return pd.Series(False, index=ops.index)
    textes = ops.select_dtypes(include=["object", "string"])
    if textes.empty:
        return pd.Series(False, index=ops.index)
    t = textes.astype(str).apply(lambda c: c.str.strip().str.upper())
    return t.apply(lambda c: c.str.startswith(EXCLUS_OPERATIONS)).any(axis=1)


COLONNES_POSTE = ("POSTE", "CODE_POSTE", "TYPE_POSTE", "POSTE_OPERATION", "CODE_OPERATION", "TYPE_OPERATION")


def poste_operation(ops: pd.DataFrame) -> pd.Series:
    """
    Poste de chaque opération (PENSRO, ABDTS, PENSDOB…) : colonne POSTE (ou CODE_POSTE, TYPE_OPERATION…) si
    elle existe, sinon première colonne texte dont la valeur est le code du poste ou commence par « code- »
    (même repérage que FRGES- / PROCDVM-) ; vide si aucun poste suivi n'est reconnu.
    """
    if ops.empty:
        return pd.Series("", index=ops.index, dtype=object)
    codes = sorted(set(POSTES_NEG_VALEUR + POSTES_NEG_ECHEANCE), key=len, reverse=True)
    motif = r"^(" + "|".join(codes) + r")(?:[-_ /].*)?$"
    poste = pd.Series("", index=ops.index, dtype=object)
    colonnes = [c for c in COLONNES_POSTE if c in ops.columns]
    colonnes += [c for c in ops.columns if c not in colonnes and
                 (pd.api.types.is_object_dtype(ops[c]) or pd.api.types.is_string_dtype(ops[c]))]
    for c in colonnes:
        trouve = ops[c].astype(str).str.strip().str.upper().str.extract(motif, expand=False)
        poste = poste.where(poste != "", trouve.fillna(""))
    return poste


def opcvm_internes(opcvm: pd.DataFrame) -> pd.Series:
    """OPCVM internes reconnus par le code titre Manar, l'ISIN ou le libellé égal à un code interne."""
    if opcvm.empty:
        return pd.Series(False, index=opcvm.index)
    codes = set(OPCVM_INTERNES)
    m = pd.Series(False, index=opcvm.index)
    for c in ("CODE_TITRE", "CODE_ISIN", "LIBELLE"):
        if c in opcvm.columns:
            m |= opcvm[c].astype(str).str.strip().str.upper().isin(codes)
    return m


def jour_ouvre_precedent(d: date) -> date:
    from datetime import timedelta
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def calcul_vl(valo_titres: pd.DataFrame, opcvm: pd.DataFrame, vl: dict[str, pd.DataFrame], date_valo: date,
              taux_fg: float | None, internes: list[str] | None = None,
              banque_actif_manuel: float | None = None) -> dict:
    """
    Position espèce = Σ MONTANT_2 des opérations en valeur J (négatif pour PENSRO, PENSRT, ABDTS, ABDTP, ATCNP,
    ATCNS, AOBLS, AOBLP) + Σ MONTANT_4 des opérations à échéance J (négatif pour PENSDOB, PENSRTOB) ;
    banque actif = banque J-1 + position espèce (ou montant saisi à la main) ;
    espèce actif = banque actif − charges en cours − agios ;
    valo des actifs = total valo des titres valorisés + valorisation des OPCVM ;
    actif brut = espèce actif + valo des actifs + opérations en cours ;
    base FG = actif brut − OPCVM internes ; FG = base × taux × (J − jour ouvré précédent) / 360 ;
    actif net = actif brut − FG ; VL = actif net / nombre de parts.
    """
    eng = vl.get("engagements", pd.DataFrame())
    ops = vl.get("operations", pd.DataFrame())
    fp = vl.get("fp", pd.DataFrame())

    operations_cours = float(_col(eng, "MONTANT_CV").sum())

    exclues = operations_exclues(ops)
    ops = ops.loc[:, ~ops.columns.duplicated()].copy() if len(ops.columns) else ops
    exclues = exclues.loc[ops.index]
    retenues = ops[~exclues].copy()
    if len(retenues):
        poste = poste_operation(retenues)
        jv = _jour(retenues["DATE_VALEUR"]) if "DATE_VALEUR" in retenues else pd.Series(None, index=retenues.index)
        je = _jour(retenues["DATE_ECHEANCE"]) if "DATE_ECHEANCE" in retenues else pd.Series(None, index=retenues.index)
        en_valeur, a_echeance = (jv == date_valo), (je == date_valo)
        m2, m4 = _col(retenues, "MONTANT_2"), _col(retenues, "MONTANT_4")
        neg2 = poste.isin(POSTES_NEG_VALEUR)
        neg4 = poste.isin(POSTES_NEG_ECHEANCE)
        retenu_valeur = np.where(en_valeur, np.where(neg2, -m2, m2), 0.0)
        retenu_echeance = np.where(a_echeance, np.where(neg4, -m4, m4), 0.0)
        esp_valeur, esp_echeance = float(retenu_valeur.sum()), float(retenu_echeance.sum())
        n_neg_valeur, n_neg_echeance = int((en_valeur & neg2).sum()), int((a_echeance & neg4).sum())
        detail_ops = pd.DataFrame({"POSTE_RETENU": poste.values, "MONTANT_RETENU_VALEUR_J": retenu_valeur,
                                   "MONTANT_RETENU_ECHEANCE_J": retenu_echeance}, index=retenues.index)
    else:
        esp_valeur = esp_echeance = 0.0
        n_neg_valeur = n_neg_echeance = 0
        detail_ops = pd.DataFrame(columns=["POSTE_RETENU", "MONTANT_RETENU_VALEUR_J", "MONTANT_RETENU_ECHEANCE_J"])
    position_espece = esp_valeur + esp_echeance

    banque_j1 = float(_col(fp, "BANQUE_DEB_JOUR").sum())
    charges_esp = float(_col(fp, "ESP_CHANGES_ENCOURS", "ESP_CHARGES_ENCOURS").sum())
    charges_prov = float(_col(fp, "CUMUL_PROVISIONS_CHARGES").sum())
    charges = charges_esp + charges_prov
    agios = float(_col(fp, "CUMUL_PROVISIONS_AGIOS").sum())
    actif_net_manar = float(_col(fp, "ACTIF_NET_AV_VL").sum()) if len(fp) else np.nan
    parts = float(_col(fp, "NOMBRE_PARTS_AV_VL").sum()) if len(fp) else np.nan

    banque_actif_calcule = banque_j1 + position_espece
    manuel = banque_actif_manuel not in (None, "") and pd.notna(banque_actif_manuel)
    banque_actif = float(banque_actif_manuel) if manuel else banque_actif_calcule      # saisie manuelle prioritaire
    espece_actif = banque_actif - charges - agios

    tv = pd.to_numeric(valo_titres.get("total_valo"), errors="coerce") if len(valo_titres) else pd.Series(dtype=float)
    titres_prices = float(tv.sum()) if len(valo_titres) else 0.0               # titres valorisés (total valo)
    n_titres_valorises = int(tv.notna().sum()) if len(valo_titres) else 0
    n_titres_non_valorises = len(valo_titres) - n_titres_valorises
    titres_non_prices = 0.0                                                  # non repris dans la valo des actifs
    v_opc = pd.to_numeric(opcvm.get("VALORISATION"), errors="coerce").fillna(0.0) if len(opcvm) else pd.Series(dtype=float)
    valo_opcvm = float(v_opc.sum()) if len(opcvm) else 0.0
    valo_actifs = titres_prices + titres_non_prices + valo_opcvm

    actif_brut = espece_actif + valo_actifs + operations_cours

    if internes is None:
        masque = opcvm_internes(opcvm)
    else:
        masque = opcvm["CODE_ISIN"].astype(str).isin(set(internes)) if len(opcvm) else pd.Series(dtype=bool)
    opcvm_int = float(v_opc[masque].sum()) if len(opcvm) else 0.0
    base_fg = actif_brut - opcvm_int
    precedent = jour_ouvre_precedent(date_valo)
    jours = (date_valo - precedent).days
    frais = base_fg * (float(taux_fg) / 100) * jours / 360 if taux_fg not in (None, "") else np.nan
    actif_net = actif_brut - (frais if pd.notna(frais) else 0.0)
    vl_calc = actif_net / parts if parts and pd.notna(parts) and pd.notna(frais) else np.nan
    vl_manar = actif_net_manar / parts if parts and pd.notna(parts) and pd.notna(actif_net_manar) else np.nan

    return {
        "date_valo": date_valo, "banque_j1": banque_j1, "esp_valeur": esp_valeur, "esp_echeance": esp_echeance,
        "position_espece": position_espece, "banque_actif": banque_actif, "charges": charges,
        "banque_actif_calcule": banque_actif_calcule, "banque_manuelle": bool(manuel),
        "charges_esp": charges_esp, "charges_prov": charges_prov, "agios": agios, "espece_actif": espece_actif,
        "titres_prices": titres_prices, "titres_non_prices": titres_non_prices, "valo_opcvm": valo_opcvm,
        "valo_actifs": valo_actifs, "operations_cours": operations_cours, "actif_brut": actif_brut,
        "opcvm_internes": opcvm_int, "masque_internes": masque, "base_fg": base_fg, "taux_fg": taux_fg,
        "jour_precedent": precedent, "jours": jours, "frais": frais, "actif_net": actif_net, "parts": parts,
        "vl": vl_calc, "actif_net_manar": actif_net_manar, "vl_manar": vl_manar,
        "ecart_an": actif_net - actif_net_manar if pd.notna(actif_net_manar) and pd.notna(frais) else np.nan,
        "ecart_vl": vl_calc - vl_manar if pd.notna(vl_calc) and pd.notna(vl_manar) else np.nan,
        "n_operations": len(ops), "n_exclues": int(exclues.sum()), "n_engagements": len(eng), "n_fp": len(fp),
        "n_neg_valeur": n_neg_valeur, "n_neg_echeance": n_neg_echeance,
        "n_titres_valorises": n_titres_valorises, "n_titres_non_valorises": n_titres_non_valorises,
        "operations": pd.concat([ops.drop(columns=[c for c in ("EXCLUE", *detail_ops.columns) if c in ops.columns]),
                                 pd.Series(exclues.values, index=ops.index, name="EXCLUE"),
                                 detail_ops.reindex(ops.index)], axis=1) if len(ops) else ops,
        "engagements": eng, "fp": fp,
    }
