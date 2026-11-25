"""Analyse de titre — Pricer : prix, échéancier et indicateurs d'un BDT."""

from __future__ import annotations

import time

import re
from datetime import date

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, ctx, dash_table, dcc, html, no_update
from dash.dash_table.Format import Format, Scheme, Sign
from dash.exceptions import PreventUpdate

import base_courbe as db
import base_titre as BT
import pricer as PR
import theme as T
from composants import carte, cls_signe, etiquette, fdate, fmt, graphe, mise_en_page, note, segment, table, toast, signaux

dash.register_page(__name__, path="/pricer", name="Pricer", order=10, section="titre")

TENORS = db.TENORS
F3 = Format(precision=3, scheme=Scheme.fixed)
F_PB = Format(precision=1, scheme=Scheme.fixed, sign=Sign.positive)
STYLE_TABLE = dict(
    style_as_list_view=True,
    style_table={"overflowX": "auto"},
    style_header={"backgroundColor": "transparent", "color": "var(--gris)", "fontWeight": 600,
                  "fontSize": "0.62rem", "letterSpacing": "0.12em", "textTransform": "uppercase",
                  "border": "none", "borderBottom": "1px solid var(--trait)", "padding": "0.5rem 0.6rem",
                  "textAlign": "right"},
    style_cell={"backgroundColor": "transparent", "color": "var(--encre)", "border": "none",
                "borderBottom": "1px solid var(--trait-doux)", "fontFamily": "inherit", "fontSize": "0.84rem",
                "padding": "0.45rem 0.6rem", "textAlign": "right", "fontVariantNumeric": "tabular-nums"},
)


def mad(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:,.2f}".replace(",", " ")


def champ(lib: str, composant, id_bloc: str | None = None, classe: str = "") -> html.Div:
    return html.Div(className=f"champ champ-fiche {classe}", id=id_bloc or "", children=[html.Label(lib), composant]) \
        if id_bloc else html.Div(className=f"champ champ-fiche {classe}", children=[html.Label(lib), composant])


def entree(id_: str, type_: str = "number", **kw) -> dcc.Input:
    return dcc.Input(id=id_, type=type_, className="input-date input-champ", debounce=True, **kw)


def jour_fr(id_: str, valeur: str | None = None) -> dcc.DatePickerSingle:
    """Sélecteur de date au format français JJ/MM/AAAA (valeur interne ISO AAAA-MM-JJ)."""
    return dcc.DatePickerSingle(id=id_, date=valeur, display_format="DD/MM/YYYY", placeholder="JJ/MM/AAAA",
                                first_day_of_week=1, month_format="MMMM YYYY", show_outside_days=True,
                                className="date-fr")


def ligne_info(lib: str, val, large: bool = False) -> html.Div:
    return html.Div(className="info-ligne" + (" large" if large else ""),
                    children=[html.Span(lib, className="info-lib"), html.Span(val or "—", className="info-val")])


# ---------------------------------------------------------------------------
# Mise en page
# ---------------------------------------------------------------------------

fiche = html.Div(className="carte fiche", children=[
    etiquette("Titre"),
    dcc.Input(id="pr-code", type="text", list="pr-codes", debounce=True, autoComplete="off",
              className="input-date input-champ input-code",
              placeholder="Code du titre (base) — vide pour une nouvelle ligne"),
    html.Datalist(id="pr-codes"),
    html.Div(id="pr-titre-info"),
    html.Div(className="separateur"),
    etiquette("Caractéristiques"),
    champ("Type", segment("pr-type", [(v, k) for k, v in PR.TYPES.items()], "in_fine")),
    html.Div(className="form-grille", children=[
        champ("Nominal (MAD)", entree("pr-nominal", value=100000, step="any")),
        champ("Taux facial (%)", entree("pr-tf", value=2.80, step="any")),
        champ("Date d'émission", jour_fr("pr-em", "2024-03-15")),
        champ("Date de jouissance", jour_fr("pr-jouis", "2024-03-15")),
        champ("Date d'échéance", jour_fr("pr-ech", "2029-03-15")),
        champ("Prime de risque (pb)", entree("pr-prime", value=0, step="any")),
    ]),
    html.Div(id="pr-bloc-rev", className="sous-bloc", children=[
        champ("Dates de révision (jj/mm/aaaa, séparées par ;)",
              dcc.Textarea(id="pr-revisions", value="15/03/2027 ; 15/03/2030", className="input-champ zone-texte",
                           placeholder="15/03/2027 ; 15/03/2028")),
        dcc.Checklist(id="pr-rev-amorti", className="case",
                      options=[{"label": "Capital amortissable (valorisé sur le capital restant dû)", "value": "oui"}],
                      value=[]),
    ]),
    html.Div(id="pr-bloc-amort", className="sous-bloc", children=[
        champ("Fréquence", segment("pr-freq", [(v, k) for k, v in PR.FREQUENCES.items()], "annuel")),
        champ("Amortissement", segment("pr-amort", [(v, k) for k, v in PR.AMORTISSEMENTS.items()], "lineaire")),
        champ("Délai de grâce (années, facultatif)",
              entree("pr-grace", min=0, step="any", placeholder="vide = amortissement dès la 1re échéance")),
        html.Div(id="pr-bloc-montant", children=[champ("Montant amorti par échéance (MAD)",
                                                       entree("pr-montant", value=10000, step="any"))]),
    ]),
    html.Div(className="separateur"),
    etiquette("Valorisation"),
    html.Div(className="form-grille", children=[
        champ("Date de valorisation", jour_fr("pr-valo")),
        champ("Date de la courbe", jour_fr("pr-date-courbe")),
    ]),
    html.Div(id="pr-source-courbe", className="note"),
    html.Button("Télécharger la courbe depuis BKAM", id="pr-bkam", className="btn btn-lien"),
    champ("Taux de rendement", segment("pr-source", [(v, k) for k, v in PR.SOURCES.items()], "courbe")),
    champ("Taux négocié (%)", entree("pr-negocie", step="any", placeholder="taux de la courbe par défaut")),
])

layout = html.Div(className="page", children=[
    dcc.Download(id="pr-dl"),
    html.Div(className="entete", children=[
        html.Div("Pricer", className="titre-fin"),
        html.Div("Valorisation et analyse d'un BDT", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),
    html.Div(className="grille-fiche", children=[
        fiche,
        html.Div(className="colonne", children=[
            html.Div(id="pr-message"),
            html.Div(className="carte", children=[
                html.Div(className="carte-tete", children=[
                    etiquette("Résultat", None),
                    html.Button("Exporter", id="pr-export", className="btn btn-secondaire"),
                ]),
                html.Div(id="pr-kpi", className="kpi-grille"),
            ]),
            carte("Taux de rendement", "trois méthodes, prix correspondants",
                  html.Div(id="pr-encadrants", className="info info-legere"),
                  html.Div(id="pr-comparaison", className="tableau-defile")),
            carte("Ténors", "modifiez un ténor (négociation) : taux et prix « Ténors encadrants » suivent",
                  dash_table.DataTable(
                      id="pr-tenors", data=[], **STYLE_TABLE,
                      columns=[{"id": "tenor", "name": "Ténor", "editable": False},
                               {"id": "jours", "name": "Maturité (j)", "type": "numeric", "editable": False},
                               {"id": "bkam", "name": "Taux BKAM", "type": "numeric", "format": F3, "editable": False},
                               {"id": "negocie", "name": "Taux négocié ✎", "type": "numeric", "format": F3,
                                "editable": True},
                               {"id": "ecart", "name": "Écart (pb)", "type": "numeric", "format": F_PB,
                                "editable": False}]),
                  outils=html.Button("Réinitialiser", id="pr-reset-tenors", className="btn btn-secondaire")),
        ]),
    ]),
    carte("Échéancier", "flux restants à la date de valorisation", html.Div(id="pr-echeancier", className="tableau-defile"),
          graphe("pr-g-flux", 300)),
    html.Div(className="grille-2", children=[
        carte("Chocs de taux", "re-pricing exact et approximation sensibilité + convexité",
              html.Div(id="pr-chocs", className="tableau-defile")),
        carte("Méthode", None, note(
            "In fine et révisable : valo_ligne (tous les flux au taux de la maturité, monétaire en dessous d'un an). "
            "Un révisable est valorisé comme un in fine échéant à sa prochaine date de révision. Amortissable : "
            "valo_ligne_zc (chaque flux au taux zéro-coupon de sa maturité ; la prime de risque s'ajoute à chacun).",
            "Taux « Courbe BKAM » : maturité résiduelle interpolée sur la courbe de marché de la date choisie "
            "(taux_courbe). « Ténors encadrants » : même règle, mais entre les deux ténors qui entourent la "
            "maturité, éventuellement renégociés. « Taux négocié » : saisi librement. Pour un amortissable, les "
            "deux premières sources donnent la courbe zéro-coupon, le taux négocié est un taux actuariel unique.",
            "Calculé à partir de l'échéancier : duration de Macaulay = Σ t·VA / P ; sensibilité = Σ t·VA/(1+y) / P "
            "(monétaire : (n/360)/(1 + y·n/360)) ; convexité = Σ t(t+1)·VA/(1+y)² / P ; TRI = taux actuariel "
            "unique qui redonne le prix. PPC = (prix − coupon couru) / nominal (capital restant dû pour un "
            "amortissable). Point mort 3 mois = hausse de taux absorbée par 3 mois de rendement.")),
    ]),
])
layout.children.append(signaux(['pricer-toast-0', 'pricer-toast-1', 'pricer-maj-2']))


# ---------------------------------------------------------------------------
# Lecture de la fiche
# ---------------------------------------------------------------------------

def _date(v) -> date | None:
    if not v:
        return None
    v = str(v).strip()
    for f in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return pd.to_datetime(v, format=f).date()
        except ValueError:
            continue
    return None


def _revisions(texte: str | None) -> list[date]:
    if not texte:
        return []
    return sorted(d for d in (_date(x) for x in re.split(r"[;,\s]+", texte)) if d)


def lire_fiche(type_, nominal, tf, em, jouis, ech, prime, revisions, rev_amorti, freq, amort, montant, valo,
               grace=None, code=None) -> dict:
    """Fiche saisie ; code connu : base du coupon (METHODE_COUPON) et coupon imposé repris de base_titre.xlsx."""
    f = None
    if code and str(code).strip():
        try:
            f = BT.fiche(str(code).strip())
        except Exception:
            f = None
    return {"base_coupon": f["base_coupon"] if f else None, "coupon_fixe": f.get("coupon_fixe") if f else None,
            "echeancier": f.get("echeancier") if f else None, "nominal_ref": f.get("nominal_ref") if f else None,
            "type": type_, "nominal": float(nominal or 0), "tf": float(tf or 0), "prime": float(prime or 0),
            "em": _date(em), "jouis": _date(jouis), "ech": _date(ech), "valo": _date(valo),
            "revisions": _revisions(revisions), "rev_amorti": type_ == "revisable" and bool(rev_amorti),
            "freq": freq or "annuel", "amortissement": amort or "lineaire",
            "montant": float(montant) if montant else None,
            "grace": float(grace) if grace not in (None, "") and float(grace) > 0 else None}


def charger_courbe(d: str) -> tuple[pd.DataFrame, str]:
    with db.connexion() as conn:
        brute = db.lire_courbe_bkam(conn, d)
        tenors = db.lire_tenors(conn, d)
    if len(brute) >= 2:
        return PR.df_points(brute), \
            f"Courbe BKAM du {fdate(d)} · {len(brute)} points de marché."
    if tenors:
        return PR.df_tenors(tenors), (f"Pas de points de marché pour le {fdate(d)} : courbe reconstituée à partir "
                                      "des 9 ténors historisés.")
    return pd.DataFrame(), f"Aucune courbe en base pour le {fdate(d)}."


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("pr-bloc-rev", "style"), Output("pr-bloc-amort", "style"), Output("pr-bloc-montant", "style"),
          Input("pr-type", "value"), Input("pr-amort", "value"), Input("pr-rev-amorti", "value"))
def visibilite(type_, amort, rev_amorti):
    cache, vu = {"display": "none"}, {}
    amorti = type_ == "amortissable" or (type_ == "revisable" and bool(rev_amorti))
    return (vu if type_ == "revisable" else cache, vu if amorti else cache,
            vu if amorti and amort == "constant" else cache)


@callback(Output("pr-codes", "children"), Input("maj", "data"), Input("pr-codes", "id"))
def liste_codes(_, _montage):
    return [html.Option(value=o["value"], label=o["label"]) for o in BT.options_codes()]


@callback(
    Output("pr-titre-info", "children"),
    Output("pr-type", "value"), Output("pr-nominal", "value"), Output("pr-tf", "value"),
    Output("pr-em", "date"), Output("pr-jouis", "date"), Output("pr-ech", "date"), Output("pr-prime", "value"),
    Output("pr-revisions", "value"), Output("pr-rev-amorti", "value"), Output("pr-freq", "value"),
    Output("pr-negocie", "value"), Output("pr-grace", "value"),
    Input("pr-code", "value"),
)
def charger_titre(code):
    """Code existant : la fiche est remplie depuis base_titre.xlsx ; code inconnu : nouvelle ligne."""
    rien = [no_update] * 12
    code = (code or "").strip()
    if not code:
        return None, *rien
    try:
        f = BT.fiche(code)
    except Exception as e:
        return html.Div(f"Lecture de base_titre.xlsx impossible : {e}", className="alerte"), *rien
    if f is None:
        return html.Div(className="titre-carte nouveau", children=[
            html.Div("Nouvelle ligne", className="titre-desc"),
            html.Div(f"Le code {code} n'est pas dans la base ({BT.info_base()}) : saisissez les "
                     "caractéristiques ci-dessous.", className="titre-note")]), *rien

    infos = html.Div(className="titre-carte", children=[
        html.Div(f["description"] or code, className="titre-desc"),
        html.Div(className="info-grille", children=[
            ligne_info("ISIN", f["isin"]), ligne_info("Classe", " · ".join(x for x in (f["classe"], f["categorie"]) if x)),
            ligne_info("Émetteur", f["emetteur"]), ligne_info("Détention", f["detention"]),
            ligne_info("Type de taux", f["type_taux"]), ligne_info("Méthode valo", f["methode"]),
            ligne_info("Coupon", f["periodicite_coupon"]), ligne_info("Remboursement", f["periodicite_rembou"]),
            ligne_info("Base de calcul", f["base_calcul"]), ligne_info("MAJ du taux", fdate(f["date_maj"]) if f["date_maj"] else "—"),
        ]),
        *[html.Div(a, className="titre-alerte") for a in f["alertes"]],
        html.Div(BT.info_base(), className="titre-note"),
    ])
    if not f["valorisable"]:
        infos.children.insert(2, html.Div("Pas de caractéristiques de pricing dans la base pour ce titre "
                                          f"(classe {f['classe'] or '—'}).", className="titre-alerte"))
        return infos, *rien
    iso = lambda d: d.isoformat() if d else None
    revs = " ; ".join(f"{d:%d/%m/%Y}" for d in f["revisions"])
    return (infos, f["type"], f["nominal"], f["tf"], iso(f["em"]), iso(f["jouis"]), iso(f["ech"]), f["prime"],
            revs, ["oui"] if f["rev_amorti"] else [], f["freq"], None, f.get("grace"))


@callback(Output("pr-valo", "date"), Output("pr-date-courbe", "date"),
          Input("maj", "data"), Input("pr-valo", "id"), State("pr-valo", "date"), State("pr-date-courbe", "date"))
def dates_defaut(_, _montage, valo, dc):
    if valo and dc:
        raise PreventUpdate
    with db.connexion() as conn:
        d = conn.execute("SELECT MAX(date_courbe) FROM courbe_bkam").fetchone()[0] \
            or conn.execute("SELECT MAX(date) FROM tenors").fetchone()[0]
    if not d:
        raise PreventUpdate
    return valo or d, dc or d


@callback(Output("pr-tenors", "data"), Output({"type": "sig-maj", "id": "pricer-maj-2"}, "data"),
          Output({"type": "sig-toast", "id": "pricer-toast-0"}, "data"),
          Input("pr-date-courbe", "date"), Input("pr-reset-tenors", "n_clicks"),
          Input("pr-tenors", "data_timestamp"), Input("pr-bkam", "n_clicks"),
          State("pr-tenors", "data"), State("maj", "data"), prevent_initial_call=True)
def tenors(d, _r, _t, n_bkam, lignes, maj):
    if not d:
        raise PreventUpdate
    declencheur, maj_out, msg = ctx.triggered_id, no_update, no_update
    if declencheur == "pr-bkam":
        if not n_bkam:
            raise PreventUpdate
        try:
            try:
                import COURBE as courbe
            except ImportError:
                import Courbe as courbe
            dj = date.fromisoformat(d)
            brute = courbe.get_bkam_taux_reference(dj)
            if len(brute) < 2:
                return no_update, no_update, toast(f"BKAM n'a pas publié de courbe pour le {fdate(d)}.", "erreur")
            tab = courbe.tableau_tenors(brute, dj, formate=False)
            with db.connexion() as conn:
                db.enregistrer_courbe(conn, dj, dict(zip(tab["Ténor"], tab.iloc[:, 2])), brute, source="bkam")
            maj_out, msg = time.time(), toast(f"Courbe du {fdate(d)} téléchargée et historisée.")
        except Exception as e:
            return no_update, no_update, toast(f"Échec du téléchargement BKAM : {e}", "erreur")
    if declencheur == "pr-tenors" and lignes:
        for l in lignes:
            n = pd.to_numeric(l.get("negocie"), errors="coerce")
            l["negocie"] = None if pd.isna(n) else round(float(n), 4)
            l["ecart"] = None if pd.isna(n) else round((float(n) - float(l["bkam"])) * 100, 2)
        return lignes, maj_out, msg
    with db.connexion() as conn:
        t = db.lire_tenors(conn, d)
    if not t:
        return [], maj_out, msg
    return [{"tenor": k, "jours": j, "bkam": t[k], "negocie": t[k], "ecart": 0.0} for k, j in TENORS.items()], \
        maj_out, msg


@callback(
    Output("pr-message", "children"), Output("pr-kpi", "children"), Output("pr-encadrants", "children"),
    Output("pr-comparaison", "children"), Output("pr-echeancier", "children"), Output("pr-g-flux", "figure"),
    Output("pr-chocs", "children"), Output("pr-tenors", "style_data_conditional"),
    Output("pr-source-courbe", "children"), Output("pr-negocie", "placeholder"),
    Input("pr-type", "value"), Input("pr-nominal", "value"), Input("pr-tf", "value"), Input("pr-em", "date"),
    Input("pr-jouis", "date"), Input("pr-ech", "date"), Input("pr-prime", "value"),
    Input("pr-revisions", "value"), Input("pr-rev-amorti", "value"), Input("pr-freq", "value"),
    Input("pr-amort", "value"),
    Input("pr-montant", "value"), Input("pr-grace", "value"), Input("pr-valo", "date"), Input("pr-date-courbe", "date"),
    Input("pr-source", "value"), Input("pr-negocie", "value"), Input("pr-tenors", "data"),
    Input("maj", "data"), Input("ui", "data"), State("pr-code", "value"),
)
def calculer(type_, nominal, tf, em, jouis, ech, prime, revisions, rev_amorti, freq, amort, montant, grace, valo, d_courbe,
             source, negocie, tenors_data, _, ui, code):
    style_base = [
        {"if": {"column_id": "negocie"}, "backgroundColor": "var(--editable)", "fontWeight": 600},
        {"if": {"column_id": "tenor"}, "textAlign": "left", "color": "var(--navy)", "fontWeight": 600},
        {"if": {"filter_query": "{ecart} > 0.05", "column_id": "ecart"}, "color": "var(--rouge)", "fontWeight": 600},
        {"if": {"filter_query": "{ecart} < -0.05", "column_id": "ecart"}, "color": "var(--vert)", "fontWeight": 600},
    ]
    if not (valo and d_courbe and tenors_data):
        raise PreventUpdate
    vide = [no_update] * 6
    courbe, info_courbe = charger_courbe(d_courbe)
    if courbe.empty:
        return html.Div(info_courbe, className="alerte"), *vide, style_base, info_courbe, no_update

    p = lire_fiche(type_, nominal, tf, em, jouis, ech, prime, revisions, rev_amorti, freq, amort, montant, valo, grace, code)
    manquants = [k for k in ("em", "jouis", "ech", "valo") if p[k] is None]
    if manquants or p["nominal"] <= 0:
        return html.Div("Complétez la fiche : dates et nominal.", className="alerte"), *vide, style_base, \
            info_courbe, no_update
    tenors = {l["tenor"]: float(l["negocie"] if l.get("negocie") is not None else l["bkam"]) for l in tenors_data}

    try:
        R = PR.pricer(p, courbe, tenors, source or "courbe", negocie)
    except Exception as e:
        return html.Div(f"Calcul impossible : {e}", className="alerte"), *vide, style_base, info_courbe, no_update

    # Indicateurs
    sur_crd = type_ == "amortissable" or p["rev_amorti"]
    unite_ppc = "du capital restant dû" if sur_crd else "du nominal"
    k = [
        ("Prix (MAD)", mad(R["valeur"]), f"{R['prix_pct']:.3f} % {unite_ppc}, coupon couru inclus"),
        ("Prix pied de coupon", f"{R['ppc_pct']:.3f} %",
         "(prix − coupon couru) / " + ("CRD" if sur_crd else "nominal")),
        ("Coupon couru (MAD)", mad(R["cc"]), f"depuis le {fdate(R['depart_cc'])}"),
        (f"Taux · {PR.SOURCES[R['source']]}", f"{R['taux_utilise']:.3f} %",
         f"prime {p['prime']:+.0f} pb" if p["prime"] else ("TRI de la courbe ZC" if type_ == "amortissable" else "taux de valorisation")),
        ("TRI (actuariel)", f"{R['tri']:.3f} %", "taux unique qui redonne le prix"),
        ("Sensibilité", f"{R['sensibilite']:.3f}", f"effective (± 1 pb) {R['sensibilite_effective']:.3f}"),
        ("Convexité", f"{R['convexite']:.2f}", "à partir de l'échéancier"),
        ("Duration (Macaulay)", f"{R['duration']:.3f} ans", f"{R['nb_flux']} flux restants"),
        ("DV01 (MAD)", mad(R["dv01"]), "variation du prix pour 1 pb"),
        ("Maturité résiduelle", f"{R['jours']:,} j".replace(",", " "),
         f"{R['jours'] / 365.25:.2f} ans · échéance {'révision ' if type_ == 'revisable' else ''}{fdate(R['ech_eff'])}"),
        ("Rendement courant", f"{R['rendement_courant']:.3f} %", "coupon annuel / prix pied de coupon"),
        ("Point mort 3 mois", f"{R['point_mort_3m']:.0f} pb", "hausse de taux absorbée en 3 mois"),
        ("Prime / décote", f"{R['prime_decote']:+.3f} %", "PPC − 100 %"),
        ("Taux facial − rendement", f"{R['ecart_facial']:+.1f} pb", "coupon au-dessus du marché si > 0"),
    ]
    if p["rev_amorti"]:
        k += [("Capital restant dû", mad(R["crd"]), f"sur un nominal de {mad(p['nominal'])}")]
    if type_ == "amortissable":
        k += [("Capital restant dû", mad(R["crd"]), "après les amortissements passés"),
              ("Durée de vie moyenne", f"{R['wal']:.2f} ans",
               "moyenne des échéances d'amortissement" + (f" · grâce {p['grace']:g} an(s)" if p["grace"] else ""))]
    kpis = [html.Div(className="kpi kpi-mini", children=[html.Div(a, className="kpi-lib"),
                                                         html.Div(b, className="kpi-val"),
                                                         html.Div(c, className="kpi-sous")]) for a, b, c in k]

    # Ténors encadrants
    if R["encadrants"]:
        ti, ts = R["encadrants"]
        txt = (f"Maturité résiduelle {R['jours']} j : en dessous du premier ténor, taux du {ti}."
               if ti == ts else
               f"Maturité résiduelle {R['jours']} j, encadrée par {ti} ({TENORS[ti]} j, {tenors[ti]:.3f} %) "
               f"et {ts} ({TENORS[ts]} j, {tenors[ts]:.3f} %) → taux interpolé {R['taux']['tenors']:.3f} % "
               f"(courbe BKAM : {R['taux']['courbe']:.3f} %).")
        encadres = [ti, ts]
    else:
        txt = ("Amortissable : chaque flux est actualisé au taux zéro-coupon de sa maturité ; « Courbe BKAM » et "
               "« Ténors encadrants » diffèrent par la courbe utilisée pour le zéro-coupon. Les taux affichés sont "
               "les TRI correspondants.")
        encadres = []
    style = style_base + [{"if": {"filter_query": f'{{tenor}} = "{t}"'}, "backgroundColor": "var(--surligne)"}
                          for t in encadres]

    comp = R["comparaison"].set_index("Source")
    spec = [{"col": "Taux (%)", "lib": "Taux", "genre": "pct", "cls": "j"},
            {"col": "Écart taux vs courbe (pb)", "lib": "vs courbe (pb)", "genre": "pb", "cls": cls_signe},
            {"col": "Prix", "lib": "Prix (MAD)", "genre": "mad", "cls": "j"},
            {"col": "PPC (%)", "lib": "PPC", "genre": "pct", "cls": "ref"},
            {"col": "Écart prix vs courbe (MAD)", "lib": "vs courbe (MAD)", "genre": "mad+", "cls": "ref"}]
    t_comp = table(comp, spec, premiere="Méthode",
                   styles=lambda idx, col, v: {"background": "var(--surligne)"}
                   if idx == PR.SOURCES[R["source"]] else None)

    # Échéancier
    tab = R["tab"].copy()
    tab["Facteur"] = tab["flux_actualise"] / tab["flux"]
    tab["Poids (%)"] = tab["flux_actualise"] / R["valeur"] * 100
    tab["Contribution duration"] = tab["maturite_residuelle_en_annee"] * tab["flux_actualise"] / R["valeur"]
    aff = tab.set_index("date_de_reglement")
    spec_e = [{"col": "maturite_residuelle_en_annee", "lib": "Maturité (ans)", "genre": "n3", "cls": "ref"},
              {"col": "taux_de_valorisation", "lib": "Taux de valo.", "genre": "pct", "cls": "ref"},
              {"col": "flux", "lib": "Flux (MAD)", "genre": "mad", "cls": "j"},
              {"col": "Facteur", "lib": "Facteur d'act.", "genre": "n3", "cls": "ref"},
              {"col": "flux_actualise", "lib": "Flux actualisé", "genre": "mad", "cls": "j"},
              {"col": "Poids (%)", "lib": "Poids", "genre": "n2", "cls": "ref"},
              {"col": "Contribution duration", "lib": "Contrib. duration", "genre": "n3", "cls": "ref"}]
    t_ech = table(aff, spec_e, premiere="Date de règlement", dense="dense")

    x = pd.to_datetime(tab["date_de_reglement"]).dt.strftime("%d/%m/%Y")
    fig = go.Figure()
    fig.add_bar(x=x, y=tab["flux"], name="Flux", marker_color=T.CIEL,
                hovertemplate="%{x} : %{y:,.2f}<extra>Flux</extra>")
    fig.add_bar(x=x, y=tab["flux_actualise"], name="Flux actualisé", marker_color=T.NAVY,
                hovertemplate="%{x} : %{y:,.2f}<extra>Flux actualisé</extra>")
    fig = mise_en_page(fig, 300, barmode="group", bargap=0.45 if len(x) > 3 else 0.6)
    fig.update_xaxes(type="category")

    ch = R["chocs"].set_index("Choc (pb)")
    spec_c = [{"col": "Prix", "lib": "Prix (MAD)", "genre": "mad", "cls": "j"},
              {"col": "Variation (MAD)", "lib": "Var. (MAD)", "genre": "mad+", "cls": cls_signe},
              {"col": "Variation (%)", "lib": "Var. exacte (%)", "genre": "n3", "cls": "j"},
              {"col": "Approximation S + C (%)", "lib": "Approx. S + C (%)", "genre": "n3", "cls": "ref"},
              {"col": "Écart d'approximation (pb de prix)", "lib": "Écart (pb)", "genre": "n2", "cls": "ref"}]
    ch.index = [f"{c:+d} pb" for c in ch.index]
    t_chocs = table(ch, spec_c, premiere="Choc", dense="dense")

    placeholder = f"{R['taux']['courbe']:.3f} (taux de la courbe)" if type_ != "amortissable" \
        else f"{R['taux']['courbe']:.3f} (TRI de la courbe)"
    return None, kpis, txt, t_comp, t_ech, T.theme_fig(fig, ui), t_chocs, style, info_courbe, placeholder


@callback(Output("pr-dl", "data"), Output({"type": "sig-toast", "id": "pricer-toast-1"}, "data"),
          Input("pr-export", "n_clicks"),
          State("pr-type", "value"), State("pr-nominal", "value"), State("pr-tf", "value"), State("pr-em", "date"),
          State("pr-jouis", "date"), State("pr-ech", "date"), State("pr-prime", "value"),
          State("pr-revisions", "value"), State("pr-rev-amorti", "value"), State("pr-freq", "value"),
          State("pr-amort", "value"),
          State("pr-montant", "value"), State("pr-grace", "value"), State("pr-valo", "date"), State("pr-date-courbe", "date"),
          State("pr-source", "value"), State("pr-negocie", "value"), State("pr-tenors", "data"),
          State("pr-code", "value"),
          prevent_initial_call=True)
def exporter(n, type_, nominal, tf, em, jouis, ech, prime, revisions, rev_amorti, freq, amort, montant, grace, valo, d_courbe,
             source, negocie, tenors_data, code):
    if not n:
        raise PreventUpdate
    courbe, _ = charger_courbe(d_courbe)
    p = lire_fiche(type_, nominal, tf, em, jouis, ech, prime, revisions, rev_amorti, freq, amort, montant, valo, grace, code)
    tenors = {l["tenor"]: float(l["negocie"] if l.get("negocie") is not None else l["bkam"]) for l in tenors_data or []}
    try:
        R = PR.pricer(p, courbe, tenors, source or "courbe", negocie)
    except Exception as e:
        return no_update, toast(f"Export impossible : {e}", "erreur")
    f = BT.fiche(code) if code else None
    infos = [("Code", code or "nouvelle ligne")] + ([
        ("Description", f["description"]), ("ISIN", f["isin"]), ("Classe", f["classe"]),
        ("Catégorie", f["categorie"]), ("Émetteur", f["emetteur"]), ("Détention", f["detention"])] if f else [])
    fiche_df = pd.DataFrame(infos + [
        ("Type", PR.TYPES[type_] + (" amortissable" if p["rev_amorti"] else "")), ("Nominal", p["nominal"]), ("Taux facial (%)", p["tf"]),
        ("Date d'émission", fdate(p["em"])), ("Date de jouissance", fdate(p["jouis"])),
        ("Date d'échéance", fdate(p["ech"])), ("Échéance retenue", fdate(R["ech_eff"])),
        ("Date de valorisation", fdate(p["valo"])), ("Date de la courbe", fdate(d_courbe)),
        ("Source du taux", PR.SOURCES[R["source"]]), ("Taux utilisé (%)", R["taux_utilise"]),
        ("Prime de risque (pb)", p["prime"]),
        ("Délai de grâce (années)", p["grace"] if p["grace"] else "aucun"), ("Prix (MAD)", R["valeur"]), ("Coupon couru (MAD)", R["cc"]),
        ("PPC (%)", R["ppc_pct"]), ("TRI (%)", R["tri"]), ("Sensibilité", R["sensibilite"]),
        ("Convexité", R["convexite"]), ("Duration (ans)", R["duration"]), ("DV01 (MAD)", R["dv01"]),
        ("Rendement courant (%)", R["rendement_courant"]), ("Point mort 3 mois (pb)", R["point_mort_3m"]),
    ], columns=["Paramètre", "Valeur"]).astype({"Valeur": str})
    nom = f"pricer_{(code or 'ligne').strip()}_{(valo or '').replace('-', '')}.xlsx"
    return dcc.send_bytes(db.exporter_classeur({
        "Fiche": fiche_df, "Échéancier": R["tab"], "Taux et prix": R["comparaison"].drop(columns="code"),
        "Chocs": R["chocs"], "Ténors": pd.DataFrame(tenors_data or []),
    }), nom), toast("Valorisation exportée en Excel.")
