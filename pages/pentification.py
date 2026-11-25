"""Analyse de la courbe — Pentification : diagnostic, réaction après les conseils de BAM, positionnement."""

from __future__ import annotations

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate

import base_courbe as db
import pentification as P
import theme as T
from composants import carte, cls_signe, fdate, graphe, kpi, mise_en_page, note, segment, table, toast, signaux

dash.register_page(__name__, path="/pentification", name="Pentification", order=5, section="courbe")

TENORS = P.TENORS
COUL_SCEN = [T.NAVY, "#3F7CC0", T.ROUGE, "#2A9D8F", T.VERT, "#7A5BA6", T.GRIS]
COUL_LIGNES = [T.NAVY, "#3F7CC0", T.VERT, T.ROUGE, "#7A5BA6", "#2A9D8F", T.GRIS]
COUL_MECA = {"Aplatissement : court ↑": T.ROUGE, "Aplatissement : long ↓": T.VERT, "Pente stable": T.GRIS_CLAIR,
             "Pentification : long ↑": T.CREMA, "Pentification : court ↓": "#3F7CC0"}
COUL_DEC = {"Hausse": T.ROUGE, "Statu quo": T.NAVY, "Baisse": T.VERT}


def champ(lib, comp, classe=""):
    return html.Div(className=f"champ {classe}", children=[html.Label(lib), comp])


def cls_rend(v) -> str:
    if isinstance(v, str):
        return "stable" if v == "—" else "hausse" if v.strip().startswith("-") else "baisse"
    if v is None or pd.isna(v):
        return "stable"
    return "baisse" if v > 0.00005 else "hausse" if v < -0.00005 else "stable"


def court_scen(c: str) -> str:
    return (c.replace("Historique : ", "Hist. ").replace(" (AR1)", "").replace("Court terme", "Court")
            .replace("Long terme", "Long").replace("Pentes historiques", "Pentes hist.").replace("Nelson-Siegel", "NS")
            .replace("Forward implicite", "Forward").replace("Courbe stable", "Stable"))


def fmt_pct(v, d=2):
    return "—" if v is None or pd.isna(v) else f"{v * 100:.{d}f} %"


def fmt_pb(v, signe=True):
    return "—" if v is None or pd.isna(v) else (f"{v:+.0f} pb" if signe else f"{v:.0f} pb")


def _tbl(entetes: list, lignes: list[list], classes: list[list] | None = None, styles=None, dense=True):
    """Petit tableau libre (cellules déjà formatées)."""
    tete = html.Thead(html.Tr([html.Th(h, className="gauche" if i == 0 else None) for i, h in enumerate(entetes)]))
    corps = []
    for i, l in enumerate(lignes):
        cells = []
        for j, v in enumerate(l):
            cls = "tenor gauche" if j == 0 else (classes[i][j] if classes else "")
            st = styles(i, j) if styles else None
            cells.append(html.Td(v, className=cls, style=st))
        corps.append(html.Tr(cells))
    return html.Table(className="tbl " + ("dense" if dense else ""), children=[tete, html.Tbody(corps)])


def _vide(msg: str, h: int = 300):
    g = mise_en_page(go.Figure(), h)
    g.add_annotation(text=msg, showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper", font=dict(color=T.GRIS))
    g.update_xaxes(visible=False)
    g.update_yaxes(visible=False)
    return g


layout = html.Div(className="page", children=[
    dcc.Download(id="pt-dl"),
    html.Div(className="entete", children=[
        html.Div("Pentification", className="titre-fin"),
        html.Div("Une courbe plus pentue que la moyenne : comment l'exploiter", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),
    html.Div(className="carte barre", children=[
        champ("Date de référence", dcc.DatePickerSingle(
            id="pt-ref", display_format="DD/MM/YYYY", placeholder="JJ/MM/AAAA", first_day_of_week=1,
            month_format="MMMM YYYY", className="date-fr")),
        champ("Pente de référence", segment("pt-pente", list(P.PENTES), "10a − 52s")),
        champ("Courbe pentue si la pente dépasse", segment("pt-seuil", list(P.SEUILS), "Moyenne")),
        champ("Historique", segment("pt-fenetre", list(P.FENETRES), "Tout")),
        html.Button("Exporter", id="pt-export", className="btn btn-secondaire"),
    ]),
    html.Div(className="carte", children=[html.Div(id="pt-kpi", className="kpi-grille kpi-6")]),

    dcc.Tabs(id="pt-onglets", value="pente", className="onglets", children=[
        # ------------------------------------------------------------------ 1
        dcc.Tab(label="Pente actuelle", value="pente", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(className="grille-2", children=[
                        carte("Pente de référence dans l'historique", "pb ; bande = moyenne ± 1 écart-type",
                              graphe("pt-g-histo", 360)),
                        carte("Courbe actuelle", "vs fin d'année précédente et courbe moyenne de l'historique",
                              graphe("pt-g-courbes", 360)),
                    ]),
                    carte("Où se concentre la pentification", "pente de chaque segment de la courbe",
                          html.Div(className="grille-2", children=[
                              graphe("pt-g-conc", 340),
                              graphe("pt-g-ytd", 340),
                          ]),
                          html.Div(id="pt-t-conc", className="tableau-defile")),
                    html.Div(className="grille-2", children=[
                        carte("Toutes les pentes", "position actuelle dans l'historique, en pb",
                              html.Div(id="pt-t-pentes", className="tableau-defile")),
                        carte("Facteurs Nelson-Siegel", "niveau, pente, courbure (λ estimé sur l'historique)",
                              html.Div(id="pt-t-ns", className="tableau-defile"),
                              html.Div(id="pt-ns-note", className="note-bloc resume")),
                    ]),
                    carte("Analogues historiques", "formes de courbe les plus proches et ce qui a suivi",
                          html.Div(id="pt-t-analogues", className="tableau-defile"),
                          html.Div(className="grille-2", children=[
                              graphe("pt-g-an-formes", 380),
                              graphe("pt-g-an-chemins", 380),
                          ])),
                ])),
        # ------------------------------------------------------------------ 2
        dcc.Tab(label="Réaction après conseil", value="conseil", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(className="carte barre", children=[
                        champ("Horizon après le conseil", segment("pt-ev-h", list(P.HORIZONS), "3 mois")),
                        champ("Décision", segment("pt-ev-dec", P.DECISIONS, "Statu quo")),
                        html.Div(id="pt-ev-info", className="info info-legere"),
                    ]),
                    carte("Réaction moyenne de la courbe", "variations depuis la veille du conseil, en pb ; "
                          "parts des mécanismes en % des cas", html.Div(id="pt-t-ev", className="tableau-defile")),
                    html.Div(className="grille-2", children=[
                        carte("Trajectoire moyenne après le conseil", None, graphe("pt-g-ev-chemin", 400)),
                        carte("Court contre long, conseil par conseil", "courbes pentues la veille ; sous la "
                              "diagonale : aplatissement", graphe("pt-g-ev-nuage", 360)),
                    ]),
                    carte("Conseils de politique monétaire", "variations à l'horizon choisi, en pb",
                          html.Div(id="pt-t-ev-liste", className="tableau-defile tableau-haut")),
                ])),
        # ------------------------------------------------------------------ 3
        dcc.Tab(label="Positionnement", value="position", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(className="carte barre", children=[
                        champ("Lignes simulées (codes)", dcc.Input(
                            id="pt-codes", type="text", value=", ".join(P.LIGNES_DEFAUT), debounce=True,
                            autoComplete="off", className="input-date input-champ input-code"), "champ-large"),
                        champ("Horizon", segment("pt-pos-h", list(P.HORIZONS), "6 mois")),
                        champ("Choc (pb)", dcc.Input(id="pt-choc", type="number", value=25, min=0, step=5,
                                                    debounce=True, className="input-date input-nombre")),
                        champ("Scénario historique", segment("pt-pos-dec", ["Statu quo", "Hausse"], "Statu quo")),
                        html.Button("Simuler", id="pt-simuler", className="btn"),
                    ]),
                    html.Div(id="pt-pos-info", className="info info-legere"),
                    html.Div(className="grille-2", children=[
                        carte("Courbes à l'horizon par scénario", "%", graphe("pt-g-scen", 430)),
                        carte("Déformation de chaque scénario", "écart à la courbe actuelle, en pb",
                              html.Div(id="pt-t-scen", className="tableau-defile")),
                    ]),
                    carte("Lignes simulées", "valorisation à J+1 sur la courbe des ténors ; portage et glissement "
                          "à courbe stable sur l'horizon", html.Div(id="pt-t-lignes", className="tableau-defile")),
                    carte("Rendement à l'horizon par scénario", "(valeur à l'horizon + coupons encaissés) / valeur "
                          "initiale − 1, non annualisé", html.Div(id="pt-t-rend", className="tableau-defile"),
                          graphe("pt-g-rend", 340)),
                    html.Div(className="grille-2", children=[
                        carte("Décomposition du rendement", "portage (TRI), glissement sur la courbe, effet scénario",
                              graphe("pt-g-decomp", 340), outils=dcc.Dropdown(
                                  id="pt-scen-choix", options=[], value=None, clearable=False, searchable=False,
                                  className="liste pt-dropdown")),
                        carte("Portage + glissement selon l'horizon", "courbe stable, rendement annualisé",
                              graphe("pt-g-roll", 340)),
                    ]),
                ])),
        # ------------------------------------------------------------------ 4
        dcc.Tab(label="Synthèse", value="synthese", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(id="pt-synthese", className="grille-3 pt-synthese"),
                    carte("Méthode", None, note(
                        "Pente de référence : écart entre deux ténors de la courbe BAM, en pb. Historique : toutes "
                        "les dates de la base jusqu'à la date de référence (ou les 5 / 10 dernières années). Une "
                        "courbe est « pentue » quand la pente de référence dépasse le seuil choisi (moyenne ou "
                        "centile de l'historique).",
                        "Concentration : la courbe est découpée en segments entre ténors (13s → 52s, …, 20a → 30a). "
                        "Pour chaque segment, écart à sa moyenne historique, centile, pente par année de maturité, et "
                        "part de l'excès de la pente de référence qu'il porte. Analogues : dates (au moins 180 jours "
                        "avant la date de référence, espacées de 120 jours) dont les pentes de segment, standardisées, "
                        "sont les plus proches de la courbe actuelle.",
                        "Réaction après conseil : pour chaque conseil de la table des taux directeurs, courbe de la "
                        "veille comparée à la courbe 1, 3, 6 et 12 mois plus tard. Mécanisme : pente stable si "
                        "|Δ pente| < 5 pb ; sinon aplatissement ou pentification, « haussier » (court ↑ / long ↑) "
                        "si le niveau moyen des deux jambes monte, « baissier » (long ↓ / court ↓) sinon. Le seuil de "
                        "courbe pentue est celui de tout l'historique : il utilise de l'information postérieure aux "
                        "premiers conseils.",
                        "Positionnement : chaque ligne est valorisée à J+1 sur la courbe des ténors de J, puis à "
                        "l'horizon sur la courbe du scénario (même pricer que la fiche valo). Rendement = (valeur à "
                        "l'horizon + coupons encaissés) / valeur initiale − 1. Portage = (1 + TRI)^(jours/365) − 1 ; "
                        "glissement = rendement à courbe stable − portage (la ligne vieillit et son taux descend la "
                        "courbe) ; effet scénario = rendement du scénario − rendement à courbe stable.",
                        "Scénarios : court terme (choc sur 13s–52s, 60 % sur 2a, 20 % sur 5a) ; long terme (choc sur "
                        "10a–30a, 40 % sur 5a) ; historique (variation moyenne par ténor après les conseils de la "
                        "décision choisie, courbe pentue la veille — toutes courbes s'il y a moins de 3 cas) ; pentes "
                        "historiques (écarts entre ténors adjacents en AR(1) sur l'historique choisi, 13s fixe) ; "
                        "Nelson-Siegel (facteurs en AR(1), variation prévue ajoutée à la courbe observée) ; forward "
                        "implicite (courbe attendue par le marché, fenêtre Taux forward).",
                        "Seuil de rentabilité : hausse du taux de la ligne à l'horizon qui annule son rendement (ou le "
                        "ramène au rendement monétaire du 13 semaines), calculée avec la sensibilité et la convexité "
                        "à l'horizon. Ce sont des simulations, pas des prévisions.")),
                ])),
    ]),
])
layout.children.append(signaux(["pentification-toast-0"]))


# ---------------------------------------------------------------------------
# Date de référence
# ---------------------------------------------------------------------------

@callback(Output("pt-ref", "date"), Input("maj", "data"), Input("pt-ref", "id"), State("pt-ref", "date"))
def ref_defaut(_, _montage, ref):
    if ref:
        raise PreventUpdate
    niv, _ = P.PV.donnees(db.version_base())
    return niv.index[-1].strftime("%Y-%m-%d")


def _args(ref, pente, seuil, fenetre):
    return (ref, pente or "10a − 52s", seuil or "Moyenne", fenetre or "Tout", db.version_base())


# ---------------------------------------------------------------------------
# 1. Pente actuelle
# ---------------------------------------------------------------------------

@callback(
    Output("pt-kpi", "children"), Output("pt-g-histo", "figure"), Output("pt-g-courbes", "figure"),
    Output("pt-g-conc", "figure"), Output("pt-g-ytd", "figure"), Output("pt-t-conc", "children"),
    Output("pt-t-pentes", "children"), Output("pt-t-ns", "children"), Output("pt-ns-note", "children"),
    Output("pt-t-analogues", "children"), Output("pt-g-an-formes", "figure"), Output("pt-g-an-chemins", "figure"),
    Input("pt-ref", "date"), Input("pt-pente", "value"), Input("pt-seuil", "value"), Input("pt-fenetre", "value"),
    Input("maj", "data"), Input("ui", "data"),
)
def afficher_pente(ref, pente, seuil, fenetre, _, ui):
    if not ref:
        raise PreventUpdate
    A = P.etat(*_args(ref, pente, seuil, fenetre))
    st, y, mk, conc = A["stats"], A["ytd"], A["marche"], A["conc"]
    dans = conc[conc["Dans la pente"]]
    top = dans["Écart"].idxmax() if len(dans) else conc["Écart"].idxmax()

    kpis = [
        kpi(f"Pente {A['pente']}", f"{A['p0']:.0f} pb", f"moyenne {st['Moyenne']:.0f} pb · {st['Centile'] * 100:.0f}e centile"),
        kpi("Écart à la moyenne", f"{A['p0'] - st['Moyenne']:+.0f} pb", f"z-score {st['Z-score']:+.2f}",
            T.ROUGE if A["p0"] > A["seuil"] else T.VERT),
        kpi("Segment le plus en excès", top, f"{conc.loc[top, 'Écart']:+.0f} pb · {conc.loc[top, 'Centile'] * 100:.0f}e centile"),
        kpi(f"Pente depuis le {y['t_debut']:%d/%m}", f"{y['d_pente']:+.0f} pb", y["mecanisme"]),
        kpi("13 semaines − taux directeur", f"{mk['ecart']:+.0f} pb", f"moyenne {mk['moyenne']:+.0f} pb"),
        kpi("Taux directeur", f"{mk['td']:.2f} %", "prochain conseil : décembre"),
    ]

    # Historique de la pente
    s = A["serie"].loc[A["masque"]]
    moy, sd = st["Moyenne"], st["Écart-type"]
    g1 = go.Figure()
    g1.add_scatter(x=[s.index[0], s.index[-1], s.index[-1], s.index[0]], y=[moy - sd, moy - sd, moy + sd, moy + sd],
                   fill="toself", fillcolor="rgba(127,169,179,0.18)", line=dict(width=0), hoverinfo="skip", mode="lines",
                   showlegend=False)
    g1.add_scatter(x=s.index, y=s, mode="lines", name=A["pente"], line=dict(color=T.NAVY, width=1.6),
                   hovertemplate="%{x|%d/%m/%Y} : %{y:.0f} pb<extra></extra>")
    g1.add_hline(y=moy, line=dict(color=T.GRIS, width=1, dash="dash"),
                 annotation_text=f"moyenne {moy:.0f}", annotation_position="top left")
    if P.SEUILS[seuil or "Moyenne"] is not None:
        g1.add_hline(y=A["seuil"], line=dict(color=T.CREMA, width=1, dash="dot"),
                     annotation_text=f"seuil {A['seuil']:.0f}", annotation_position="bottom left")
    td = A["td"].loc[s.index]
    g1.add_scatter(x=td.index, y=td, mode="lines", name="Taux directeur (%)", yaxis="y2",
                   line=dict(color=T.ROUGE, width=1.2, shape="hv"), hovertemplate="%{y:.2f} %<extra>TD</extra>")
    g1.add_scatter(x=[A["t0"]], y=[A["p0"]], mode="markers", marker=dict(color=T.CREMA, size=10), showlegend=False,
                   hovertemplate="Actuelle : %{y:.0f} pb<extra></extra>")
    g1 = mise_en_page(g1, 360, yaxis_title="pb", legend=dict(orientation="h", y=1.1, x=0),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False, title="%", tickformat=".2f",
                                  dtick=0.5, tickfont=dict(color=T.ROUGE)))

    # Courbes
    niv_h = P.PV.donnees(db.version_base())[0].loc[A["masque"]]
    g2 = go.Figure()
    g2.add_scatter(x=P.ANS, y=niv_h.mean().to_numpy(), mode="lines+markers", name="Moyenne historique",
                   line=dict(color=T.GRIS_CLAIR, width=2, dash="dot"), text=TENORS,
                   hovertemplate="%{text} : %{y:.3f} %<extra>Moyenne</extra>")
    g2.add_scatter(x=P.ANS, y=y["courbe_debut"].to_numpy(), mode="lines+markers", name=fdate(y["t_debut"]),
                   line=dict(color=T.CIEL, width=2), text=TENORS, hovertemplate="%{text} : %{y:.3f} %<extra></extra>")
    g2.add_scatter(x=P.ANS, y=A["courbe"].to_numpy(), mode="lines+markers", name=fdate(A["t0"]),
                   line=dict(color=T.NAVY, width=3), text=TENORS, hovertemplate="%{text} : %{y:.3f} %<extra></extra>")
    g2 = mise_en_page(g2, 360, xaxis_title="Maturité (années)", yaxis_title="%", legend=dict(orientation="h", y=1.1, x=0))

    # Concentration
    couleurs = [T.NAVY if d else T.CIEL for d in conc["Dans la pente"]]
    g3 = go.Figure()
    g3.add_bar(x=conc.index, y=conc["Moyenne"], name="Moyenne historique", marker_color="rgba(163,157,150,0.45)",
               error_y=dict(type="data", array=conc["Écart-type"], color=T.GRIS_CLAIR, thickness=1),
               hovertemplate="%{x} : %{y:.0f} pb<extra>Moyenne</extra>")
    g3.add_bar(x=conc.index, y=conc["Actuelle"], name="Actuelle", marker_color=couleurs,
               text=[f"{v:+.0f}" for v in conc["Écart"]], textposition="outside",
               hovertemplate="%{x} : %{y:.0f} pb<extra>Actuelle</extra>")
    g3 = mise_en_page(g3, 340, barmode="group", bargap=0.25, yaxis_title="pb",
                      title=dict(text="Pente par segment, en foncé ceux de la pente de référence (étiquette : écart à "
                                 "la moyenne)", font=dict(size=12)),
                      legend=dict(orientation="h", yanchor="top", y=-0.2, x=0), margin=dict(t=50, b=80))
    g3.update_xaxes(type="category")

    d = y["delta"]
    g4 = go.Figure()
    g4.add_bar(x=TENORS, y=d, marker_color=[T.ROUGE if v > 0 else T.VERT for v in d],
               text=[f"{v:+.0f}" for v in d], textposition="outside", hovertemplate="%{x} : %{y:+.1f} pb<extra></extra>")
    g4 = mise_en_page(g4, 340, yaxis_title="pb", showlegend=False,
                      title=dict(text=f"Variation par ténor depuis le {fdate(y['t_debut'])} : {y['mecanisme'].lower()}",
                                 font=dict(size=12)))
    g4.update_xaxes(type="category")
    g4.add_hline(y=0, line_color=T.GRIS_CLAIR, line_width=1)

    spec = [{"col": "Actuelle", "lib": "Actuelle", "genre": "pb1", "cls": "j"},
            {"col": "Moyenne", "lib": "Moyenne", "genre": "pb1", "cls": "ref"},
            {"col": "Écart", "lib": "Écart", "genre": "pb", "cls": cls_signe},
            {"col": "Z-score", "lib": "Z", "genre": "n2", "cls": "ref"},
            {"col": "Centile", "lib": "Centile", "genre": "part", "cls": "ref"},
            {"col": "pb/an actuel", "lib": "Actuelle", "genre": "pb1", "cls": "j"},
            {"col": "pb/an moyen", "lib": "Moyenne", "genre": "pb1", "cls": "ref"},
            {"col": "Part de la courbe", "lib": "Actuelle", "genre": "part", "cls": "j"},
            {"col": "Part moyenne", "lib": "Moyenne", "genre": "part", "cls": "ref"},
            {"col": "Part de l'excès", "lib": f"Excès {A['pente']}", "genre": "part", "cls": "j"}]
    t_conc = table(conc, spec, premiere="Segment", dense="dense",
                   groupes=[("Pente (pb)", 5), ("Pente par année (pb/an)", 2), ("Part de la pente totale", 3)],
                   styles=lambda i, c, v: {"background": "var(--surligne)"} if conc.loc[i, "Dans la pente"] and
                   c in ("Écart", "Part de l'excès") else None)

    tp = A["tab_pentes"]
    spec_p = [{"col": "Actuelle", "lib": "Actuelle", "genre": "pb1", "cls": "j"},
              {"col": "Moyenne", "lib": "Moyenne", "genre": "pb1", "cls": "ref"},
              {"col": "Médiane", "lib": "Médiane", "genre": "pb1", "cls": "ref"},
              {"col": "Écart-type", "lib": "σ", "genre": "pb1", "cls": "ref"},
              {"col": "Z-score", "lib": "Z", "genre": "n2", "cls": cls_signe},
              {"col": "Centile", "lib": "Centile", "genre": "part", "cls": "ref"},
              {"col": "Min", "lib": "Min", "genre": "pb1", "cls": "ref"},
              {"col": "Max", "lib": "Max", "genre": "pb1", "cls": "ref"}]
    t_p = table(tp, spec_p, premiere="Pente", dense="dense",
                styles=lambda i, c, v: {"fontWeight": 700} if i == A["pente"] else None)

    ns = A["ns"]
    t_ns = table(ns, [{"col": "Actuel", "lib": "Actuel", "genre": "n3", "cls": "j"},
                      {"col": "Moyenne", "lib": "Moyenne", "genre": "n3", "cls": "ref"},
                      {"col": "Écart-type", "lib": "σ", "genre": "n3", "cls": "ref"},
                      {"col": "Z-score", "lib": "Z", "genre": "n2", "cls": cls_signe},
                      {"col": "Centile", "lib": "Centile", "genre": "part", "cls": "ref"}], premiere="Facteur", dense="dense")
    b2, b3 = ns.loc["β2 pente"], ns.loc["β3 courbure"]
    ns_note = [html.P(
        f"β2 (≈ court − long, négatif quand la courbe monte) est au {b2['Centile'] * 100:.0f}e centile ; "
        f"β3 (bosse sur le moyen terme) au {b3['Centile'] * 100:.0f}e centile. "
        + ("La pentification actuelle passe surtout par la courbure : le ventre de la courbe (2–15 ans) est "
           "relevé plus que le 30 ans." if b3["Z-score"] > abs(b2["Z-score"]) else
           "La pentification est surtout un mouvement de pente globale entre le court et le long terme."))]

    # Analogues
    an = A["analogues"]
    ta = an["table"]
    if ta.empty:
        t_an, g5, g6 = html.Div("Pas d'analogue sur cet historique.", className="info info-legere"), _vide("—"), _vide("—")
    else:
        spec_a = [{"col": "Distance", "lib": "Distance", "genre": "n2", "cls": "ref"},
                  {"col": "Pente", "lib": "Pente", "genre": "pb1", "cls": "j"},
                  {"col": "Taux directeur", "lib": "TD", "genre": "pct2", "cls": "ref"},
                  {"col": "2a", "lib": "2a", "genre": "n3", "cls": "ref"},
                  {"col": "10a", "lib": "10a", "genre": "n3", "cls": "ref"}]
        for lib in ("3m", "6m", "12m"):
            spec_a += [{"col": f"Δ pente {lib}", "lib": f"Pente {lib}", "genre": "pb", "cls": cls_signe},
                       {"col": f"Δ 52s {lib}", "lib": f"52s {lib}", "genre": "pb", "cls": cls_signe},
                       {"col": f"Δ 10a {lib}", "lib": f"10a {lib}", "genre": "pb", "cls": cls_signe}]
        spec_a.append({"col": "Δ TD 6m", "lib": "TD 6m", "genre": "pb", "cls": cls_signe})
        moy_a = ta.mean(numeric_only=True).rename("Moyenne")
        ta2 = pd.concat([ta, moy_a.to_frame().T])
        t_an = table(ta2, spec_a, premiere="Date", dense="dense",
                     groupes=[("À la date", 5), ("Variation à 3 mois", 3), ("à 6 mois", 3), ("à 12 mois", 3), ("", 1)],
                     styles=lambda i, c, v: {"fontWeight": 700, "background": "var(--surligne)"} if i == "Moyenne" else None)
        g5 = go.Figure()
        for k, (d_, c) in enumerate(an["courbes"].items()):
            g5.add_scatter(x=P.ANS, y=(c - c["13s"]).to_numpy() * 100, mode="lines", name=fdate(d_),
                           line=dict(width=1.2, color=COUL_SCEN[k % len(COUL_SCEN)]), opacity=0.55,
                           hovertemplate="%{y:.0f} pb<extra>" + fdate(d_) + "</extra>")
        g5.add_scatter(x=P.ANS, y=(A["courbe"] - A["courbe"]["13s"]).to_numpy() * 100, mode="lines+markers",
                       name=fdate(A["t0"]), line=dict(width=3.5, color=T.NAVY),
                       hovertemplate="%{y:.0f} pb<extra>Actuelle</extra>")
        g5 = mise_en_page(g5, 380, xaxis_title="Maturité (années)", yaxis_title="écart au 13s (pb)",
                          title=dict(text="Forme de la courbe (écart au 13 semaines)", font=dict(size=12)),
                          legend=dict(orientation="h", yanchor="top", y=-0.32, x=0, font=dict(size=9)), margin=dict(b=140))
        g6 = go.Figure()
        chem = []
        for k, (d_, ch) in enumerate(an["chemins"].items()):
            g6.add_scatter(x=ch.index, y=ch, mode="lines", name=fdate(d_), line=dict(width=1.2, color=COUL_SCEN[k % len(COUL_SCEN)]),
                           opacity=0.55, hovertemplate="J+%{x} : %{y:+.0f} pb<extra>" + fdate(d_) + "</extra>")
            chem.append(ch.reindex(ch.index.union(an["grille"])).ffill().reindex(an["grille"]).where(an["grille"] <= ch.index.max()))
        mc = pd.concat(chem, axis=1).mean(axis=1)
        g6.add_scatter(x=mc.index, y=mc, mode="lines", name="Moyenne", line=dict(width=3.5, color=T.ROUGE),
                       hovertemplate="J+%{x} : %{y:+.0f} pb<extra>Moyenne</extra>")
        g6.add_hline(y=0, line_color=T.GRIS_CLAIR, line_width=1)
        g6 = mise_en_page(g6, 380, xaxis_title="Jours après la date analogue", yaxis_title="Δ pente (pb)",
                          title=dict(text=f"Évolution de la pente {A['pente']} après chaque analogue", font=dict(size=12)),
                          showlegend=False)

    return (kpis, T.theme_fig(g1, ui), T.theme_fig(g2, ui), T.theme_fig(g3, ui), T.theme_fig(g4, ui), t_conc,
            t_p, t_ns, ns_note, t_an, T.theme_fig(g5, ui), T.theme_fig(g6, ui))


# ---------------------------------------------------------------------------
# 2. Réaction après conseil
# ---------------------------------------------------------------------------

@callback(
    Output("pt-ev-info", "children"), Output("pt-t-ev", "children"), Output("pt-g-ev-chemin", "figure"),
    Output("pt-g-ev-nuage", "figure"), Output("pt-t-ev-liste", "children"),
    Input("pt-pente", "value"), Input("pt-seuil", "value"), Input("pt-fenetre", "value"),
    Input("pt-ev-h", "value"), Input("pt-ev-dec", "value"), Input("maj", "data"), Input("ui", "data"),
)
def afficher_conseils(pente, seuil, fenetre, horizon, decision, _, ui):
    _, pente, seuil, fenetre, v = _args(None, pente, seuil, fenetre)
    horizon, decision = horizon or "3 mois", decision or "Statu quo"
    E = P.evenements(pente, seuil, fenetre, v)
    ev = E["ev"]
    info = (f"{len(ev)} conseils depuis le {fdate(ev['Conseil'].min())} · courbe pentue la veille si "
            f"{pente} ≥ {E['seuil']:.0f} pb · court = {P.NOMS_COURTS[E['court']]}, long = {P.NOMS_COURTS[E['long']]}")

    R = P.resume_evenements(E, horizon).set_index(["Décision", "Courbe"])
    R.index = [f"{a} · {b}" for a, b in R.index]
    spec = [{"col": "N", "lib": "Cas", "genre": "int", "cls": "ref"},
            {"col": "Δ court", "lib": "Court", "genre": "pb", "cls": cls_signe},
            {"col": "Δ long", "lib": "Long", "genre": "pb", "cls": cls_signe},
            {"col": "Δ pente", "lib": "Pente", "genre": "pb", "cls": cls_signe},
            {"col": "Δ pente médiane", "lib": "Pente méd.", "genre": "pb", "cls": cls_signe},
            {"col": "% aplatissement", "lib": "Aplatit", "genre": "part", "cls": "j"}]
    spec += [{"col": k, "lib": k.split(" : ")[-1].replace("Pente stable", "Stable"), "genre": "part", "cls": "ref"} for k in P.MECANISMES]
    t_ev = table(R, spec, premiere="Décision · courbe", dense="dense",
                 groupes=[("", 1), (f"Variation moyenne à {horizon} (pb)", 4), ("", 1),
                          ("Aplatissement", 2), ("", 1), ("Pentification", 2)],
                 styles=lambda i, c, val: {"background": "var(--surligne)"} if i.startswith(decision) and "Pentue" in i else None)

    # Trajectoire moyenne
    g1 = go.Figure()
    for pentue, tiret in ((True, "solid"), (False, "dot")):
        ch = P.chemin_moyen(E, decision, pentue)
        if ch.empty:
            continue
        suf = "pentue" if pentue else "normale"
        for col, coul, nom in (("court", T.ROUGE, "Court"), ("long", T.NAVY, "Long"), ("pente", "#7A5BA6", "Pente")):
            g1.add_scatter(x=ch.index, y=ch[col], mode="lines", name=f"{nom} · {suf}",
                           line=dict(color=coul, width=2.6 if pentue else 1.4, dash=tiret),
                           customdata=ch["n"], hovertemplate="J+%{x} : %{y:+.1f} pb (n=%{customdata})<extra>"
                           + f"{nom} · {suf}</extra>")
    g1.add_hline(y=0, line_color=T.GRIS_CLAIR, line_width=1)
    g1.add_vline(x=P.HORIZONS[horizon], line=dict(color=T.GRIS_CLAIR, dash="dash", width=1))
    g1 = mise_en_page(g1, 400, xaxis_title="Jours après la veille du conseil", yaxis_title="pb",
                      title=dict(text=f"{decision} : courbe pentue (trait plein) / normale (pointillé)", font=dict(size=12)),
                      legend=dict(orientation="h", yanchor="top", y=-0.38, x=0, font=dict(size=9)), margin=dict(b=150))
    if not g1.data:
        g1 = _vide(f"Aucun conseil « {decision.lower()} » dans l'historique", 360)

    # Nuage court / long (courbes pentues)
    g2 = go.Figure()
    pent = ev[ev["Pentue"] & ev[f"Δ pente {horizon}"].notna()]
    lim = 10
    for dec in P.DECISIONS:
        g = pent[pent["Décision"] == dec]
        if g.empty:
            continue
        lim = max(lim, g[f"Δ court {horizon}"].abs().max(), g[f"Δ long {horizon}"].abs().max())
        g2.add_scatter(x=g[f"Δ court {horizon}"], y=g[f"Δ long {horizon}"], mode="markers", name=dec,
                       marker=dict(color=COUL_DEC[dec], size=11 if dec != "Statu quo" else 8,
                                   symbol="diamond" if dec == "Hausse" else "circle", line=dict(width=1, color="white")),
                       text=[fdate(d) for d in g["Conseil"]], customdata=g[f"Mécanisme {horizon}"],
                       hovertemplate="%{text}<br>court %{x:+.0f} pb · long %{y:+.0f} pb<br>%{customdata}<extra>" + dec + "</extra>")
    lim = float(lim) * 1.1
    g2.add_scatter(x=[-lim, lim], y=[-lim, lim], mode="lines", line=dict(color=T.GRIS_CLAIR, dash="dash", width=1),
                   hoverinfo="skip", showlegend=False)
    g2.add_hline(y=0, line_color=T.GRIS_CLAIR, line_width=0.6)
    g2.add_vline(x=0, line_color=T.GRIS_CLAIR, line_width=0.6)
    for x_, y_, txt in ((lim * 0.6, -lim * 0.85, "court ↑ / long ↓ : aplatissement"),
                        (-lim * 0.6, lim * 0.85, "pentification")):
        g2.add_annotation(x=x_, y=y_, text=txt, showarrow=False, font=dict(size=10, color=T.GRIS))
    g2 = mise_en_page(g2, 360, xaxis_title=f"Δ court à {horizon} (pb)", yaxis_title=f"Δ long à {horizon} (pb)",
                      legend=dict(orientation="h", y=1.1, x=0))
    g2.update_xaxes(range=[-lim, lim])
    g2.update_yaxes(range=[-lim, lim])

    # Liste
    entetes = ["Conseil", "Décision", "TD", "Pente veille", "Courbe", "Court", "Long", "Pente", "Mécanisme",
               "Conseils suivants"]
    lignes, classes = [], []
    for _, r in ev.sort_values("Conseil", ascending=False).iterrows():
        lignes.append([fdate(r["Conseil"]), r["Décision"] + (f" ({r['Variation TD (pb)']:+.0f})" if r["Décision"] != "Statu quo" else ""),
                       f"{r['Taux directeur']:.2f} %", f"{r['Pente veille']:.0f}", "Pentue" if r["Pentue"] else "—",
                       fmt_pb(r[f"Δ court {horizon}"]), fmt_pb(r[f"Δ long {horizon}"]), fmt_pb(r[f"Δ pente {horizon}"]),
                       r[f"Mécanisme {horizon}"], r[f"Conseils suivants {horizon}"] or "—"])
        classes.append(["", "j" if r["Décision"] != "Statu quo" else "ref", "ref", "ref", "j" if r["Pentue"] else "ref",
                        cls_signe(r[f"Δ court {horizon}"]), cls_signe(r[f"Δ long {horizon}"]),
                        cls_signe(r[f"Δ pente {horizon}"]), "ref", "ref"])
    t_liste = _tbl(entetes, lignes, classes)
    return info, t_ev, T.theme_fig(g1, ui), T.theme_fig(g2, ui), t_liste


# ---------------------------------------------------------------------------
# 3. Positionnement et 4. Synthèse
# ---------------------------------------------------------------------------

def _codes(txt) -> tuple[str, ...]:
    out = []
    for c in str(txt or "").replace(";", ",").replace(" ", ",").split(","):
        c = c.strip()
        if c and c not in out:
            out.append(c)
    return tuple(out) or tuple(P.LIGNES_DEFAUT)


def _pos(ref, pente, seuil, fenetre, horizon, choc, decision, codes):
    ref, pente, seuil, fenetre, v = _args(ref, pente, seuil, fenetre)
    choc = float(choc) if choc not in (None, "") else 25.0
    return P.positionnement(ref, pente, seuil, fenetre, horizon or "6 mois", choc, decision or "Statu quo",
                            _codes(codes), v, P._mtime_titres())


@callback(
    Output("pt-pos-info", "children"), Output("pt-g-scen", "figure"), Output("pt-t-scen", "children"),
    Output("pt-t-lignes", "children"), Output("pt-t-rend", "children"), Output("pt-g-rend", "figure"),
    Output("pt-g-roll", "figure"), Output("pt-scen-choix", "options"), Output("pt-scen-choix", "value"),
    Output("pt-synthese", "children"),
    Input("pt-ref", "date"), Input("pt-pente", "value"), Input("pt-seuil", "value"), Input("pt-fenetre", "value"),
    Input("pt-pos-h", "value"), Input("pt-choc", "value"), Input("pt-pos-dec", "value"),
    Input("pt-simuler", "n_clicks"), Input("pt-codes", "value"), Input("maj", "data"), Input("ui", "data"),
    State("pt-scen-choix", "value"),
)
def afficher_position(ref, pente, seuil, fenetre, horizon, choc, decision, _n, codes, _, ui, choix):
    if not ref:
        raise PreventUpdate
    R = _pos(ref, pente, seuil, fenetre, horizon, choc, decision, codes)
    S, horizon = R["scen"], horizon or "6 mois"
    courbes = S["courbes"]
    noms = list(courbes.columns)
    info = (f"Valorisation au {fdate(R['valo0'])} sur la courbe du {fdate(R['t0'])}, horizon le {fdate(R['valo_h'])} "
            f"({R['h_cal']} jours). Scénario historique : {S['n_hist']} conseils « {S['decision'].lower()} » "
            f"({S['base_hist']}). Rendement monétaire de référence (13s) : {fmt_pct(R['monetaire'])}.")
    if R["erreurs"]:
        info += " Lignes ignorées : " + " ; ".join(R["erreurs"]) + "."

    # Courbes des scénarios
    g1 = go.Figure()
    g1.add_scatter(x=P.ANS, y=S["actuelle"].to_numpy(), mode="lines+markers", name=f"Actuelle ({fdate(R['t0'])})",
                   line=dict(color=T.GRIS_CLAIR, width=4), text=TENORS, hovertemplate="%{text} : %{y:.3f} %<extra>Actuelle</extra>")
    for k, nom in enumerate(noms[1:], start=1):
        g1.add_scatter(x=P.ANS, y=courbes[nom].to_numpy(), mode="lines", name=nom,
                       line=dict(color=COUL_SCEN[k % len(COUL_SCEN)], width=2), text=TENORS,
                       hovertemplate="%{text} : %{y:.3f} %<extra>" + nom + "</extra>")
    if not R["caract"].empty:
        car = R["caract"]
        g1.add_scatter(x=car["Maturité (ans)"], y=car["TRI"], mode="markers+text", name="Lignes (TRI)",
                       text=list(car.index), textposition="top center", marker=dict(color=T.NAVY, size=10, symbol="diamond"),
                       hovertemplate="%{text} : %{y:.3f} %<extra>TRI</extra>")
    g1 = mise_en_page(g1, 430, xaxis_title="Maturité (années)", yaxis_title="%",
                      legend=dict(orientation="h", yanchor="top", y=-0.3, x=0, font=dict(size=9)), margin=dict(b=150))

    dl = S["delta"].copy()
    dl.columns = [court_scen(c) for c in dl.columns]
    t_scen = table(dl.drop(columns=dl.columns[0]), [{"col": c, "lib": c, "genre": "pb", "cls": cls_signe}
                                                     for c in dl.columns[1:]], premiere="Ténor", dense="dense")

    car = R["caract"]
    if car.empty:
        vide = html.Div("Aucune ligne valorisable : vérifier les codes.", className="info info-legere")
        return (info, T.theme_fig(g1, ui), t_scen, vide, vide, _vide("—"), _vide("—"), [], None,
                _synthese(ref, pente, seuil, fenetre, horizon, None))

    spec_l = [{"col": "Échéance", "lib": "Échéance", "genre": "txt", "cls": "ref"},
              {"col": "Maturité (ans)", "lib": "Mat. (ans)", "genre": "n2", "cls": "ref"},
              {"col": "Coupon", "lib": "Coupon", "genre": "pct2", "cls": "ref"},
              {"col": "TRI", "lib": "TRI", "genre": "pct", "cls": "j"},
              {"col": "Prix pied de coupon", "lib": "Prix PdC", "genre": "n3", "cls": "ref"},
              {"col": "Sensibilité", "lib": "Sensi", "genre": "n2", "cls": "ref"},
              {"col": "Convexité", "lib": "Convexité", "genre": "n2", "cls": "ref"},
              {"col": "Taux à l'horizon (stable)", "lib": "Taux horizon", "genre": "pct", "cls": "ref"},
              {"col": "Glissement du taux (pb)", "lib": "Glissement taux", "genre": "pb", "cls": cls_signe},
              {"col": "Portage", "lib": "Portage", "genre": "part2", "cls": "j"},
              {"col": "Glissement", "lib": "Glissement", "genre": "part2", "cls": "j"},
              {"col": "Portage + glissement", "lib": "Total", "genre": "part2", "cls": "j"},
              {"col": "Annualisé", "lib": "Annualisé", "genre": "part2", "cls": "j"},
              {"col": "Excès vs monétaire", "lib": "vs 13s", "genre": "part2", "cls": cls_rend},
              {"col": "Seuil de rentabilité (pb)", "lib": "Seuil 0", "genre": "pb1", "cls": "ref"},
              {"col": "Seuil vs monétaire (pb)", "lib": "Seuil 13s", "genre": "pb1", "cls": "ref"}]
    c2 = car.copy()
    c2["Échéance"] = [fdate(d) for d in c2["Échéance"]]
    for c in ("Portage", "Glissement", "Portage + glissement", "Annualisé", "Excès vs monétaire"):
        c2[c] = [fmt_pct(x) for x in c2[c]]
    for s_ in spec_l:
        if s_["genre"] == "part2":
            s_["genre"] = "txt"
    t_lignes = table(c2, spec_l, premiere="Ligne", dense="dense",
                     groupes=[("Caractéristiques à J+1", 7), (f"Courbe stable à {horizon}", 7), ("Coussin (pb)", 2)])

    mat = R["rend"]
    spec_r = [{"col": c, "lib": court_scen(c), "genre": "txt",
               "cls": "j" if c in ("Moyenne", "Pire") else ""} for c in mat.columns]
    m2 = mat.copy()
    for c in m2.columns:
        m2[c] = [fmt_pct(x) if c != "Écart-type" else fmt_pct(x) for x in m2[c]]
    best = {c: mat[c].idxmax() for c in mat.columns if c != "Écart-type"}
    rmin, rmax = float(mat[noms].min().min()), float(mat[noms].max().max())

    def style_r(i, c, _v):
        x = mat.loc[i, c]
        st = {}
        if c in noms and pd.notna(x):
            a = min(abs(x) / max(abs(rmin), abs(rmax), 1e-9), 1) * 0.32
            st["background"] = f"rgba(30,122,79,{a:.3f})" if x > 0 else f"rgba(193,39,45,{a:.3f})"
        if best.get(c) == i:
            st["fontWeight"] = 700
        return st or None
    t_rend = table(m2, spec_r, premiere="Ligne", dense="dense",
                   groupes=[(f"Scénarios à {horizon}", len(noms)), ("Synthèse", 3)], styles=style_r)

    g2 = go.Figure()
    for k, nom in enumerate(noms):
        g2.add_bar(x=list(mat.index), y=mat[nom] * 100, name=nom, marker_color=COUL_SCEN[k % len(COUL_SCEN)],
                   hovertemplate="%{x} : %{y:.2f} %<extra>" + nom + "</extra>")
    g2.add_hline(y=R["monetaire"] * 100, line=dict(color=T.GRIS, dash="dash", width=1),
                 annotation_text="13 semaines", annotation_position="top right")
    g2.add_hline(y=0, line_color=T.GRIS_CLAIR, line_width=1)
    g2 = mise_en_page(g2, 340, barmode="group", bargap=0.2, yaxis_title="% sur l'horizon",
                      legend=dict(orientation="h", y=-0.15, x=0, font=dict(size=9)))
    g2.update_xaxes(type="category")

    # Portage + glissement par horizon
    g4 = go.Figure()
    for k, (code, d) in enumerate(R["roll"].items()):
        hs = list(d)
        g4.add_scatter(x=hs, y=[d[h]["Annualisé"] * 100 for h in hs], mode="lines+markers", name=code,
                       line=dict(color=COUL_LIGNES[k % len(COUL_LIGNES)], width=2.4),
                       customdata=[[d[h]["Portage"] * 100, d[h]["Glissement"] * 100] for h in hs],
                       hovertemplate="%{x} : %{y:.2f} % annualisé<br>portage %{customdata[0]:.2f} % · glissement "
                       "%{customdata[1]:.2f} % (sur la période)<extra>" + code + "</extra>")
    g4.add_hline(y=float(S["actuelle"]["13s"]), line=dict(color=T.GRIS, dash="dash", width=1),
                 annotation_text="13 semaines", annotation_position="bottom right")
    g4 = mise_en_page(g4, 340, yaxis_title="% annualisé", legend=dict(orientation="h", y=1.1, x=0))
    g4.update_xaxes(type="category")

    opts = [{"label": n, "value": n} for n in noms]
    choix = choix if choix in noms else noms[0]
    return (info, T.theme_fig(g1, ui), t_scen, t_lignes, t_rend, T.theme_fig(g2, ui), T.theme_fig(g4, ui),
            opts, choix, _synthese(ref, pente, seuil, fenetre, horizon, R))


@callback(Output("pt-g-decomp", "figure"),
          Input("pt-scen-choix", "value"), Input("pt-ref", "date"), Input("pt-pente", "value"),
          Input("pt-seuil", "value"), Input("pt-fenetre", "value"), Input("pt-pos-h", "value"),
          Input("pt-choc", "value"), Input("pt-pos-dec", "value"), Input("pt-codes", "value"), Input("ui", "data"))
def afficher_decomp(scen, ref, pente, seuil, fenetre, horizon, choc, decision, codes, ui):
    if not ref or not scen:
        raise PreventUpdate
    R = _pos(ref, pente, seuil, fenetre, horizon, choc, decision, codes)
    d = R["decomp"]
    if d.empty or scen not in set(d["Scénario"]):
        return T.theme_fig(_vide("—", 340), ui)
    d = d[d["Scénario"] == scen].set_index("Code")
    g = go.Figure()
    for col, coul in (("Portage", T.NAVY), ("Glissement", T.CIEL), ("Effet scénario", "#7A5BA6")):
        g.add_bar(x=list(d.index), y=d[col] * 100, name=col, marker_color=coul,
                  hovertemplate="%{x} : %{y:+.2f} %<extra>" + col + "</extra>")
    g.add_scatter(x=list(d.index), y=d["Total"] * 100, mode="markers+text", name="Total",
                  text=[f"{v * 100:.2f} %" for v in d["Total"]], textposition="top center",
                  marker=dict(color=T.ENCRE, size=10, symbol="line-ew-open", line=dict(width=3)),
                  hovertemplate="%{x} : %{y:.2f} %<extra>Total</extra>")
    g.add_hline(y=0, line_color=T.GRIS_CLAIR, line_width=1)
    g = mise_en_page(g, 340, barmode="relative", bargap=0.35, yaxis_title="% sur l'horizon",
                     legend=dict(orientation="h", y=1.1, x=0))
    g.update_xaxes(type="category")
    return T.theme_fig(g, ui)


def _synthese(ref, pente, seuil, fenetre, horizon, R):
    a = _args(ref, pente, seuil, fenetre)
    A = P.etat(*a)
    E = P.evenements(a[1], a[2], a[3], a[4])
    blocs = P.synthese(A, E, R, horizon)
    question = html.Div(className="carte pt-question", children=[
        html.Div("La courbe de 2026 présente une pentification au-dessus de la moyenne : comment l'exploiter ?",
                 className="pt-question-titre"),
    ])
    pistes = blocs.pop("Comment l'exploiter", [])
    cartes = [carte(titre, None, html.Ul([html.Li(t) for t in lignes], className="pt-liste"))
              for titre, lignes in blocs.items()]
    bloc_pistes = carte("Comment l'exploiter", "lecture opérationnelle des trois analyses",
                        html.Ul([html.Li(t) for t in pistes], className="pt-liste pt-pistes"), classe="pt-large")
    return [question, bloc_pistes] + cartes


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@callback(Output("pt-dl", "data"), Output({"type": "sig-toast", "id": "pentification-toast-0"}, "data"),
          Input("pt-export", "n_clicks"), State("pt-ref", "date"), State("pt-pente", "value"),
          State("pt-seuil", "value"), State("pt-fenetre", "value"), State("pt-ev-h", "value"),
          State("pt-pos-h", "value"), State("pt-choc", "value"), State("pt-pos-dec", "value"),
          State("pt-codes", "value"), prevent_initial_call=True)
def exporter(n, ref, pente, seuil, fenetre, h_ev, h_pos, choc, decision, codes):
    if not n or not ref:
        raise PreventUpdate
    a = _args(ref, pente, seuil, fenetre)
    A = P.etat(*a)
    E = P.evenements(a[1], a[2], a[3], a[4])
    R = _pos(ref, pente, seuil, fenetre, h_pos, choc, decision, codes)
    ev = E["ev"].copy()
    for c in ("Conseil", "Veille"):
        ev[c] = ev[c].dt.date
    an = A["analogues"]["table"].reset_index()
    if not an.empty:
        an["Date"] = an["Date"].dt.date
    car = R["caract"].reset_index()
    if not car.empty:
        car["Échéance"] = car["Échéance"].dt.date
    syn = [{"Thème": k, "Conclusion": t} for k, l in P.synthese(A, E, R, h_pos or "6 mois").items() for t in l]
    feuilles = {
        "Synthèse": pd.DataFrame(syn),
        "Pentes": A["tab_pentes"].reset_index(names="Pente"),
        "Concentration": A["conc"].reset_index(names="Segment"),
        "Nelson-Siegel": A["ns"].reset_index(names="Facteur"),
        "Analogues": an,
        f"Conseils {h_ev or '3 mois'}": P.resume_evenements(E, h_ev or "3 mois"),
        "Liste des conseils": ev,
        "Scénarios (pb)": R["scen"]["delta"].reset_index(names="Ténor"),
        "Lignes": car,
        "Rendements": R["rend"].reset_index(names="Ligne"),
        "Décomposition": R["decomp"],
    }
    nom = f"pentification_{A['t0']:%Y%m%d}.xlsx"
    return dcc.send_bytes(db.exporter_classeur(feuilles), nom), toast("Analyse de la pentification exportée en Excel.")
