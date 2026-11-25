"""Fenêtre 2 — Étude statistique de l'historique de la courbe des taux."""

from __future__ import annotations

import time

from datetime import date

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html, no_update
from dash.exceptions import PreventUpdate

import base_courbe as db
import stats_courbe as sc
import theme as T
from composants import (carte, cls_p, cls_signe, etiquette, fdate, fmt, graphe, kpi,
                        mise_en_page, note, segment, table, toast, signaux)

dash.register_page(__name__, path="/statistiques", name="Étude statistique", order=1, section="courbe")

TENORS = db.TENOR_COLS
COULEURS_FACTEURS = {"Niveau": T.NAVY, "Pente": T.VERT, "Convexité": T.ROUGE}
PALETTE_TENORS = ["#0B4A8B", "#3F7CC0", "#8FAED0", "#0E7A3E", "#5DBB85", "#7DD3A0",
                  "#E2001A", "#F07A86", "#5E6A7A"]
PERIODES = {"1A": 1, "3A": 3, "5A": 5, "10A": 10, "Tout": None}


# ---------------------------------------------------------------------------
# Mise en page
# ---------------------------------------------------------------------------

layout = html.Div(className="page", children=[
    dcc.Download(id="st-dl"),
    html.Div(className="entete", children=[
        html.Div("Étude statistique", className="titre-fin"),
        html.Div("Historique de la courbe des BDT", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),

    html.Div(className="carte barre", children=[
        html.Div(className="champ", children=[
            html.Label("Période"),
            html.Div(className="periode", children=[
                dcc.Input(id="st-debut", type="date", className="input-date"),
                html.Span("→", className="fleche"),
                dcc.Input(id="st-fin", type="date", className="input-date"),
            ]),
        ]),
        html.Div(className="champ", children=[
            html.Label("Raccourci"),
            segment("st-rapide", list(PERIODES), "Tout"),
        ]),
        html.Div(className="sep-v"),
        html.Div(className="champ", children=[
            html.Label("Seuil cas aberrants"),
            segment("st-seuil", [("4σ", 4.0), ("5σ", 5.0), ("6σ", 6.0), ("8σ", 8.0)], 5.0),
        ]),
        dcc.Checklist(id="st-exclure", className="interrupteur clair", value=[],
                      options=[{"label": "Exclure les cas aberrants des statistiques", "value": "oui"}]),
        html.Div(className="sep-v"),
        html.Div(id="st-puces", className="puces"),
        html.Button("Exporter l'étude", id="st-export", className="btn btn-secondaire"),
    ]),

    dcc.Loading(type="dot", color=T.NAVY, delay_show=400, children=html.Div(id="st-alerte")),

    dcc.Tabs(id="st-onglets", value="stats", className="onglets", children=[
        dcc.Tab(label="Statistiques", value="stats", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    carte("Statistiques descriptives", "niveaux en %, variations quotidiennes en pb",
                          html.Div(id="st-table-stats", className="tableau-defile")),
                    html.Div(className="grille-2", children=[
                        carte("Volatilité annualisée", "par ténor, en pb", graphe("st-g-vol", 340)),
                        carte("Corrélation des variations", "quotidiennes", graphe("st-g-corr", 340)),
                    ]),
                ])),
        dcc.Tab(label="Graphiques", value="graphes", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    carte("Historique des taux", "ténors choisis et taux directeur",
                          graphe("st-g-histo", 420),
                          outils=html.Div(className="outils", children=[
                              dcc.Checklist(id="st-tenors", value=["13s", "2ans", "10ans", "30ans"],
                                            inline=True, className="pastilles",
                                            options=[{"label": t, "value": t} for t in TENORS]),
                              dcc.Checklist(id="st-td", value=["oui"], className="interrupteur clair",
                                            options=[{"label": "Taux directeur", "value": "oui"}]),
                          ])),
                    html.Div(className="grille-2", children=[
                        carte("Enveloppe de la courbe", "min, max, moyenne, début et fin de période",
                              graphe("st-g-enveloppe", 360)),
                        carte("Pente et convexité", "10 ans − 2 ans ; 2×5 ans − 2 ans − 10 ans, en pb",
                              graphe("st-g-pente", 360)),
                    ]),
                    carte("Distribution des variations quotidiennes", "comparée à une loi normale",
                          graphe("st-g-distrib", 360),
                          outils=segment("st-distrib-tenor", TENORS, "10ans")),
                ])),
        dcc.Tab(label="Pentification", value="pentes", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(id="st-pente-kpi", className="grille-2"),
                    carte("Pente actuelle et historique", "dernière date de la période comparée à la période, en pb",
                          html.Div(id="st-table-pentes", className="tableau-defile")),
                    html.Div(className="grille-2", children=[
                        carte("Évolution des pentes", "moyenne de la période en pointillés", graphe("st-g-pentes", 380)),
                        carte("Position actuelle", "distribution des pentes sur la période", graphe("st-g-pentes-distrib", 380)),
                    ]),
                    html.Div(className="grille-2", children=[
                        carte("Moyennes par fenêtre", "écart de la pente actuelle sous chaque moyenne",
                              html.Div(id="st-table-pentes-fen", className="tableau-defile")),
                        carte("Pentes par année", None, html.Div(id="st-table-pentes-an", className="tableau-defile")),
                    ]),
                    note("Pente = taux 10 ans − taux 2 ans (ou 5 ans), en points de base, à partir des taux BKAM des ténors. "
                         "z-score = (pente actuelle − moyenne) / écart-type de la période. Percentile = part des jours de la "
                         "période où la pente était inférieure ou égale à aujourd'hui. Une pente supérieure à sa moyenne "
                         "signifie une courbe plus pentue que d'habitude ; une pente négative, une courbe inversée."),
                ])),
        dcc.Tab(label="Marche aléatoire", value="rw", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    carte("Les ténors suivent-ils une marche aléatoire ?", "tests au seuil de 5 %",
                          html.Div(id="st-table-rw", className="tableau-defile"),
                          note(
                              "ADF teste la racine unitaire du niveau : p ≥ 5 % est compatible avec une marche "
                              "aléatoire, p < 5 % indique un niveau qui revient vers sa moyenne. KPSS teste "
                              "l'hypothèse inverse (stationnarité) et sert de contrôle.",
                              "Le ratio de variance (Lo-MacKinlay, robuste) compare la variance des variations "
                              "sur 5 et 10 jours à 5 et 10 fois la variance quotidienne. VR = 1 pour une marche "
                              "aléatoire ; VR < 1 : les mouvements se corrigent ; VR > 1 : ils se prolongent.",
                              "Ljung-Box est donné à titre indicatif : sur des milliers d'observations, il rejette "
                              "au moindre écart. Les valeurs significatives sont en gras.",
                          )),
                ])),
        dcc.Tab(label="ACP", value="acp", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(id="st-acp-kpi", className="grille-3"),
                    html.Div(className="grille-2", children=[
                        carte("Chargements des facteurs", "forme de chaque facteur par ténor",
                              graphe("st-g-chargements", 360)),
                        carte("Facteurs cumulés", "évolution en pb",
                              graphe("st-g-scores", 360)),
                    ]),
                    carte("Risque par ténor", "mouvement en pb pour un choc d'un écart-type quotidien",
                          html.Div(id="st-table-chocs", className="tableau-defile"),
                          note("ACP sur la covariance des variations quotidiennes (pb). Signes normalisés : "
                               "Niveau > 0 = hausse parallèle ; Pente > 0 = pentification (long − court) ; "
                               "Convexité > 0 = extrémités en hausse par rapport au 5 ans.")),
                ])),
        dcc.Tab(label="Cas aberrants", value="aberrants", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(id="st-ab-kpi", className="grille-4"),
                    carte("Variations quotidiennes et cas aberrants", "bornes à ± seuil × σ winsorisé",
                          graphe("st-g-aberrants", 380),
                          outils=segment("st-ab-tenor", TENORS, "13s")),
                    carte("Liste des cas aberrants", "classés par score décroissant (200 premiers)",
                          html.Div(id="st-table-ab", className="tableau-defile hauteur-max"),
                          note("Score z = (variation − médiane) / écart-type des variations winsorisées à 1 %–99 %. "
                               "« Retour le lendemain » : la variation suivante efface au moins 70 % du mouvement "
                               "en sens inverse, ce qui suggère une erreur de donnée plutôt qu'un vrai choc.")),
                ])),
        dcc.Tab(label="Résumé annuel", value="annuel", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    carte("Synthèse par année", None, html.Div(id="st-table-synthese", className="tableau-defile")),
                    carte("Par ténor et par année", None,
                          html.Div(id="st-table-annuel", className="tableau-defile"),
                          outils=segment("st-metrique", list(sc.METRIQUES_ANNUELLES), "Variation annuelle (pb)")),
                ])),
    ]),
])
layout.children.append(signaux(['statistiques-toast-0']))


# ---------------------------------------------------------------------------
# Accès aux résultats (cache partagé par tous les callbacks)
# ---------------------------------------------------------------------------

def resultats(debut, fin, seuil, exclure) -> dict:
    return sc.analyse(debut or None, fin or None, float(seuil or 5.0), bool(exclure), db.version_base())


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("st-debut", "value"), Output("st-fin", "value"),
          Input("st-rapide", "value"), Input("maj", "data"))
def periode_rapide(choix, _):
    with db.connexion() as conn:
        s = db.statistiques(conn)
    if not s["nb_dates"]:
        raise PreventUpdate
    fin = s["derniere"]
    ans = PERIODES.get(choix)
    debut = s["premiere"] if ans is None else max(s["premiere"], fin - pd.DateOffset(years=ans))
    return debut.date().isoformat(), fin.date().isoformat()


@callback(
    Output("st-puces", "children"), Output("st-alerte", "children"),
    Output("st-table-stats", "children"), Output("st-g-vol", "figure"), Output("st-g-corr", "figure"),
    Output("st-g-enveloppe", "figure"), Output("st-g-pente", "figure"),
    Output("st-table-rw", "children"),
    Output("st-acp-kpi", "children"), Output("st-g-chargements", "figure"),
    Output("st-g-scores", "figure"), Output("st-table-chocs", "children"),
    Output("st-ab-kpi", "children"), Output("st-table-ab", "children"),
    Output("st-table-synthese", "children"),
    Input("st-debut", "value"), Input("st-fin", "value"), Input("st-seuil", "value"),
    Input("st-exclure", "value"), Input("maj", "data"), Input("ui", "data"),
)
def calculer(debut, fin, seuil, exclure, _, ui):
    if not debut or not fin:
        raise PreventUpdate
    if debut > fin:
        return [], html.Div("La date de début est postérieure à la date de fin.", className="alerte"), \
            *[no_update] * 13
    R = resultats(debut, fin, seuil, exclure)
    if R["vide"]:
        return [], html.Div(f"Seulement {R['n']} observations sur la période : il en faut au moins 30.",
                            className="alerte"), *[no_update] * 13

    niv, k = R["niv"], R["k"]
    puces = [
        html.Div(className="puce", children=[html.Div("Observations", className="puce-lib"),
                                              html.Div(fmt(len(niv), "int"), className="puce-val"),
                                              html.Div(f"{niv.index[0]:%d/%m/%Y} → {niv.index[-1]:%d/%m/%Y}",
                                                       className="puce-sous")]),
        html.Div(className="puce", children=[html.Div("Annualisation", className="puce-lib"),
                                              html.Div(f"{k:.0f} obs/an", className="puce-val"),
                                              html.Div("constaté sur la période", className="puce-sous")]),
    ]

    kpis_acp, fig_c, fig_s, chocs = bloc_acp(R["acp"])
    th = lambda f: T.theme_fig(f, ui)
    return (puces, None,
            table_stats(R["stats"]), th(fig_vol(R["stats"])), th(fig_corr(R["correlations"])),
            th(fig_enveloppe(niv)), th(fig_pente(niv)),
            table_rw(R["tests"]),
            kpis_acp, th(fig_c), th(fig_s), chocs,
            *bloc_aberrants_fixe(R),
            table_synthese(R["synthese"]))


@callback(Output("st-g-histo", "figure"),
          Input("st-debut", "value"), Input("st-fin", "value"), Input("st-tenors", "value"),
          Input("st-td", "value"), Input("maj", "data"), Input("ui", "data"),
          State("st-seuil", "value"), State("st-exclure", "value"))
def historique(debut, fin, tenors, avec_td, _, ui, seuil, exclure):
    if not debut or not fin or debut > fin:
        raise PreventUpdate
    R = resultats(debut, fin, seuil, exclure)
    if R["vide"]:
        raise PreventUpdate
    niv, fig = R["niv"], go.Figure()
    for i, t in enumerate(t for t in TENORS if t in (tenors or [])):
        fig.add_scatter(x=niv.index, y=niv[t], name=t, mode="lines",
                        line=dict(width=1.8, color=PALETTE_TENORS[TENORS.index(t)]),
                        hovertemplate="%{y:.3f}%<extra>" + t + "</extra>")
    if avec_td and R["td"].notna().any():
        fig.add_scatter(x=R["td"].index, y=R["td"], name="Taux directeur", mode="lines",
                        line=dict(width=2, color=T.ROUGE, dash="dash", shape="hv"),
                        hovertemplate="%{y:.2f}%<extra>Taux directeur</extra>")
    return T.theme_fig(mise_en_page(fig, 420, yaxis_ticksuffix="%"), ui)


@callback(Output("st-g-distrib", "figure"),
          Input("st-debut", "value"), Input("st-fin", "value"), Input("st-distrib-tenor", "value"),
          Input("st-seuil", "value"), Input("st-exclure", "value"), Input("maj", "data"),
          Input("ui", "data"))
def distribution(debut, fin, tenor, seuil, exclure, _, ui):
    if not debut or not fin or debut > fin:
        raise PreventUpdate
    R = resultats(debut, fin, seuil, exclure)
    if R["vide"]:
        raise PreventUpdate
    v = R["var_stats"][tenor].dropna()
    bas, haut = v.quantile(0.005), v.quantile(0.995)
    vv = v[(v >= bas) & (v <= haut)]
    fig = go.Figure()
    fig.add_histogram(x=vv, nbinsx=80, histnorm="probability density", name="Observé",
                      marker=dict(color=T.NAVY, opacity=0.75, line=dict(width=0)))
    x = np.linspace(bas, haut, 300)
    fig.add_scatter(x=x, y=np.exp(-0.5 * ((x - v.mean()) / v.std()) ** 2) / (v.std() * np.sqrt(2 * np.pi)),
                    name="Loi normale (même moyenne et écart-type)", mode="lines",
                    line=dict(color=T.ROUGE, width=2))
    nuls = (v.abs() < 1e-9).mean()
    fig.add_annotation(xref="paper", yref="paper", x=1, y=1, showarrow=False, align="right",
                       text=f"skew {v.skew():.2f} · kurtosis {v.kurt():.1f} · {nuls:.0%} de jours inchangés",
                       font=dict(size=11, color=T.GRIS))
    return T.theme_fig(mise_en_page(fig, 360, bargap=0.05, xaxis_title="Variation quotidienne (pb), "
                        "axe limité aux quantiles 0,5 %–99,5 %", xaxis_title_font=dict(size=11, color=T.GRIS)), ui)


@callback(Output("st-g-aberrants", "figure"),
          Input("st-debut", "value"), Input("st-fin", "value"), Input("st-ab-tenor", "value"),
          Input("st-seuil", "value"), Input("st-exclure", "value"), Input("maj", "data"),
          Input("ui", "data"))
def graphe_aberrants(debut, fin, tenor, seuil, exclure, _, ui):
    if not debut or not fin or debut > fin:
        raise PreventUpdate
    R = resultats(debut, fin, seuil, exclure)
    if R["vide"]:
        raise PreventUpdate
    v, m = R["var"][tenor], R["masque"][tenor].fillna(False)
    bas, haut = R["bornes"][0][tenor], R["bornes"][1][tenor]
    fig = go.Figure()
    fig.add_scattergl(x=v.index[~m], y=v[~m], mode="markers", name="Variations",
                      marker=dict(size=3, color="rgba(11,74,139,0.35)"),
                      hovertemplate="%{x|%d/%m/%Y} : %{y:+.1f} pb<extra></extra>")
    fig.add_scattergl(x=v.index[m], y=v[m], mode="markers", name="Cas aberrants",
                      marker=dict(size=7, color=T.ROUGE, line=dict(color="#FFFFFF", width=1)),
                      hovertemplate="%{x|%d/%m/%Y} : %{y:+.1f} pb<extra>Aberrant</extra>")
    for y in (bas, haut):
        if pd.notna(y):
            fig.add_hline(y=y, line=dict(color=T.ROUGE, width=1, dash="dot"), opacity=0.6)
    return T.theme_fig(mise_en_page(fig, 380, yaxis_title="pb",
                                    yaxis_title_font=dict(size=11, color=T.GRIS)), ui)


@callback(Output("st-table-annuel", "children"),
          Input("st-debut", "value"), Input("st-fin", "value"), Input("st-metrique", "value"),
          Input("st-seuil", "value"), Input("st-exclure", "value"), Input("maj", "data"))
def table_annuelle(debut, fin, metrique, seuil, exclure, _):
    if not debut or not fin or debut > fin:
        raise PreventUpdate
    R = resultats(debut, fin, seuil, exclure)
    if R["vide"]:
        raise PreventUpdate
    df = R["par_tenor"][metrique]
    genre = {"Variation annuelle (pb)": "pb", "Volatilité annualisée (pb)": "pb1",
             "Niveau moyen (%)": "pct", "Skewness": "n2", "Kurtosis": "n2"}[metrique]
    amplitude = float(np.nanmax(np.abs(df.to_numpy()))) if df.notna().any().any() else 1.0

    def style(_, __, v):
        if v is None or pd.isna(v) or amplitude == 0:
            return None
        a = min(abs(v) / amplitude, 1) * 0.22
        if metrique == "Variation annuelle (pb)":
            return {"background": f"rgba({'226,0,26' if v > 0 else '14,122,62'},{a:.3f})"}
        if metrique == "Volatilité annualisée (pb)":
            return {"background": f"rgba(11,74,139,{a:.3f})"}
        return None

    spec = [{"col": t, "lib": t, "genre": genre,
             "cls": cls_signe if genre == "pb" else "ref"} for t in TENORS]
    return table(df, spec, premiere="Année", styles=style)


@callback(Output("st-dl", "data"), Output({"type": "sig-toast", "id": "statistiques-toast-0"}, "data"),
          Input("st-export", "n_clicks"),
          State("st-debut", "value"), State("st-fin", "value"),
          State("st-seuil", "value"), State("st-exclure", "value"), prevent_initial_call=True)
def exporter(n, debut, fin, seuil, exclure):
    if not n:
        raise PreventUpdate
    R = resultats(debut, fin, seuil, exclure)
    if R["vide"]:
        return no_update, toast("Pas assez de données sur la période.", "erreur")
    feuilles = {
        "Statistiques": R["stats"].reset_index(),
        "Corrélations": R["correlations"].rename_axis("Ténor").reset_index(),
        "Marche aléatoire": R["tests"].reset_index(),
    }
    if R["acp"]:
        feuilles["ACP facteurs"] = R["acp"]["facteurs"].rename_axis("Facteur").reset_index()
        feuilles["ACP chargements"] = R["acp"]["chargements"].rename_axis("Ténor").reset_index()
        feuilles["ACP chocs 1σ (pb)"] = R["acp"]["chocs"].rename_axis("Ténor").reset_index()
    E = R["pentes"]
    feuilles["Pentes - synthèse"] = E["synthese"].reset_index()
    feuilles["Pentes - fenêtres"] = E["moyennes"].rename_axis("Pente").reset_index()
    an = E["annuel"].copy()
    an.columns = [f"{p} - {m}" for m, p in an.columns]
    feuilles["Pentes - par année"] = an.reset_index()
    feuilles["Pentes - historique"] = E["series"].rename_axis("Date").reset_index()
    feuilles["Cas aberrants"] = R["aberrants"]
    feuilles["Synthèse annuelle"] = R["synthese"]
    for nom, df in R["par_tenor"].items():
        feuilles[f"Annuel - {nom}"] = df.reset_index()
    nom = f"etude_courbe_{debut.replace('-', '')}_{fin.replace('-', '')}.xlsx"
    return dcc.send_bytes(db.exporter_classeur(feuilles), nom), toast("Étude exportée en Excel.")


# ---------------------------------------------------------------------------
# Construction des blocs
# ---------------------------------------------------------------------------

def table_stats(st: pd.DataFrame) -> html.Table:
    spec = [
        {"col": "Niveau moyen (%)", "lib": "Moyen", "genre": "pct", "cls": "j"},
        {"col": "Niveau min (%)", "lib": "Min", "genre": "pct", "cls": "ref"},
        {"col": "Niveau max (%)", "lib": "Max", "genre": "pct", "cls": "ref"},
        {"col": "Dernier (%)", "lib": "Dernier", "genre": "pct", "cls": "j"},
        {"col": "Variation période (pb)", "lib": "Var. période", "genre": "pb", "cls": cls_signe},
        {"col": "Moyenne ann. (pb)", "lib": "Moy. ann.", "genre": "pb", "cls": lambda v: f"debut-groupe {cls_signe(v)}"},
        {"col": "Volatilité ann. (pb)", "lib": "Vol. ann.", "genre": "pb1", "cls": "j"},
        {"col": "Volatilité quot. (pb)", "lib": "Vol. quot.", "genre": "n2", "cls": "ref"},
        {"col": "Skewness", "lib": "Skewness", "genre": "n2", "cls": "ref"},
        {"col": "Kurtosis", "lib": "Kurtosis", "genre": "n2", "cls": "ref"},
        {"col": "Hausse max (pb)", "lib": "Hausse max", "genre": "pb", "cls": "hausse"},
        {"col": "Baisse max (pb)", "lib": "Baisse max", "genre": "pb", "cls": "baisse"},
    ]
    return table(st, spec, groupes=[("Niveaux (%)", 5), ("Variations quotidiennes (pb)", 7)], dense="dense")


def fig_vol(st: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Bar(x=st.index, y=st["Volatilité ann. (pb)"], marker_color=T.NAVY,
                           text=[f"{v:.0f}" for v in st["Volatilité ann. (pb)"]], textposition="outside",
                           textfont=dict(color=T.GRIS, size=11),
                           hovertemplate="%{x} : %{y:.1f} pb<extra></extra>"))
    return mise_en_page(fig, 340, bargap=0.45, showlegend=False)


def fig_corr(c: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Heatmap(
        z=c.to_numpy(), x=c.columns, y=c.index, zmin=-1, zmax=1,
        colorscale=[[0, T.ROUGE], [0.5, "#F4F6FA"], [1, T.NAVY]],
        text=np.round(c.to_numpy(), 2), texttemplate="%{text}", textfont=dict(size=10),
        hovertemplate="%{y} / %{x} : %{z:.2f}<extra></extra>", showscale=False))
    fig = mise_en_page(fig, 340)
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)")
    return fig


def fig_enveloppe(niv: pd.DataFrame) -> go.Figure:
    x = TENORS
    fig = go.Figure()
    fig.add_scatter(x=x, y=niv.max(), mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip")
    fig.add_scatter(x=x, y=niv.min(), mode="lines", line=dict(width=0), fill="tonexty",
                    fillcolor="rgba(143,174,208,0.28)", name="Min – max",
                    customdata=np.c_[niv.min(), niv.max()],
                    hovertemplate="min %{customdata[0]:.3f}% · max %{customdata[1]:.3f}%<extra></extra>")
    fig.add_scatter(x=x, y=niv.mean(), mode="lines", name="Moyenne",
                    line=dict(color=T.GRIS, width=1.6, dash="dot"),
                    hovertemplate="%{y:.3f}%<extra>Moyenne</extra>")
    fig.add_scatter(x=x, y=niv.iloc[0], mode="lines+markers", name=f"Début · {niv.index[0]:%d/%m/%Y}",
                    line=dict(color=T.CIEL, width=2), marker=dict(size=6),
                    hovertemplate="%{y:.3f}%<extra>Début</extra>")
    fig.add_scatter(x=x, y=niv.iloc[-1], mode="lines+markers", name=f"Fin · {niv.index[-1]:%d/%m/%Y}",
                    line=dict(color=T.NAVY, width=2.6), marker=dict(size=7),
                    hovertemplate="%{y:.3f}%<extra>Fin</extra>")
    return mise_en_page(fig, 360, yaxis_ticksuffix="%")


def fig_pente(niv: pd.DataFrame) -> go.Figure:
    pente = (niv["10ans"] - niv["2ans"]) * 100
    convexite = (2 * niv["5ans"] - niv["2ans"] - niv["10ans"]) * 100
    fig = go.Figure()
    fig.add_scatter(x=niv.index, y=pente, name="Pente 10 ans − 2 ans", mode="lines",
                    line=dict(color=T.NAVY, width=1.6), hovertemplate="%{y:.0f} pb<extra>Pente</extra>")
    fig.add_scatter(x=niv.index, y=convexite, name="Convexité 2×5 ans − 2 ans − 10 ans", mode="lines",
                    line=dict(color=T.VERT, width=1.4), hovertemplate="%{y:.0f} pb<extra>Convexité</extra>")
    fig.add_hline(y=0, line=dict(color="rgba(11,74,139,0.25)", width=1))
    return mise_en_page(fig, 360, yaxis_title="pb", yaxis_title_font=dict(size=11, color=T.GRIS))


def table_rw(tests: pd.DataFrame) -> html.Table:
    classes = {"Marche aléatoire": "pill-rw", "Retour à la moyenne": "pill-mr",
               "Variations qui se corrigent": "pill-neg", "Variations persistantes": "pill-pos",
               "Non testable": "pill-nt"}
    tete = html.Tr([html.Th(c, className="gauche" if c in ("Ténor", "Verdict") else None) for c in
                    ["Ténor", "ADF stat", "ADF p", "KPSS p", "Ljung-Box p",
                     "VR(5)", "VR(5) p", "VR(10)", "VR(10) p", "Verdict"]])
    lignes = []
    for t, r in tests.iterrows():
        g = r.get
        lignes.append(html.Tr([
            html.Td(t, className="tenor gauche"),
            html.Td(fmt(g("ADF stat"), "n2"), className="ref"),
            html.Td(fmt(g("ADF p"), "p"), className=cls_p(g("ADF p"))),
            html.Td(fmt(g("KPSS p"), "p"), className=cls_p(g("KPSS p"))),
            html.Td(fmt(g("Ljung-Box p"), "p"), className=cls_p(g("Ljung-Box p"))),
            html.Td(fmt(g("VR(5)"), "n3"), className="j debut-groupe"),
            html.Td(fmt(g("VR(5) p"), "p"), className=cls_p(g("VR(5) p"))),
            html.Td(fmt(g("VR(10)"), "n3"), className="j"),
            html.Td(fmt(g("VR(10) p"), "p"), className=cls_p(g("VR(10) p"))),
            html.Td(html.Span(r["Verdict"], className=f"pill {classes.get(r['Verdict'], '')}"),
                    className="gauche debut-groupe"),
        ]))
    return html.Table(className="tbl", children=[html.Thead(tete), html.Tbody(lignes)])


def bloc_acp(a: dict | None):
    if a is None:
        vide = html.Div("Pas assez d'observations complètes pour l'ACP.", className="note")
        return [vide], go.Figure(), go.Figure(), vide
    f = a["facteurs"]
    kpis = [html.Div(className="carte kpi-carte", children=[
        etiquette(nom, f"facteur {i + 1}"),
        html.Div(className="kpi-ligne", children=[
            kpi("Part var.", fmt(f.at[nom, "Part de variance"], "part"),
                f"cumul {fmt(f.at[nom, 'Part cumulée'], 'part')}", COULEURS_FACTEURS[nom]),
            kpi("Volatilité ann.", f"{f.at[nom, 'Volatilité ann. (pb)']:.0f} pb",
                f"{f.at[nom, 'Volatilité quot. (pb)']:.1f} pb par jour"),
            kpi("VaR 99 % 1j", f"{f.at[nom, 'VaR 99 % 1 jour (pb)']:.1f} pb", "choc du facteur"),
        ]),
    ]) for i, nom in enumerate(sc.FACTEURS)]

    ch = a["chargements"]
    fig_c = go.Figure()
    for nom in sc.FACTEURS:
        fig_c.add_scatter(x=ch.index, y=ch[nom], name=nom, mode="lines+markers",
                          line=dict(color=COULEURS_FACTEURS[nom], width=2.4), marker=dict(size=7),
                          hovertemplate="%{y:.3f}<extra>" + nom + "</extra>")
    fig_c.add_hline(y=0, line=dict(color="rgba(11,74,139,0.25)", width=1))
    fig_c = mise_en_page(fig_c, 360)

    sc_ = a["scores_cumules"]
    fig_s = go.Figure()
    for nom in sc.FACTEURS:
        fig_s.add_scatter(x=sc_.index, y=sc_[nom], name=nom, mode="lines",
                          line=dict(color=COULEURS_FACTEURS[nom], width=1.6),
                          hovertemplate="%{y:.0f} pb<extra>" + nom + "</extra>")
    fig_s = mise_en_page(fig_s, 360, yaxis_title="pb", yaxis_title_font=dict(size=11, color=T.GRIS))

    chocs = a["chocs"].copy()
    chocs["Total (quadratique)"] = np.sqrt((chocs ** 2).sum(axis=1))
    spec = [{"col": c, "lib": c, "genre": "pb1", "cls": "j" if c.startswith("Total") else "ref"}
            for c in chocs.columns]
    return kpis, fig_c, fig_s, table(chocs, spec)


def bloc_aberrants_fixe(R: dict):
    ab, n_obs = R["aberrants"], R["var"].size
    if ab.empty:
        kpis = [kpi("Cas aberrants", "0", "aucun au-delà du seuil")]
        return kpis, html.Div("Aucun cas aberrant au seuil choisi.", className="note")
    par_tenor = ab["Ténor"].value_counts()
    kpis = [
        html.Div(className="carte kpi-carte", children=[kpi("Cas aberrants", fmt(len(ab), "int"),
                                                            f"{len(ab) / n_obs:.2%} des variations")]),
        html.Div(className="carte kpi-carte", children=[kpi("Retour le lendemain",
                                                            fmt(int(ab["Retour le lendemain"].sum()), "int"),
                                                            f"{ab['Retour le lendemain'].mean():.0%} des cas : erreurs probables")]),
        html.Div(className="carte kpi-carte", children=[kpi("Ténor le plus touché", par_tenor.index[0],
                                                            f"{par_tenor.iloc[0]} cas")]),
        html.Div(className="carte kpi-carte", children=[kpi("Plus forte variation",
                                                            f"{ab.iloc[ab['Variation (pb)'].abs().argmax()]['Variation (pb)']:+.0f} pb",
                                                            f"{ab.iloc[ab['Variation (pb)'].abs().argmax()]['Ténor']} · "
                                                            f"{fdate(ab.iloc[ab['Variation (pb)'].abs().argmax()]['Date'])}")]),
    ]
    top = ab.head(200).set_index("Date")
    spec = [
        {"col": "Ténor", "lib": "Ténor", "genre": "txt", "cls": "tenor"},
        {"col": "Variation (pb)", "lib": "Variation (pb)", "genre": "pb", "cls": cls_signe},
        {"col": "Score z", "lib": "Score z", "genre": "n2", "cls": "j"},
        {"col": "Niveau veille (%)", "lib": "Niveau veille", "genre": "pct", "cls": "ref"},
        {"col": "Niveau (%)", "lib": "Niveau", "genre": "pct", "cls": "ref"},
        {"col": "Retour le lendemain", "lib": "Retour le lendemain", "genre": "txt", "cls": "ref"},
    ]
    top = top.assign(**{"Retour le lendemain": top["Retour le lendemain"].map({True: "oui", False: "—"})})
    return kpis, table(top, spec, premiere="Date", compact=True)


def table_synthese(s: pd.DataFrame) -> html.Table:
    df = s.set_index("Année")
    spec = [
        {"col": "Observations", "lib": "Obs.", "genre": "int", "cls": "ref"},
        {"col": "Taux directeur fin (%)", "lib": "Taux dir. fin", "genre": "pct2", "cls": "j"},
        {"col": "Δ taux directeur (pb)", "lib": "Δ taux dir.", "genre": "pb", "cls": cls_signe},
        {"col": "Part niveau", "lib": "Niveau", "genre": "part", "cls": "debut-groupe ref"},
        {"col": "Part pente", "lib": "Pente", "genre": "part", "cls": "ref"},
        {"col": "Part convexité", "lib": "Convexité", "genre": "part", "cls": "ref"},
        {"col": "Cas aberrants", "lib": "Cas aberrants", "genre": "int", "cls": "debut-groupe ref"},
        {"col": "Ténor le plus volatil", "lib": "Ténor le + volatil", "genre": "txt", "cls": "tenor"},
        {"col": "Volatilité max (pb)", "lib": "Sa vol. ann.", "genre": "pb1", "cls": "j"},
    ]
    return table(df, spec, premiere="Année",
                 groupes=[("Marché", 3), ("Part de variance (ACP)", 3), ("Risque", 3)])


# ---------------------------------------------------------------------------
# Pentification
# ---------------------------------------------------------------------------

COULEURS_PENTES = {"10 ans − 2 ans": T.NAVY, "10 ans − 5 ans": T.VERT}


def lecture_pente(z: float, actuelle: float, var3m: float) -> str:
    if actuelle < 0:
        niveau = "courbe inversée sur ce segment"
    elif np.isnan(z):
        niveau = "—"
    elif z >= 1.5:
        niveau = "nettement plus pentue que sa moyenne"
    elif z >= 0.5:
        niveau = "plus pentue que sa moyenne"
    elif z > -0.5:
        niveau = "proche de sa moyenne"
    elif z > -1.5:
        niveau = "plus plate que sa moyenne"
    else:
        niveau = "nettement plus plate que sa moyenne"
    tendance = "" if np.isnan(var3m) else (" · en pentification sur 3 mois" if var3m > 0
                                            else " · en aplatissement sur 3 mois")
    return niveau[0].upper() + niveau[1:] + tendance


@callback(
    Output("st-pente-kpi", "children"), Output("st-table-pentes", "children"),
    Output("st-g-pentes", "figure"), Output("st-g-pentes-distrib", "figure"),
    Output("st-table-pentes-fen", "children"), Output("st-table-pentes-an", "children"),
    Input("st-debut", "value"), Input("st-fin", "value"), Input("maj", "data"), Input("ui", "data"),
    State("st-seuil", "value"), State("st-exclure", "value"),
)
def onglet_pentification(debut, fin, _, ui, seuil, exclure):
    if not debut or not fin or debut > fin:
        raise PreventUpdate
    R = resultats(debut, fin, seuil, exclure)
    if R["vide"]:
        raise PreventUpdate
    E = R["pentes"]
    syn, series = E["synthese"], E["series"]

    kpis = []
    for nom, r in syn.iterrows():
        kpis.append(html.Div(className="carte kpi-carte", children=[
            etiquette(nom, f"au {fdate(E['fin'])}"),
            html.Div(className="kpi-ligne kpi-ligne-4", children=[
                kpi("Actuelle", f"{r['Actuelle (pb)']:.0f} pb", f"moyenne {r['Moyenne (pb)']:.0f} pb"),
                kpi("Écart à la moyenne", fmt(r["Écart à la moyenne (pb)"], "pb") + " pb",
                    f"écart-type {r['Écart-type (pb)']:.0f} pb"),
                kpi("z-score", f"{r['z-score']:+.2f}", "en écarts-types"),
                kpi("Percentile", f"{r['Percentile']:.0%}", "des jours de la période"),
            ]),
            html.Div(lecture_pente(r["z-score"], r["Actuelle (pb)"], r["Var. 3 mois (pb)"]), className="kpi-lecture"),
        ]))

    df = syn.copy()
    df["Min"] = [f"{v:.0f} ({fdate(d)})" for v, d in zip(df["Min (pb)"], df["Date min"])]
    df["Max"] = [f"{v:.0f} ({fdate(d)})" for v, d in zip(df["Max (pb)"], df["Date max"])]
    spec = [
        {"col": "Actuelle (pb)", "lib": "Actuelle", "genre": "pb1", "cls": "j col-j"},
        {"col": "Moyenne (pb)", "lib": "Moyenne", "genre": "pb1", "cls": "j"},
        {"col": "Médiane (pb)", "lib": "Médiane", "genre": "pb1", "cls": "ref"},
        {"col": "Écart-type (pb)", "lib": "Écart-type", "genre": "pb1", "cls": "ref"},
        {"col": "Écart à la moyenne (pb)", "lib": "Écart", "genre": "pb", "cls": cls_signe},
        {"col": "z-score", "lib": "z-score", "genre": "n2", "cls": "j"},
        {"col": "Percentile", "lib": "Percentile", "genre": "part", "cls": "ref"},
        {"col": "Min", "lib": "Min (date)", "genre": "txt", "cls": "debut-groupe ref"},
        {"col": "Max", "lib": "Max (date)", "genre": "txt", "cls": "ref"},
        {"col": "Var. 1 mois (pb)", "lib": "1 mois", "genre": "pb", "cls": lambda v: f"debut-groupe {cls_signe(v)}"},
        {"col": "Var. 3 mois (pb)", "lib": "3 mois", "genre": "pb", "cls": cls_signe},
        {"col": "Var. YTD (pb)", "lib": "YTD", "genre": "pb", "cls": cls_signe},
    ]
    t_syn = table(df, spec, premiere="Pente", dense="dense",
                  groupes=[("Comparaison à la moyenne", 7), ("Extrêmes (pb)", 2), ("Variation (pb)", 3)])

    fig = go.Figure()
    for nom in series.columns:
        c = COULEURS_PENTES[nom]
        fig.add_scatter(x=series.index, y=series[nom], name=nom, mode="lines", line=dict(color=c, width=1.6),
                        hovertemplate="%{y:.0f} pb<extra>" + nom + "</extra>")
        fig.add_hline(y=syn.at[nom, "Moyenne (pb)"], line=dict(color=c, width=1.2, dash="dot"))
        fig.add_scatter(x=[series.index[-1]], y=[series[nom].iloc[-1]], mode="markers", showlegend=False,
                        marker=dict(size=9, color=c, line=dict(color="#FFFFFF", width=1.5)),
                        hovertemplate="Aujourd'hui : %{y:.0f} pb<extra>" + nom + "</extra>")
    fig.add_hline(y=0, line=dict(color="rgba(11,74,139,0.25)", width=1))
    fig = mise_en_page(fig, 380, yaxis_title="pb", yaxis_title_font=dict(size=11, color=T.GRIS))

    fig_d = go.Figure()
    for nom in series.columns:
        c = COULEURS_PENTES[nom]
        fig_d.add_histogram(x=series[nom], name=nom, nbinsx=60, marker=dict(color=c, opacity=0.5, line=dict(width=0)),
                            hovertemplate="%{x} pb : %{y} jours<extra>" + nom + "</extra>")
        fig_d.add_vline(x=series[nom].iloc[-1], line=dict(color=c, width=2.4))
        fig_d.add_vline(x=syn.at[nom, "Moyenne (pb)"], line=dict(color=c, width=1.2, dash="dot"))
    fig_d = mise_en_page(fig_d, 380, barmode="overlay", bargap=0.05, xaxis_title="pb · trait plein : aujourd'hui, "
                         "pointillés : moyenne", xaxis_title_font=dict(size=11, color=T.GRIS), hovermode="closest")

    moy = E["moyennes"]
    tete = html.Tr([html.Th("Pente", className="gauche"), html.Th("Actuelle", className="col-j")] +
                   [html.Th(c) for c in moy.columns])
    lignes = []
    for nom in moy.index:
        act = syn.at[nom, "Actuelle (pb)"]
        cells = [html.Td(nom, className="tenor gauche"), html.Td(fmt(act, "pb1"), className="j col-j")]
        for c in moy.columns:
            m = moy.at[nom, c]
            ecart = act - m if pd.notna(m) else np.nan
            cells.append(html.Td(html.Div(className="cellule", children=[
                html.Span(fmt(m, "pb1"), className="ref"),
                html.Span(fmt(ecart, "pb"), className=f"mini {cls_signe(ecart)}")])))
        lignes.append(html.Tr(cells))
    t_fen = html.Table(className="tbl", children=[html.Thead(tete), html.Tbody(lignes)])

    an = E["annuel"]
    an = an.reorder_levels([1, 0], axis=1)[[(p, m) for p in series.columns
                                             for m in ("Moyenne", "Fin d'année", "Min", "Max")]]
    an.columns = [f"{p}|{m}" for p, m in an.columns]
    spec_an = []
    for p in series.columns:
        spec_an += [{"col": f"{p}|Moyenne", "lib": "Moyenne", "genre": "pb1", "cls": "debut-groupe j"},
                    {"col": f"{p}|Fin d'année", "lib": "Fin", "genre": "pb1", "cls": "ref"},
                    {"col": f"{p}|Min", "lib": "Min", "genre": "pb1", "cls": "ref"},
                    {"col": f"{p}|Max", "lib": "Max", "genre": "pb1", "cls": "ref"}]
    t_an = table(an.sort_index(ascending=False), spec_an, premiere="Année", dense="dense",
                 groupes=[(p, 4) for p in series.columns])

    return kpis, t_syn, T.theme_fig(fig, ui), T.theme_fig(fig_d, ui), t_fen, t_an
