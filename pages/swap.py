"""Analyse de titre — Swap : quantités équivalentes en montant, en sensibilité, ou chez un même émetteur."""

from __future__ import annotations

import time

import dash
import numpy as np
import pandas as pd
from dash import Input, Output, State, callback, ctx, dash_table, dcc, html, no_update
from dash.dash_table.Format import Format, Group, Scheme, Sign
from dash.exceptions import PreventUpdate

import base_courbe as db
import base_titre as BT
import swap as SW
from composants import carte, etiquette, fdate, note, toast, signaux

dash.register_page(__name__, path="/swap", name="Swap", order=20, section="titre")

F0 = Format(precision=0, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ")
F2 = Format(precision=2, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ")
F3 = Format(precision=3, scheme=Scheme.fixed)
F3S = Format(precision=3, scheme=Scheme.fixed, sign=Sign.positive)
F1 = Format(precision=1, scheme=Scheme.fixed)

STYLE_TABLE = dict(
    style_as_list_view=True,
    style_table={"overflowX": "auto"},
    style_header={"backgroundColor": "transparent", "color": "var(--gris)", "fontWeight": 600,
                  "fontSize": "0.6rem", "letterSpacing": "0.1em", "textTransform": "uppercase",
                  "border": "none", "borderBottom": "1px solid var(--trait)", "padding": "0.5rem 0.55rem",
                  "textAlign": "right", "whiteSpace": "normal"},
    style_cell={"backgroundColor": "transparent", "color": "var(--encre)", "border": "none",
                "borderBottom": "1px solid var(--trait-doux)", "fontFamily": "inherit", "fontSize": "0.8rem",
                "padding": "0.42rem 0.55rem", "textAlign": "right", "fontVariantNumeric": "tabular-nums"},
    style_cell_conditional=[
        {"if": {"column_id": "code"}, "textAlign": "left", "fontWeight": 600, "color": "var(--navy)"},
        {"if": {"column_id": "description"}, "textAlign": "left", "maxWidth": "300px", "overflow": "hidden",
         "textOverflow": "ellipsis", "whiteSpace": "nowrap"},
        {"if": {"column_id": "emetteur"}, "textAlign": "left"},
        {"if": {"column_id": "categorie"}, "textAlign": "left"},
        {"if": {"column_id": "type_lib"}, "textAlign": "left"},
    ],
    style_header_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                              for c in ("code", "description", "emetteur", "categorie", "type_lib")],
    style_data_conditional=[
        {"if": {"column_id": "q_sensi_arr"}, "fontWeight": 700},
        {"if": {"column_id": "q_montant_arr"}, "fontWeight": 700},
    ],
    tooltip_delay=300, tooltip_duration=None,
    sort_action="native", page_action="native", page_size=15, row_selectable="single", cell_selectable=False,
)

COL_BASE = [
    {"id": "code", "name": "Code"}, {"id": "description", "name": "Description"},
    {"id": "emetteur", "name": "Émetteur"}, {"id": "categorie", "name": "Cat."}, {"id": "type_lib", "name": "Type"},
    {"id": "echeance_txt", "name": "Échéance"}, {"id": "maturite", "name": "Mat. (ans)", "type": "numeric", "format": F1},
    {"id": "spread", "name": "Spread (pb)", "type": "numeric", "format": F1},
    {"id": "taux", "name": "Taux (%)", "type": "numeric", "format": F3},
    {"id": "prix", "name": "Prix plein", "type": "numeric", "format": F2},
    {"id": "sensibilite", "name": "Sensi", "type": "numeric", "format": F3},
]
COL_SENSI = COL_BASE + [
    {"id": "ecart_sensi", "name": "Écart sensi", "type": "numeric", "format": F3S},
    {"id": "q_sensi_arr", "name": "Qté swap sensi", "type": "numeric", "format": F0},
    {"id": "montant_sensi", "name": "Montant swap sensi", "type": "numeric", "format": F2},
]
COL_EMET = [c for c in COL_BASE if c["id"] != "emetteur"] + [
    {"id": "q_montant_arr", "name": "Qté swap montant", "type": "numeric", "format": F0},
    {"id": "q_sensi_arr", "name": "Qté swap sensi", "type": "numeric", "format": F0},
]


def mad(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:,.2f}".replace(",", " ")


def qte(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:,.0f}".replace(",", " ")


def champ(lib, comp, classe=""):
    return html.Div(className=f"champ {classe}", children=[html.Label(lib), comp])


def jour_fr(id_):
    return dcc.DatePickerSingle(id=id_, display_format="DD/MM/YYYY", placeholder="JJ/MM/AAAA", first_day_of_week=1,
                                month_format="MMMM YYYY", clearable=True, className="date-fr")


def code_input(id_, placeholder="Code du titre"):
    return dcc.Input(id=id_, type="text", list="sw-codes", debounce=True, autoComplete="off",
                     className="input-date input-champ input-code", placeholder=placeholder)


def nombre(id_, className="input-date input-champ", **kw):
    return dcc.Input(id=id_, type="number", step="any", debounce=True, className=className, **kw)


# ---------------------------------------------------------------------------
# Mise en page
# ---------------------------------------------------------------------------

def table(id_, colonnes):
    return dash_table.DataTable(id=id_, columns=colonnes, data=[], **STYLE_TABLE)


layout = html.Div(className="page", children=[
    dcc.Download(id="sw-dl"),
    html.Datalist(id="sw-codes"),
    html.Div(className="entete", children=[
        html.Div("Swap", className="titre-fin"),
        html.Div("Arbitrage de lignes : montant, sensibilité, émetteur", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),
    html.Div(className="carte barre", children=[
        champ("Date de valorisation", jour_fr("sw-valo")),
        champ("Date de la courbe", jour_fr("sw-courbe")),
        champ("Ligne 1 (à céder)", code_input("sw-code1", "Code de la ligne 1"), "champ-large"),
        champ("Quantité ligne 1", nombre("sw-q1", value=100, min=0)),
        html.Button("Exporter", id="sw-export", className="btn btn-secondaire"),
    ]),
    html.Div(id="sw-info-courbe", className="note"),
    html.Div(id="sw-ligne1"),
    dcc.Tabs(id="sw-onglets", value="montant", className="onglets", children=[
        dcc.Tab(label="Montant", value="montant", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(className="carte barre barre-interne", children=[
                        champ("Ligne 2 (à acheter)", code_input("sw-code2", "Code de la ligne 2"), "champ-large"),
                        html.Div("Quantité de la ligne 2 pour un montant identique : Q2 = Q1 × P1 / P2 "
                                 "(prix pleins, coupon couru inclus).", className="note"),
                    ]),
                    html.Div(id="sw-detail-montant"),
                ])),
        dcc.Tab(label="Sensi", value="sensi", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(className="carte barre barre-interne filtres", children=[
                        champ("Écart de sensi max (±)", nombre("sw-tol", value=0.5, min=0)),
                        champ("Émetteurs", dcc.Dropdown(id="sw-emetteurs", multi=True, placeholder="Tous",
                                                        className="liste"), "champ-large"),
                        champ("Type de ligne", dcc.Checklist(
                            id="sw-types", className="pastilles", inline=True,
                            options=[{"label": v, "value": k} for k, v in SW.TYPES.items()],
                            value=list(SW.TYPES))),
                        champ("Catégories", dcc.Dropdown(id="sw-categories", multi=True, placeholder="Toutes",
                                                         className="liste")),
                        champ("Spread (pb)", html.Div(className="periode", children=[
                            nombre("sw-smin", placeholder="min", className="input-date input-champ input-court"),
                            html.Span("→", className="fleche"),
                            nombre("sw-smax", placeholder="max", className="input-date input-champ input-court")])),
                    ]),
                    carte("Lignes de sensibilité proche", "triées par écart de sensibilité ; cochez une ligne "
                          "pour le détail du swap", table("sw-t-sensi", COL_SENSI),
                          html.Div(id="sw-n-sensi", className="note")),
                    html.Div(id="sw-detail-sensi"),
                ])),
        dcc.Tab(label="Émetteur", value="emetteur", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(className="carte barre barre-interne", children=[
                        champ("Échéance du", jour_fr("sw-ech-du")),
                        champ("Échéance au", jour_fr("sw-ech-au")),
                        html.Div(id="sw-emetteur-lib", className="note"),
                    ]),
                    carte("Lignes du même émetteur", "cochez une ligne pour les quantités en montant et en sensi",
                          table("sw-t-emet", COL_EMET), html.Div(id="sw-n-emet", className="note")),
                    html.Div(id="sw-detail-emet"),
                ])),
    ]),
    carte("Méthode", None, note(
        "Toutes les lignes vivantes de base_titre.xlsx sont valorisées sur la courbe BKAM de la date choisie, avec "
        "les règles du Pricer (in fine et révisable : valo_ligne ; amortissable : valo_ligne_zc, amortissement "
        "linéaire sans délai de grâce faute d'information dans la base). Prix = prix plein unitaire (coupon "
        "couru inclus) ; sensibilité calculée sur l'échéancier (révisable : jusqu'à la prochaine révision).",
        "Swap en montant : Q2 = Q1 × P1 / P2. Swap en sensibilité : Q2 = Q1 × P1 × S1 / (P2 × S2), soit la même "
        "exposition au taux (même DV01 = montant × sensibilité / 10 000). Les quantités sont arrondies à l'unité ; "
        "l'écart résiduel en montant et en DV01 est indiqué.")),
])
layout.children.append(signaux(['swap-toast-0']))


# ---------------------------------------------------------------------------
# Données
# ---------------------------------------------------------------------------

def _univers(valo, courbe):
    if not (valo and courbe):
        raise PreventUpdate
    return SW.univers(valo, courbe)


def _ligne(u: pd.DataFrame, code):
    code = (code or "").strip()
    return u.loc[code] if code and code in u.index else None


def _q1(q1):
    try:
        return max(float(q1), 0.0)
    except (TypeError, ValueError):
        return 0.0


def bloc_ligne(l: pd.Series, q: float | None = None, titre: str = "") -> html.Div:
    lignes = [
        ("Code", l["code"]), ("ISIN", l["isin"]), ("Émetteur", l["emetteur"]),
        ("Type", f"{l['type_lib']} · {l['categorie']}"), ("Échéance", fdate(l["echeance"])),
        ("Spread", f"{l['spread']:g} pb"), ("Taux facial", f"{l['tf']:.3f} %"),
        ("Taux de valo.", f"{l['taux']:.3f} %"), ("Prix plein", mad(l["prix"])), ("PPC", f"{l['ppc']:.3f} %"),
        ("Sensibilité", f"{l['sensibilite']:.3f}"), ("Duration", f"{l['duration']:.3f} ans"),
    ]
    if l["type"] == "revisable":
        lignes.insert(5, ("Prochaine révision", fdate(l["echeance_eff"])))
    return html.Div(className="swap-ligne", children=[
        html.Div(titre, className="swap-titre") if titre else None,
        html.Div(l["description"], className="titre-desc"),
        html.Div(className="info-grille info-grille-3", children=[
            html.Div(className="info-ligne", children=[html.Span(a, className="info-lib"), html.Span(b, className="info-val")])
            for a, b in lignes]),
    ])


def bloc_quantite(titre: str, r: dict, accent: bool, formule: str) -> html.Div:
    return html.Div(className="swap-q" + (" accent" if accent else ""), children=[
        html.Div(titre, className="swap-q-titre"),
        html.Div(qte(r["arrondie"]), className="swap-q-val"),
        html.Div(f"titres de la ligne 2 (exact : {r['exacte']:,.2f})".replace(",", " "), className="swap-q-sous"),
        html.Div(className="swap-q-grille", children=[
            html.Span("Montant ligne 2"), html.Span(mad(r["montant_2"])),
            html.Span("Écart de montant"), html.Span(mad(r["ecart_montant"]), className="neg" if r["ecart_montant"] < 0 else ""),
            html.Span("DV01 ligne 2"), html.Span(mad(r["dv01_2"])),
            html.Span("Écart de DV01"), html.Span(mad(r["ecart_dv01"])),
        ]),
        html.Div(formule, className="swap-q-formule"),
    ])


def detail(l1: pd.Series, q1: float, l2: pd.Series, mode: str) -> html.Div:
    r = SW.quantites(l1, q1, l2)
    return html.Div(className="carte", children=[
        html.Div(className="carte-tete", children=[etiquette("Détail du swap", f"{l1['code']} → {l2['code']}")]),
        html.Div(className="swap-grille", children=[
            bloc_ligne(l1, q1, f"Ligne 1 · {qte(q1)} titres · {mad(r['montant_1'])} MAD · DV01 {mad(r['dv01_1'])}"),
            bloc_ligne(l2, None, "Ligne 2"),
            html.Div(className="swap-qs", children=[
                bloc_quantite("Swap en montant", r["montant"], mode in ("montant", "emetteur"), "Q2 = Q1 × P1 / P2"),
                bloc_quantite("Swap en sensibilité", r["sensi"], mode in ("sensi", "emetteur"),
                              "Q2 = Q1 × P1 × S1 / (P2 × S2)"),
            ]),
        ]),
    ])


def lignes_table(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    d = df.copy()
    d["echeance_txt"] = d["echeance"].dt.strftime("%d/%m/%Y")
    d["id"] = d["code"]
    cols = sorted(set(c["id"] for c in COL_SENSI + COL_EMET) | {"id"})
    return d[[c for c in cols if c in d.columns]].replace({np.nan: None}).to_dict("records")


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("sw-valo", "date"), Output("sw-courbe", "date"), Input("maj", "data"), Input("sw-valo", "id"),
          State("sw-valo", "date"), State("sw-courbe", "date"))
def dates_defaut(_, _montage, valo, dc):
    if valo and dc:
        raise PreventUpdate
    with db.connexion() as conn:
        d = conn.execute("SELECT MAX(date_courbe) FROM courbe_bkam").fetchone()[0] \
            or conn.execute("SELECT MAX(date) FROM tenors").fetchone()[0]
    if not d:
        raise PreventUpdate
    return valo or d, dc or d


@callback(Output("sw-codes", "children"), Input("sw-valo", "date"), Input("sw-courbe", "date"))
def liste_codes(valo, courbe):
    u, _ = _univers(valo, courbe)
    if u.empty:
        return []
    return [html.Option(value=c, label=f"{c} — {d}") for c, d in zip(u["code"], u["description"])]


@callback(Output("sw-ligne1", "children"), Output("sw-info-courbe", "children"),
          Output("sw-emetteurs", "options"), Output("sw-categories", "options"), Output("sw-emetteur-lib", "children"),
          Input("sw-valo", "date"), Input("sw-courbe", "date"), Input("sw-code1", "value"), Input("sw-q1", "value"))
def ligne1(valo, courbe, code1, q1):
    u, info = _univers(valo, courbe)
    if u.empty:
        return html.Div(info, className="alerte"), info, [], [], None
    info = f"{info} {len(u)} lignes vivantes valorisées ({BT.info_base()})."
    em = [{"label": e, "value": e} for e in sorted(u["emetteur"].dropna().unique())]
    cat = [{"label": c, "value": c} for c in sorted(u["categorie"].dropna().unique())]
    l1 = _ligne(u, code1)
    if code1 and l1 is None:
        f = BT.fiche(code1)
        motif = ("n'est pas dans base_titre.xlsx" if f is None else
                 "n'a pas de caractéristiques de pricing" if not f["valorisable"] else
                 "est échu ou pas encore en jouissance à cette date")
        return html.Div(f"Le code {code1.strip()} {motif}.", className="alerte"), info, em, cat, None
    if l1 is None:
        return html.Div("Saisissez le code de la ligne 1 et sa quantité.", className="info info-legere"), info, em, cat, None
    q = _q1(q1)
    m, dv01 = q * l1["prix"], q * l1["prix"] * l1["sensibilite"] * 1e-4
    carte1 = html.Div(className="carte", children=[
        html.Div(className="carte-tete", children=[etiquette("Ligne 1", l1["code"])]),
        html.Div(className="swap-l1", children=[
            bloc_ligne(l1),
            html.Div(className="swap-expo", children=[
                html.Div(className="kpi", children=[html.Div("Montant", className="kpi-lib"),
                                                    html.Div(mad(m), className="kpi-val"),
                                                    html.Div(f"{qte(q)} titres × {mad(l1['prix'])}", className="kpi-sous")]),
                html.Div(className="kpi", children=[html.Div("Exposition (montant × sensi)", className="kpi-lib"),
                                                    html.Div(mad(m * l1["sensibilite"]), className="kpi-val"),
                                                    html.Div(f"DV01 {mad(dv01)} MAD par pb", className="kpi-sous")]),
            ]),
        ]),
    ])
    return carte1, info, em, cat, f"Émetteur : {l1['emetteur']}"


@callback(Output("sw-detail-montant", "children"),
          Input("sw-valo", "date"), Input("sw-courbe", "date"), Input("sw-code1", "value"), Input("sw-q1", "value"),
          Input("sw-code2", "value"))
def swap_montant(valo, courbe, code1, q1, code2):
    u, _ = _univers(valo, courbe)
    l1, l2 = _ligne(u, code1), _ligne(u, code2)
    if l1 is None:
        return None
    if l2 is None:
        return html.Div(f"Le code {code2.strip()} n'est pas une ligne vivante de la base à cette date." if code2
                        else "Saisissez le code de la ligne 2.", className="info info-legere")
    return detail(l1, _q1(q1), l2, "montant")


@callback(Output("sw-t-sensi", "data"), Output("sw-t-sensi", "selected_rows"), Output("sw-n-sensi", "children"),
          Input("sw-valo", "date"), Input("sw-courbe", "date"), Input("sw-code1", "value"), Input("sw-q1", "value"),
          Input("sw-tol", "value"), Input("sw-emetteurs", "value"), Input("sw-types", "value"),
          Input("sw-categories", "value"), Input("sw-smin", "value"), Input("sw-smax", "value"))
def table_sensi(valo, courbe, code1, q1, tol, emetteurs, types, categories, smin, smax):
    u, _ = _univers(valo, courbe)
    l1 = _ligne(u, code1)
    if l1 is None:
        return [], [], None
    c = u.drop(index=l1["code"])
    if tol not in (None, ""):
        c = c[(c["sensibilite"] - l1["sensibilite"]).abs() <= float(tol)]
    if emetteurs:
        c = c[c["emetteur"].isin(emetteurs)]
    c = c[c["type"].isin(types or [])]
    if categories:
        c = c[c["categorie"].isin(categories)]
    if smin not in (None, ""):
        c = c[c["spread"] >= float(smin)]
    if smax not in (None, ""):
        c = c[c["spread"] <= float(smax)]
    c = SW.ajouter_quantites(c, l1, _q1(q1))
    c = c.reindex(c["ecart_sensi"].abs().sort_values().index)
    return lignes_table(c), [], f"{len(c)} lignes (sensibilité de la ligne 1 : {l1['sensibilite']:.3f})."


@callback(Output("sw-t-emet", "data"), Output("sw-t-emet", "selected_rows"), Output("sw-n-emet", "children"),
          Input("sw-valo", "date"), Input("sw-courbe", "date"), Input("sw-code1", "value"), Input("sw-q1", "value"),
          Input("sw-ech-du", "date"), Input("sw-ech-au", "date"))
def table_emetteur(valo, courbe, code1, q1, du, au):
    u, _ = _univers(valo, courbe)
    l1 = _ligne(u, code1)
    if l1 is None:
        return [], [], None
    c = u[(u["emetteur"] == l1["emetteur"]) & (u["code"] != l1["code"])]
    if du:
        c = c[c["echeance"] >= pd.Timestamp(du)]
    if au:
        c = c[c["echeance"] <= pd.Timestamp(au)]
    c = SW.ajouter_quantites(c, l1, _q1(q1)).sort_values("echeance")
    periode = (f" échéance du {fdate(du) if du else '…'} au {fdate(au) if au else '…'}") if (du or au) else ""
    return lignes_table(c), [], f"{len(c)} lignes de {l1['emetteur']}{periode}."


def _detail_selection(valo, courbe, code1, q1, ids, mode):
    if not ids:
        return html.Div("Cochez une ligne du tableau pour le détail du swap.", className="info info-legere")
    u, _ = _univers(valo, courbe)
    l1, l2 = _ligne(u, code1), _ligne(u, ids[0])
    if l1 is None or l2 is None:
        return None
    return detail(l1, _q1(q1), l2, mode)


@callback(Output("sw-detail-sensi", "children"), Input("sw-t-sensi", "selected_row_ids"),
          State("sw-valo", "date"), State("sw-courbe", "date"), State("sw-code1", "value"), State("sw-q1", "value"))
def detail_sensi(ids, valo, courbe, code1, q1):
    return _detail_selection(valo, courbe, code1, q1, ids, "sensi")


@callback(Output("sw-detail-emet", "children"), Input("sw-t-emet", "selected_row_ids"),
          State("sw-valo", "date"), State("sw-courbe", "date"), State("sw-code1", "value"), State("sw-q1", "value"))
def detail_emet(ids, valo, courbe, code1, q1):
    return _detail_selection(valo, courbe, code1, q1, ids, "emetteur")


@callback(Output("sw-dl", "data"), Output({"type": "sig-toast", "id": "swap-toast-0"}, "data"),
          Input("sw-export", "n_clicks"),
          State("sw-valo", "date"), State("sw-courbe", "date"), State("sw-code1", "value"), State("sw-q1", "value"),
          State("sw-code2", "value"), State("sw-t-sensi", "data"), State("sw-t-emet", "data"),
          prevent_initial_call=True)
def exporter(n, valo, courbe, code1, q1, code2, data_sensi, data_emet):
    if not n:
        raise PreventUpdate
    u, _ = _univers(valo, courbe)
    l1 = _ligne(u, code1)
    if l1 is None:
        return no_update, toast("Choisissez d'abord la ligne 1.", "erreur")
    q = _q1(q1)
    entete = pd.DataFrame([("Date de valorisation", fdate(valo)), ("Date de la courbe", fdate(courbe)),
                           ("Ligne 1", f"{l1['code']} — {l1['description']}"), ("Quantité ligne 1", q),
                           ("Prix plein ligne 1", l1["prix"]), ("Sensibilité ligne 1", l1["sensibilite"]),
                           ("Montant ligne 1", q * l1["prix"])], columns=["Paramètre", "Valeur"]).astype({"Valeur": str})
    feuilles = {"Ligne 1": entete}
    l2 = _ligne(u, code2)
    if l2 is not None:
        r = SW.quantites(l1, q, l2)
        feuilles["Swap montant"] = pd.DataFrame([
            {"Méthode": m, "Ligne 2": l2["code"], "Quantité exacte": r[k]["exacte"], "Quantité arrondie": r[k]["arrondie"],
             "Montant ligne 2": r[k]["montant_2"], "Écart de montant": r[k]["ecart_montant"],
             "DV01 ligne 2": r[k]["dv01_2"], "Écart de DV01": r[k]["ecart_dv01"]}
            for m, k in (("Montant", "montant"), ("Sensibilité", "sensi"))])
    colonnes = {"code": "Code", "description": "Description", "emetteur": "Émetteur", "categorie": "Catégorie",
                "type_lib": "Type", "echeance_txt": "Échéance", "maturite": "Maturité (ans)", "spread": "Spread (pb)",
                "taux": "Taux (%)", "prix": "Prix plein", "sensibilite": "Sensibilité", "ecart_sensi": "Écart sensi",
                "q_montant_arr": "Qté swap montant", "q_sensi_arr": "Qté swap sensi", "montant_sensi": "Montant swap sensi"}
    for nom, data in (("Sensi proche", data_sensi), ("Même émetteur", data_emet)):
        if data:
            d = pd.DataFrame(data)
            feuilles[nom] = d[[c for c in colonnes if c in d.columns]].rename(columns=colonnes)
    return dcc.send_bytes(db.exporter_classeur(feuilles), f"swap_{l1['code']}_{(valo or '').replace('-', '')}.xlsx"), \
        toast("Swap exporté en Excel.")
