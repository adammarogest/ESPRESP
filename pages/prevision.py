"""Fenêtre 3 — Prévision de la courbe (pentes historiques, Nelson-Siegel) et valeur relative."""

from __future__ import annotations

import time

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html, no_update
from dash.exceptions import PreventUpdate

import base_courbe as db
import prevision as P
import stats_courbe as sc
import theme as T
import valeur_relative as V
from composants import (carte, cls_signe, etiquette, fdate, fmt, graphe, kpi, mise_en_page, note,
                        segment, table, toast, signaux)

dash.register_page(__name__, path="/prevision", name="Prévision & valeur relative", order=2, section="courbe")

TENORS = db.TENOR_COLS
COULEURS_FEN = {"1A": "#8FAED0", "3A": "#3F7CC0", "5A": "#0B4A8B", "10A": "#0E7A3E",
                "Tout": "#E2001A", "Perso": "#5E6A7A"}
SCENARIOS = {"Inchangée": None, "Pentes inter-ténors": "inter", "Pente vs taux directeur": "td",
             "Moyenne des variations": "derive", "Nelson-Siegel": "ns"}


# ---------------------------------------------------------------------------
# Mise en page
# ---------------------------------------------------------------------------

layout = html.Div(className="page", children=[
    dcc.Download(id="pv-dl"),
    html.Div(className="entete", children=[
        html.Div("Prévision & valeur relative", className="titre-fin"),
        html.Div("Courbe attendue et attractivité des ténors", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),

    html.Div(className="carte barre", children=[
        html.Div(className="champ", children=[
            html.Label("Date de référence"),
            dcc.Input(id="pv-ref", type="date", className="input-date"),
        ]),
        html.Div(className="champ", children=[
            html.Label("Horizon de prévision"),
            segment("pv-horizon", list(P.HORIZONS), "1 mois"),
        ]),
        html.Div(className="champ", children=[
            html.Label("Fenêtre de référence"),
            segment("pv-fenetre", list(P.FENETRES), "5A"),
        ]),
        html.Div(className="champ", children=[
            html.Label("Fenêtre personnalisée"),
            html.Div(className="periode", children=[
                dcc.Input(id="pv-perso-debut", type="date", className="input-date"),
                html.Span("→", className="fleche"),
                dcc.Input(id="pv-perso-fin", type="date", className="input-date"),
            ]),
        ]),
        html.Button("Exporter", id="pv-export", className="btn btn-secondaire"),
    ]),

    dcc.Tabs(id="pv-onglets", value="pentes", className="onglets", children=[
        dcc.Tab(label="Pentes historiques", value="pentes", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    carte("Courbe actuelle et courbes attendues", "écart attendu − actuel en pb sous chaque taux",
                          html.Div(id="pv-table-attendues", className="tableau-defile"),
                          outils=segment("pv-methode", [(v, k) for k, v in P.METHODES.items()], "inter")),
                    html.Div(className="grille-2", children=[
                        carte("Courbes attendues", "par fenêtre de référence", graphe("pv-g-attendues", 360)),
                        carte("Erreur de prévision par ténor", "RMSE du backtest, en pb", graphe("pv-g-rmse", 360)),
                    ]),
                    carte("Backtest depuis le début de l'année", None,
                          html.Div(id="pv-table-backtest", className="tableau-defile"),
                          html.Div(id="pv-resume", className="note-bloc resume")),
                ])),
        dcc.Tab(label="Nelson-Siegel", value="ns", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(id="pv-ns-kpi", className="grille-4"),
                    html.Div(className="grille-2", children=[
                        carte("Courbe Nelson-Siegel", "observée, ajustée et prévue", graphe("pv-g-ns", 360)),
                        carte("Facteurs", "niveau, pente et courbure sur la fenêtre", graphe("pv-g-ns-facteurs", 360)),
                    ]),
                    html.Div(className="grille-2", children=[
                        carte("Ajustement et prévision par ténor", None,
                              html.Div(id="pv-table-ns", className="tableau-defile")),
                        carte("Backtest Nelson-Siegel depuis le début de l'année", None,
                              html.Div(id="pv-table-ns-bt", className="tableau-defile"),
                              note("y(τ) = β1 + β2·(1 − e^(−λτ))/(λτ) + β3·[(1 − e^(−λτ))/(λτ) − e^(−λτ)]. "
                                   "λ minimise l'erreur d'ajustement sur la fenêtre ; chaque facteur est prévu par "
                                   "un AR(1) estimé sur la fenêtre (retour progressif vers sa moyenne). "
                                   "Le modèle est ajusté directement sur les taux BKAM des ténors.")),
                    ]),
                ])),
        dcc.Tab(label="Sensibilités & valeur relative", value="rv", className="onglet",
                selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
                    html.Div(className="carte barre barre-interne", children=[
                        html.Div(className="champ", children=[html.Label("Horizon de détention"),
                                                              segment("pv-rv-horizon", list(V.HORIZONS_RV), "6 mois")]),
                        html.Div(className="champ", children=[html.Label("Scénario de courbe"),
                                                              segment("pv-scenario", list(SCENARIOS), "Inchangée")]),
                        html.Div(className="champ", children=[html.Label("Classement"),
                                                              segment("pv-critere", [(v, k) for k, v in V.CRITERES.items()],
                                                                      "point_mort")]),
                    ]),
                    carte("Classement des ténors", "rendement attendu décomposé sur l'horizon, en % du prix",
                          html.Div(id="pv-table-rv", className="tableau-defile")),
                    carte("Décomposition du rendement attendu", "carry, glissement et effet courbe",
                          graphe("pv-g-rv", 360)),
                    html.Div(className="grille-2", children=[
                        carte("Sensibilités", "titre au pair de chaque ténor",
                              html.Div(id="pv-table-sensi", className="tableau-defile")),
                        carte("Key rate durations", "choc de 1 pb par point clé, courbe zéro",
                              graphe("pv-g-krd", 380)),
                    ]),
                    html.Div(id="pv-note-rv"),
                ])),
    ]),
])
layout.children.append(signaux(['prevision-toast-0']))


# ---------------------------------------------------------------------------
# Accès aux calculs
# ---------------------------------------------------------------------------

def _perso(a, b):
    return (a, b) if a and b and a < b else (None, None)


def pentes(ref, horizon, a, b) -> dict:
    a, b = _perso(a, b)
    return P.analyse_pentes(ref or None, P.HORIZONS.get(horizon, 21), a, b, db.version_base())


def nelson_siegel(ref, horizon_obs: int, fenetre, a, b) -> dict:
    a, b = _perso(a, b)
    return P.analyse_ns(ref or None, int(horizon_obs), fenetre or "5A", a, b, db.version_base())


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("pv-ref", "value"), Output("pv-perso-debut", "value"), Output("pv-perso-fin", "value"),
          Input("maj", "data"), Input("pv-ref", "id"), State("pv-perso-debut", "value"), State("pv-perso-fin", "value"))
def initialiser(_, _montage, a, b):
    with db.connexion() as conn:
        s = db.statistiques(conn)
    if not s["nb_dates"]:
        raise PreventUpdate
    fin = s["derniere"]
    if a and b:
        return fin.date().isoformat(), no_update, no_update
    return (fin.date().isoformat(), f"{fin.year - 2}-01-01", f"{fin.year - 1}-12-31")


@callback(
    Output("pv-table-attendues", "children"), Output("pv-g-attendues", "figure"),
    Output("pv-g-rmse", "figure"), Output("pv-table-backtest", "children"), Output("pv-resume", "children"),
    Input("pv-ref", "value"), Input("pv-horizon", "value"), Input("pv-perso-debut", "value"),
    Input("pv-perso-fin", "value"), Input("pv-methode", "value"), Input("maj", "data"), Input("ui", "data"),
)
def onglet_pentes(ref, horizon, a, b, methode, _, ui):
    if not ref:
        raise PreventUpdate
    R = pentes(ref, horizon, a, b)
    att, act = R["attendues"][methode], R["actuelle"]

    # Tableau : courbe actuelle et courbes attendues avec l'écart en pb
    tete = html.Tr([html.Th("Ténor", className="gauche"), html.Th(["Actuelle", html.Div(fdate(R["t0"]), className="th-date")],
                                                                  className="col-j")] +
                   [html.Th(f) for f in att.columns])
    lignes = []
    for t in TENORS:
        cells = [html.Td(t, className="tenor gauche"), html.Td(fmt(act[t], "pct"), className="j col-j")]
        for f in att.columns:
            ecart = (att.at[t, f] - act[t]) * 100
            cells.append(html.Td(html.Div(className="cellule", children=[
                html.Span(fmt(att.at[t, f], "pct"), className="ref"),
                html.Span(fmt(ecart, "pb"), className=f"mini {cls_signe(ecart)}")])))
        lignes.append(html.Tr(cells))
    tableau = html.Table(className="tbl", children=[html.Thead(tete), html.Tbody(lignes)])

    # Graphique des courbes attendues
    fig = go.Figure()
    for f in att.columns:
        fig.add_scatter(x=TENORS, y=att[f], name=f, mode="lines+markers",
                        line=dict(color=COULEURS_FEN[f], width=1.6, dash="dot"), marker=dict(size=5),
                        hovertemplate="%{y:.3f}%<extra>" + f + "</extra>")
    fig.add_scatter(x=TENORS, y=act, name=f"Actuelle · {fdate(R['t0'])}", mode="lines+markers",
                    line=dict(color=T.NAVY, width=3), marker=dict(size=8, line=dict(color="#FFFFFF", width=1.5)),
                    hovertemplate="%{y:.3f}%<extra>Actuelle</extra>")
    fig = mise_en_page(fig, 360, yaxis_ticksuffix="%", margin=dict(l=8, r=8, t=78, b=8))

    # RMSE par ténor
    rm = R["rmse_tenor"][methode]
    fig_r = go.Figure()
    for f in rm.columns:
        fig_r.add_bar(x=TENORS, y=rm[f], name=f, marker_color=COULEURS_FEN[f],
                      hovertemplate="%{y:.1f} pb<extra>" + f + "</extra>")
    if R["ma"] is not None:
        fig_r.add_scatter(x=TENORS, y=R["ma"]["RMSE par ténor"], name="Marche aléatoire", mode="lines+markers",
                          line=dict(color=T.ENCRE, width=2), marker=dict(size=6),
                          hovertemplate="%{y:.1f} pb<extra>Marche aléatoire</extra>")
    fig_r = mise_en_page(fig_r, 360, barmode="group", bargap=0.25, yaxis_title="pb",
                         yaxis_title_font=dict(size=11, color=T.GRIS), margin=dict(l=8, r=8, t=78, b=8))

    # Backtest : toutes les méthodes, toutes les fenêtres
    if not R["n_tests"]:
        return tableau, T.theme_fig(fig, ui), T.theme_fig(fig_r, ui), \
            html.Div("Pas assez de dates cette année pour ce backtest.", className="note"), None
    bt = pd.concat({P.METHODES[m]: R["backtest"][m][["RMSE (pb)", "Gain vs marche aléatoire", "Bon sens"]]
                    for m in P.METHODES}, axis=1)
    bt.columns = [f"{m}|{c}" for m, c in bt.columns]
    spec = []
    for m in P.METHODES.values():
        spec += [{"col": f"{m}|RMSE (pb)", "lib": "RMSE (pb)", "genre": "pb1", "cls": "debut-groupe j"},
                 {"col": f"{m}|Gain vs marche aléatoire", "lib": "Gain vs MA", "genre": "part",
                  "cls": lambda v: "baisse" if v and v > 0 else "hausse"},
                 {"col": f"{m}|Bon sens", "lib": "Bon sens", "genre": "part", "cls": "ref"}]
    table_bt = table(bt, spec, premiere="Fenêtre", groupes=[(m, 3) for m in P.METHODES.values()])

    # Résumé
    toutes = pd.concat({m: R["backtest"][m]["RMSE (pb)"] for m in P.METHODES}, axis=1)
    f_best, m_best = toutes.stack().idxmin()      # index (fenêtre, méthode)
    rmse_best, rmse_ma = toutes.loc[f_best, m_best], R["ma"]["RMSE (pb)"]
    ecarts = (att - act.to_numpy()[:, None]) * 100
    f_ref = "5A" if "5A" in ecarts.columns else ecarts.columns[0]
    plus_cher = ecarts[f_ref].idxmin()
    plus_haut = ecarts[f_ref].idxmax()
    resume = [
        html.P([f"Backtest sur {R['n_tests']} dates depuis le {fdate(R['debut_tests'])}, horizon {horizon} : "
                "la meilleure combinaison est ", html.Strong(f"{P.METHODES[m_best]} · {f_best}"),
                f" ({rmse_best:.1f} pb d'erreur moyenne), contre {rmse_ma:.1f} pb pour la marche aléatoire "
                "(courbe inchangée). ",
                "Elle fait mieux que la marche aléatoire." if rmse_best < rmse_ma else
                "Aucune méthode ne bat la marche aléatoire sur cet horizon : les courbes attendues indiquent "
                "une valeur d'équilibre plutôt qu'une trajectoire à court terme."]),
        html.P([f"Avec la méthode « {P.METHODES[methode]} » et la fenêtre {f_ref}, le ténor le plus au-dessus "
                f"de sa courbe attendue est ", html.Strong(plus_cher),
                f" ({ecarts.at[plus_cher, f_ref]:+.0f} pb attendus : taux élevé, potentiellement attractif) ; "
                f"le plus en dessous est ", html.Strong(plus_haut), f" ({ecarts.at[plus_haut, f_ref]:+.0f} pb)."]),
    ]
    if R["fuite_perso"]:
        resume.append(html.P("La fenêtre personnalisée recouvre l'année testée : son backtest utilise des données "
                             "futures et surestime sa performance."))
    return tableau, T.theme_fig(fig, ui), T.theme_fig(fig_r, ui), table_bt, resume


@callback(
    Output("pv-ns-kpi", "children"), Output("pv-g-ns", "figure"), Output("pv-g-ns-facteurs", "figure"),
    Output("pv-table-ns", "children"), Output("pv-table-ns-bt", "children"),
    Input("pv-ref", "value"), Input("pv-horizon", "value"), Input("pv-fenetre", "value"),
    Input("pv-perso-debut", "value"), Input("pv-perso-fin", "value"), Input("maj", "data"), Input("ui", "data"),
)
def onglet_ns(ref, horizon, fenetre, a, b, _, ui):
    if not ref:
        raise PreventUpdate
    h = P.HORIZONS.get(horizon, 21)
    N = nelson_siegel(ref, h, fenetre, a, b)
    b0, bp = N["beta0"], N["beta_prev"]
    kpis = [
        html.Div(className="carte kpi-carte", children=[kpi("λ estimé", f"{N['lambda']:.2f}",
                                                            f"bosse de courbure vers {1.79 / N['lambda']:.0f} ans")]),
        html.Div(className="carte kpi-carte", children=[kpi("Erreur d'ajustement", f"{N['rmse_ajustement']:.1f} pb",
                                                            f"le {fdate(N['t0'])}")]),
        html.Div(className="carte kpi-carte", children=[kpi("β1 niveau long terme", f"{b0[0]:.2f}%",
                                                            f"prévu {bp[0]:.2f}% à {horizon}")]),
        html.Div(className="carte kpi-carte", children=[kpi("β2 pente · β3 courbure", f"{b0[1]:.2f} · {b0[2]:.2f}",
                                                            f"prévus {bp[1]:.2f} · {bp[2]:.2f}")]),
    ]
    tau, y_aj, y_pr = N["courbe_fine"]
    fig = go.Figure()
    fig.add_scatter(x=tau, y=y_aj, name="Ajustée", mode="lines", line=dict(color=T.NAVY, width=2.4),
                    hovertemplate="%{x:.1f} ans : %{y:.3f}%<extra>Ajustée</extra>")
    fig.add_scatter(x=tau, y=y_pr, name=f"Prévue à {horizon}", mode="lines",
                    line=dict(color=T.ROUGE, width=2, dash="dash"),
                    hovertemplate="%{x:.1f} ans : %{y:.3f}%<extra>Prévue</extra>")
    fig.add_scatter(x=P.MATURITES, y=N["actuelle"], name="Observée", mode="markers",
                    marker=dict(size=9, color=T.CIEL, line=dict(color=T.NAVY, width=1.5)), text=TENORS,
                    hovertemplate="%{text} : %{y:.3f}%<extra>Observée</extra>")
    fig = mise_en_page(fig, 360, yaxis_ticksuffix="%", xaxis_title="Maturité (années)",
                       xaxis_title_font=dict(size=11, color=T.GRIS), hovermode="closest")

    be = N["betas"]
    fig_f = go.Figure()
    for c, coul in zip(be.columns, [T.NAVY, T.VERT, T.ROUGE]):
        fig_f.add_scatter(x=be.index, y=be[c], name=c, mode="lines", line=dict(color=coul, width=1.6),
                          hovertemplate="%{y:.3f}<extra>" + c + "</extra>")
    fig_f = mise_en_page(fig_f, 360)

    df = pd.DataFrame({"Actuelle": N["actuelle"], "Ajustée": N["ajustee"],
                       "Résidu (pb)": (N["actuelle"] - N["ajustee"]) * 100, "Prévue": N["prevue"],
                       "Variation attendue (pb)": (N["prevue"] - N["actuelle"]) * 100})
    spec = [{"col": "Actuelle", "lib": "Actuelle", "genre": "pct", "cls": "j col-j"},
            {"col": "Ajustée", "lib": "Ajustée NS", "genre": "pct", "cls": "ref"},
            {"col": "Résidu (pb)", "lib": "Résidu (pb)", "genre": "pb", "cls": cls_signe},
            {"col": "Prévue", "lib": f"Prévue {horizon}", "genre": "pct", "cls": "j debut-groupe"},
            {"col": "Variation attendue (pb)", "lib": "Var. attendue", "genre": "pb", "cls": cls_signe}]
    t_ns = table(df, spec, dense="dense")

    bt = N["backtest"]
    if bt.empty:
        t_bt = html.Div("Pas assez de dates cette année pour ce backtest.", className="note")
    else:
        spec_bt = [{"col": "RMSE (pb)", "lib": "RMSE (pb)", "genre": "pb1", "cls": "j"},
                   {"col": "MAE (pb)", "lib": "MAE (pb)", "genre": "pb1", "cls": "ref"},
                   {"col": "Biais (pb)", "lib": "Biais (pb)", "genre": "pb", "cls": "ref"},
                   {"col": "Bon sens", "lib": "Bon sens", "genre": "part", "cls": "ref"},
                   {"col": "Gain vs marche aléatoire", "lib": "Gain vs MA", "genre": "part",
                    "cls": lambda v: "baisse" if v and v > 0 else "hausse"}]
        t_bt = table(bt, spec_bt, premiere="Fenêtre")
    return kpis, T.theme_fig(fig, ui), T.theme_fig(fig_f, ui), t_ns, t_bt


def courbe_scenario(scenario: str, ref, fenetre, a, b, H: float, obs_an: float, actuelle: pd.Series):
    code = SCENARIOS.get(scenario)
    if code is None:
        return None
    fen = fenetre if fenetre in P.FENETRES else "5A"
    if fen == "Perso" and _perso(a, b) == (None, None):
        fen = "5A"
    if code == "ns":
        return nelson_siegel(ref, max(1, round(H * obs_an)), fen, a, b)["prevue"]
    if code == "derive":
        niv, td = P.donnees(db.version_base())
        moy = P.moyennes(niv, td, P.FENETRES[fen], _perso(a, b))
        t0 = P.date_reference(niv, ref)
        return actuelle + H * obs_an * moy["derive"].loc[t0]
    return pentes(ref, "1 mois", a, b)["attendues"][code][fen]


def calcul_rv(ref, fenetre, a, b, horizon_rv, scenario, critere) -> tuple[pd.DataFrame, pd.Series]:
    """Classement des ténors : volatilité et annualisation mesurées sur la fenêtre de référence."""
    niv, _td = P.donnees(db.version_base())
    t0 = P.date_reference(niv, ref)
    actuelle = niv.loc[t0]
    fen = P.FENETRES.get(fenetre, 5)
    pa, pb = _perso(a, b)
    if fen == "perso" and pa is None:
        fen = 5
    hist = niv.loc[P._fenetre_masque(niv.index, t0, fen, (pa, pb))]
    vol = (hist.diff() * 100).std()
    obs_an = sc.facteur_annualisation(hist.index)
    H = V.HORIZONS_RV.get(horizon_rv, 0.5)
    prevue = courbe_scenario(scenario, ref, fenetre, a, b, H, obs_an, actuelle)
    return V.rendement_attendu(actuelle, prevue, H, vol, obs_an, critere or "point_mort"), actuelle


@callback(
    Output("pv-table-rv", "children"), Output("pv-g-rv", "figure"), Output("pv-table-sensi", "children"),
    Output("pv-g-krd", "figure"), Output("pv-note-rv", "children"),
    Input("pv-ref", "value"), Input("pv-fenetre", "value"), Input("pv-perso-debut", "value"),
    Input("pv-perso-fin", "value"), Input("pv-rv-horizon", "value"), Input("pv-scenario", "value"),
    Input("pv-critere", "value"), Input("maj", "data"), Input("ui", "data"),
)
def onglet_rv(ref, fenetre, a, b, horizon_rv, scenario, critere, _, ui):
    if not ref:
        raise PreventUpdate
    rv, actuelle = calcul_rv(ref, fenetre, a, b, horizon_rv, scenario, critere)

    spec = [{"col": "Ténor", "lib": "Ténor", "genre": "txt", "cls": "tenor"},
            {"col": "Taux (%)", "lib": "Taux", "genre": "pct", "cls": "ref"},
            {"col": "Carry (%)", "lib": "Carry", "genre": "n3", "cls": "debut-groupe ref"},
            {"col": "Glissement (%)", "lib": "Glissement", "genre": "n3", "cls": cls_signe},
            {"col": "Effet courbe (%)", "lib": "Effet courbe", "genre": "n3", "cls": cls_signe},
            {"col": "Rendement attendu (%)", "lib": "Rendement", "genre": "n3", "cls": "j"},
            {"col": "Rendement annualisé (%)", "lib": "Annualisé", "genre": "n2", "cls": "ref"},
            {"col": "Point mort (pb)", "lib": "Point mort (pb)", "genre": "pb1", "cls": "debut-groupe j"},
            {"col": "Volatilité horizon (pb)", "lib": "Vol. horizon (pb)", "genre": "pb1", "cls": "ref"},
            {"col": "Point mort / vol", "lib": "PM / vol", "genre": "n2", "cls": "j"},
            {"col": "Rendement / risque", "lib": "Rend. / risque", "genre": "n2", "cls": "j"}]
    t_rv = table(rv.reset_index().set_index("Rang"), spec, premiere="Rang",
                 groupes=[("Titre", 2), ("Rendement attendu (%)", 5), ("Point mort et risque", 4)], dense="dense")

    ordre = rv.reindex(TENORS)
    fig = go.Figure()
    for col, nom, coul in [("Carry (%)", "Carry", T.NAVY), ("Glissement (%)", "Glissement", T.CIEL),
                           ("Effet courbe (%)", "Effet courbe", T.ROUGE)]:
        fig.add_bar(x=TENORS, y=ordre[col], name=nom, marker_color=coul,
                    hovertemplate="%{y:.3f} %<extra>" + nom + "</extra>")
    fig.add_scatter(x=TENORS, y=ordre["Rendement attendu (%)"], name="Total", mode="markers",
                    marker=dict(size=11, color=T.ENCRE, symbol="diamond", line=dict(color="#FFFFFF", width=1)),
                    hovertemplate="%{y:.3f} %<extra>Total</extra>")
    fig = mise_en_page(fig, 360, barmode="relative", bargap=0.45, yaxis_ticksuffix=" %")

    sensi = V.tableau_sensibilites(actuelle)
    t_s = table(sensi, [{"col": "Taux (%)", "lib": "Taux", "genre": "pct", "cls": "ref"},
                        {"col": "Sensibilité", "lib": "Sensibilité", "genre": "n2", "cls": "j"},
                        {"col": "Convexité", "lib": "Convexité", "genre": "n2", "cls": "ref"},
                        {"col": "DV01 (MAD / 1 M)", "lib": "DV01 (MAD / 1 M)", "genre": "pb1", "cls": "ref"}])

    K = V.matrice_krd(actuelle)
    fig_k = go.Figure(go.Heatmap(
        z=K.to_numpy(), x=K.columns, y=K.index, colorscale=[[0, "#F4F6FA"], [1, T.NAVY]],
        text=np.where(K.to_numpy() > 0.005, np.round(K.to_numpy(), 2).astype(str), ""),
        texttemplate="%{text}", textfont=dict(size=10), showscale=False,
        hovertemplate="Titre %{y} · point %{x} : %{z:.3f}<extra></extra>"))
    fig_k = mise_en_page(fig_k, 380, xaxis_title="Point clé choqué", yaxis_title="Titre",
                         xaxis_title_font=dict(size=11, color=T.GRIS), yaxis_title_font=dict(size=11, color=T.GRIS))
    fig_k.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)")

    top = rv.index[0]
    texte = note(
        f"Scénario « {scenario} »" + ("" if SCENARIOS.get(scenario) is None else f", fenêtre {fenetre}")
        + f", horizon {horizon_rv}. Ténor le mieux classé ({V.CRITERES[critere or 'point_mort'].lower()}) : {top}.",
        "Carry = y × H. Glissement : à courbe inchangée, le titre vieillit et prend le taux de la maturité T − H. "
        "Effet courbe : −sensibilité × variation prévue + ½ convexité × variation². "
        "Point mort = hausse de taux qui efface carry + glissement ; rapporté à la volatilité historique des "
        "variations sur l'horizon (fenêtre de référence).",
        "Les ténors qui arrivent à échéance avant l'horizon sont supposés réinvestis au même taux, sans risque de "
        "prix : pas de point mort, classés en dernier pour les critères de risque. Les KRD sont calculées sur la "
        "courbe zéro-coupon ; avec la convention de valorisation VL (actualisation au seul taux de l'échéance), "
        "tout le risque d'un BDT in fine est porté par son point d'échéance.")
    return t_rv, T.theme_fig(fig, ui), t_s, T.theme_fig(fig_k, ui), carte("Méthode", None, texte)


@callback(Output("pv-dl", "data"), Output({"type": "sig-toast", "id": "prevision-toast-0"}, "data"),
          Input("pv-export", "n_clicks"),
          State("pv-ref", "value"), State("pv-horizon", "value"), State("pv-fenetre", "value"),
          State("pv-perso-debut", "value"), State("pv-perso-fin", "value"),
          State("pv-rv-horizon", "value"), State("pv-scenario", "value"), State("pv-critere", "value"),
          prevent_initial_call=True)
def exporter(n, ref, horizon, fenetre, a, b, horizon_rv, scenario, critere):
    if not n:
        raise PreventUpdate
    R = pentes(ref, horizon, a, b)
    feuilles = {}
    for m, nom in P.METHODES.items():
        df = R["attendues"][m].copy()
        df.insert(0, "Actuelle", R["actuelle"])
        feuilles[f"Attendue - {nom}"[:31]] = df.rename_axis("Ténor").reset_index()
        if not R["backtest"][m].empty:
            feuilles[f"Backtest - {nom}"[:31]] = R["backtest"][m].reset_index()
    N = nelson_siegel(ref, P.HORIZONS.get(horizon, 21), fenetre, a, b)
    feuilles["Nelson-Siegel"] = pd.DataFrame({"Actuelle": N["actuelle"], "Ajustée": N["ajustee"],
                                              "Prévue": N["prevue"]}).rename_axis("Ténor").reset_index()
    if not N["backtest"].empty:
        feuilles["Backtest - Nelson-Siegel"] = N["backtest"].reset_index()
    rv, act = calcul_rv(ref, fenetre, a, b, horizon_rv, scenario, critere)
    feuilles[f"Classement ({horizon_rv})"] = rv.reset_index()
    feuilles["Sensibilités"] = V.tableau_sensibilites(act).reset_index()
    feuilles["KRD"] = V.matrice_krd(act).rename_axis("Titre").reset_index()
    nom = f"prevision_courbe_{(ref or '').replace('-', '')}.xlsx"
    return dcc.send_bytes(db.exporter_classeur(feuilles), nom), toast("Prévisions exportées en Excel.")
