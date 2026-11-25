"""Analyse de la courbe — Taux forward implicites et comparaison avec les prévisions."""

from __future__ import annotations

import time

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html, no_update
from dash.exceptions import PreventUpdate

import base_courbe as db
import forward as F
import prevision as PV
import theme as T
from composants import carte, cls_signe, fdate, graphe, kpi, mise_en_page, note, segment, table, toast, signaux

dash.register_page(__name__, path="/forward", name="Taux forward", order=4, section="courbe")

TENORS = db.TENOR_COLS
COULEURS = {"Actuelle": T.GRIS_CLAIR, "Forward implicite": T.ROUGE, "Pentes inter-ténors": "#3F7CC0",
            "Pente vs taux directeur": T.VERT, "Moyenne des variations": T.MENTHE, "Nelson-Siegel": T.NAVY}
TIRETS = {"Actuelle": "dot", "Forward implicite": "solid"}


def cls_gain(v) -> str:
    """Gain de précision : positif = mieux que la marche aléatoire (vert)."""
    if v is None or pd.isna(v):
        return "ref"
    return "baisse" if v > 0.0005 else "hausse" if v < -0.0005 else "stable"


layout = html.Div(className="page", children=[
    dcc.Download(id="fw-dl"),
    html.Div(className="entete", children=[
        html.Div("Taux forward", className="titre-fin"),
        html.Div("Courbe implicite et prévisions", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),
    html.Div(className="carte barre", children=[
        html.Div(className="champ", children=[
            html.Label("Date de référence"),
            dcc.DatePickerSingle(id="fw-ref", display_format="DD/MM/YYYY", placeholder="JJ/MM/AAAA",
                                 first_day_of_week=1, month_format="MMMM YYYY", className="date-fr"),
        ]),
        html.Div(className="champ", children=[html.Label("Horizon"), segment("fw-horizon", list(F.HORIZONS), "3 mois")]),
        html.Div(className="champ", children=[html.Label("Fenêtre des prévisions"),
                                              segment("fw-fenetre", [f for f in PV.FENETRES if f != "Perso"], "5A")]),
        html.Button("Exporter", id="fw-export", className="btn btn-secondaire"),
    ]),
    html.Div(className="carte", children=[html.Div(id="fw-kpi", className="kpi-grille kpi-6")]),
    html.Div(className="grille-2", children=[
        carte("Courbe zéro-coupon et forward 1 an", "rendements, ZC bootstrappés, forward 1 an", graphe("fw-g-courbes", 360)),
        carte("Courbe implicite par date de départ", "taux des ténors attendus dans 3 mois … 10 ans, en %",
              html.Div(id="fw-matrice", className="tableau-defile")),
    ]),
    carte("Courbe attendue à l'horizon : forward vs prévisions", None,
          html.Div(id="fw-titre-comp", className="info info-legere"),
          graphe("fw-g-comp", 360),
          html.Div(id="fw-table-comp", className="tableau-defile")),
    html.Div(className="grille-2 grille-backtest", children=[
        carte("Backtest depuis le début de l'année", "mêmes dates de test que la fenêtre Prévision",
              html.Div(id="fw-backtest", className="tableau-defile"), html.Div(id="fw-resume")),
        carte("Erreur par ténor", "RMSE du backtest, en pb", graphe("fw-g-rmse", 320)),
    ]),
    html.Div(className="grille-2", children=[
        carte("Forward vs réalisé", "5 dernières années",
              graphe("fw-g-serie", 340), outils=segment("fw-tenor", TENORS, "2ans")),
        carte("Prime de terme ex post", "forward − taux réalisé à l'horizon, moyenne en pb",
              graphe("fw-g-prime", 260), html.Div(id="fw-table-prime", className="tableau-defile")),
    ]),
    carte("Méthode", None, note(
        "Courbe zéro-coupon : logique de ZCP (taux_courbe.py). Les ténors courts sont convertis du monétaire à "
        "l'actuariel, les maturités de 1 à 30 ans sont bootstrappées comme des obligations au pair à coupon "
        "annuel ; entre deux nœuds, les taux ZC sont interpolés linéairement.",
        "Courbe implicite dans h : pour chaque ténor, le taux forward entre h et h + maturité, exprimé dans la "
        "convention du ténor (monétaire jusqu'à 52 semaines, rendement au pair au-delà). À h = 0 on retrouve "
        "la courbe : elle est directement comparable aux courbes prévues par les pentes historiques et "
        "Nelson-Siegel (fenêtre Prévision, même horizon en nombre d'observations).",
        "Sous l'hypothèse des anticipations pures, le forward est la courbe attendue par le marché. Il contient "
        "en réalité une prime de terme : un forward supérieur au taux finalement observé (prime positive) "
        "rémunère le risque de duration. Un écart prévision − forward positif signifie que le modèle attend des "
        "taux plus hauts que le marché.")),
])
layout.children.append(signaux(['forward-toast-0']))


def _ref_defaut():
    niv, _ = PV.donnees(db.version_base())
    return niv.index[-1].strftime("%Y-%m-%d")


@callback(Output("fw-ref", "date"), Input("maj", "data"), Input("fw-ref", "id"), State("fw-ref", "date"))
def ref_defaut(_, _montage, ref):
    if ref:
        raise PreventUpdate
    return _ref_defaut()


@callback(
    Output("fw-kpi", "children"), Output("fw-g-courbes", "figure"), Output("fw-matrice", "children"),
    Output("fw-titre-comp", "children"), Output("fw-g-comp", "figure"), Output("fw-table-comp", "children"),
    Output("fw-backtest", "children"), Output("fw-resume", "children"), Output("fw-g-rmse", "figure"),
    Output("fw-g-prime", "figure"), Output("fw-table-prime", "children"),
    Input("fw-ref", "date"), Input("fw-horizon", "value"), Input("fw-fenetre", "value"),
    Input("maj", "data"), Input("ui", "data"),
)
def afficher(ref, horizon, fenetre, _, ui):
    if not ref:
        raise PreventUpdate
    A = F.analyse(ref, horizon or "3 mois", fenetre or "5A", db.version_base())
    t0 = A["t0"]

    # Indicateurs
    cartes = [kpi(k["lib"], f"{k['forward']:.3f} %", f"{k['ecart']:+.1f} pb vs {k['actuel']:.3f} % aujourd'hui")
              for k in A["kpis"]]
    cartes.append(kpi("Pente 10a − 2a dans 1 an", f"{A['pente_fwd']:.0f} pb",
                      f"{A['pente_fwd'] - A['pente_auj']:+.1f} pb vs {A['pente_auj']:.0f} pb aujourd'hui"))

    # Courbes
    ans = np.array([db.TENORS[t] / 365.25 for t in TENORS])
    g1 = go.Figure()
    g1.add_scatter(x=ans, y=A["actuelle"].to_numpy(), mode="lines+markers", name="Rendements (ténors)",
                   line=dict(color=T.GRIS_CLAIR, width=2, dash="dot"), marker=dict(size=7),
                   text=TENORS, hovertemplate="%{text} : %{y:.3f} %<extra></extra>")
    g1.add_scatter(x=A["t_fin"], y=A["zc_fin"], mode="lines", name="Zéro-coupon", line=dict(color=T.NAVY, width=2.5),
                   hovertemplate="%{x:.2f} ans : %{y:.3f} %<extra>ZC</extra>")
    g1.add_scatter(x=A["t_fin"], y=A["fwd1_fin"], mode="lines", name="Forward 1 an dans t",
                   line=dict(color=T.ROUGE, width=2), hovertemplate="dans %{x:.2f} ans : %{y:.3f} %<extra>Forward 1 an</extra>")
    g1 = mise_en_page(g1, 360, xaxis_title="Maturité / départ (années)", yaxis_title="%",
                      legend=dict(orientation="h", y=1.08, x=0))

    # Matrice
    m = A["matrice"]
    spec_m = [{"col": t, "lib": t, "genre": "n3", "cls": "j" if t in ("2ans", "10ans") else ""} for t in TENORS]
    base_auj = m.loc["Aujourd'hui"]
    t_mat = table(m, spec_m, premiere="Départ", dense="dense",
                  styles=lambda i, c, v: None if i == "Aujourd'hui" else
                  {"background": f"rgba(226, 0, 26, {min(abs(v - base_auj[c]) / 2.5, 0.35):.3f})"
                   if v > base_auj[c] else f"rgba(14, 122, 62, {min(abs(v - base_auj[c]) / 2.5, 0.35):.3f})"})

    # Comparaison à l'horizon
    c = A["courbes"]
    g2 = go.Figure()
    for col in c.columns:
        g2.add_scatter(x=TENORS, y=c[col], mode="lines+markers", name=col,
                       line=dict(color=COULEURS.get(col, T.GRIS), width=3 if col == "Forward implicite" else 2,
                                 dash=TIRETS.get(col, "dash" if col != "Nelson-Siegel" else "solid")),
                       marker=dict(size=6), hovertemplate="%{x} : %{y:.3f} %<extra>" + col + "</extra>")
    g2 = mise_en_page(g2, 360, yaxis_title="%", legend=dict(orientation="h", y=1.1, x=0))
    g2.update_xaxes(type="category")

    comp = c.copy()
    autres = [col for col in c.columns if col not in ("Actuelle", "Forward implicite")]
    for col in autres:
        comp[f"Δ {col}"] = (c[col] - c["Forward implicite"]) * 100
    comp["Δ forward − actuelle"] = (c["Forward implicite"] - c["Actuelle"]) * 100
    spec_c = [{"col": col, "lib": col.replace("Pente vs taux directeur", "Pente vs TD").replace("Moyenne des variations", "Moy. variations"),
               "genre": "n3", "cls": "j" if col == "Forward implicite" else "ref"} for col in c.columns]
    spec_c += [{"col": "Δ forward − actuelle", "lib": "Forward − actuelle", "genre": "pb", "cls": cls_signe}]
    spec_c += [{"col": f"Δ {col}", "lib": col.replace("Pente vs taux directeur", "Pente vs TD")
                .replace("Moyenne des variations", "Moy. variations").replace("Pentes inter-ténors", "Pentes inter"),
                "genre": "pb", "cls": cls_signe} for col in autres]
    t_comp = table(comp, spec_c, premiere="Ténor",
                   groupes=[("Niveaux (%)", len(c.columns)), ("Écart (pb)", 1), ("Prévision − forward (pb)", len(autres))])
    titre = (f"Courbe du {fdate(t0)} et courbe attendue dans {A['horizon']} ({A['h_cal']} jours pour le forward, "
             f"{A['h_obs']} observations pour les prévisions, fenêtre {A['fenetre']}).")

    # Backtest
    bt = A["backtest"]
    if bt.empty:
        t_bt = html.Div(f"Pas de date de test : l'horizon {A['horizon']} dépasse la période écoulée depuis le "
                        f"1er janvier. Voir la précision sur 5 ans dans le tableau de la prime de terme.",
                        className="info info-legere")
        resume = None
        g3 = mise_en_page(go.Figure(), 320)
    else:
        spec_b = [{"col": "RMSE (pb)", "lib": "RMSE (pb)", "genre": "pb1", "cls": "j"},
                  {"col": "MAE (pb)", "lib": "MAE (pb)", "genre": "pb1", "cls": "ref"},
                  {"col": "Biais (pb)", "lib": "Biais (pb)", "genre": "pb", "cls": "ref"},
                  {"col": "Bon sens", "lib": "Bon sens", "genre": "part", "cls": "ref"},
                  {"col": "Gain vs marche aléatoire", "lib": "Gain vs MA", "genre": "part", "cls": cls_gain}]
        meilleur = bt["RMSE (pb)"].idxmin()
        t_bt = table(bt, spec_b, premiere="Méthode",
                     styles=lambda i, col, v: {"background": "var(--surligne)"} if i == meilleur else
                     ({"fontWeight": 600} if i == "Forward implicite" else None))
        rang = int(bt["RMSE (pb)"].rank().loc["Forward implicite"]) if "Forward implicite" in bt.index else None
        resume = html.Div(className="note-bloc resume", children=[html.P(
            f"{A['n_tests']} dates de test depuis le {fdate(A['debut_tests'])}. Méthode la plus précise : {meilleur} "
            f"({bt.loc[meilleur, 'RMSE (pb)']:.1f} pb). Le forward implicite est {rang}e sur {len(bt)} "
            f"(RMSE {bt.loc['Forward implicite', 'RMSE (pb)']:.1f} pb, biais {bt.loc['Forward implicite', 'Biais (pb)']:+.1f} pb).")])
        rt = A["rmse_tenor"]
        g3 = go.Figure()
        for col in rt.columns:
            g3.add_bar(x=TENORS, y=rt[col], name=col, marker_color=COULEURS.get(col, T.GRIS_CLAIR if col == "Marche aléatoire" else T.GRIS),
                       hovertemplate="%{x} : %{y:.1f} pb<extra>" + col + "</extra>")
        g3 = mise_en_page(g3, 320, barmode="group", bargap=0.25, yaxis_title="pb",
                          legend=dict(orientation="h", y=1.12, x=0, font=dict(size=9)))
        g3.update_xaxes(type="category")

    # Prime de terme
    pr = A["prime"]
    g4 = go.Figure()
    for col, coul in (("Tout l'historique", T.NAVY), ("5 dernières années", T.CIEL), ("Depuis le début de l'année", T.MENTHE)):
        g4.add_bar(x=TENORS, y=pr[col], name=col, marker_color=coul, hovertemplate="%{x} : %{y:+.1f} pb<extra>" + col + "</extra>")
    g4 = mise_en_page(g4, 260, barmode="group", bargap=0.25, yaxis_title="pb", legend=dict(orientation="h", y=1.15, x=0))
    g4.update_xaxes(type="category")
    g4.add_hline(y=0, line_color=T.GRIS_CLAIR, line_width=1)
    spec_p = [{"col": "Tout l'historique", "lib": "Historique", "genre": "pb", "cls": cls_signe},
              {"col": "5 dernières années", "lib": "5 ans", "genre": "pb", "cls": cls_signe},
              {"col": "Depuis le début de l'année", "lib": "YTD", "genre": "pb", "cls": cls_signe},
              {"col": "RMSE forward 5 ans", "lib": "RMSE forward 5a", "genre": "pb1", "cls": "j"},
              {"col": "RMSE marche aléatoire 5 ans", "lib": "RMSE MA 5a", "genre": "pb1", "cls": "ref"}]
    t_prime = table(pr, spec_p, premiere="Ténor", dense="dense",
                    groupes=[("Prime de terme moyenne (pb)", 3), ("Précision sur 5 ans (pb)", 2)])

    return (cartes, T.theme_fig(g1, ui), t_mat, titre, T.theme_fig(g2, ui), t_comp, t_bt, resume,
            T.theme_fig(g3, ui), T.theme_fig(g4, ui), t_prime)


@callback(Output("fw-g-serie", "figure"), Input("fw-ref", "date"), Input("fw-horizon", "value"),
          Input("fw-fenetre", "value"), Input("fw-tenor", "value"), Input("maj", "data"), Input("ui", "data"))
def serie(ref, horizon, fenetre, tenor, _, ui):
    if not ref:
        raise PreventUpdate
    A = F.analyse(ref, horizon or "3 mois", fenetre or "5A", db.version_base())
    t0, tenor = A["t0"], tenor or "2ans"
    debut = t0 - pd.DateOffset(years=5)
    reel = A["niv"].loc[(A["niv"].index >= debut) & (A["niv"].index <= t0), tenor]
    fw = A["serie_fwd"].loc[reel.index, tenor]
    g = go.Figure()
    g.add_scatter(x=reel.index, y=reel, mode="lines", name="Réalisé", line=dict(color=T.NAVY, width=2),
                  hovertemplate="%{x|%d/%m/%Y} : %{y:.3f} %<extra>Réalisé</extra>")
    g.add_scatter(x=fw.index, y=fw, mode="lines", name=f"Forward formé {A['horizon']} plus tôt",
                  line=dict(color=T.ROUGE, width=1.6), hovertemplate="%{x|%d/%m/%Y} : %{y:.3f} %<extra>Forward</extra>")
    g = mise_en_page(g, 340, yaxis_title="%", legend=dict(orientation="h", y=1.1, x=0), hovermode="x unified")
    g.update_xaxes(tickformat="%m/%Y")
    return T.theme_fig(g, ui)


@callback(Output("fw-dl", "data"), Output({"type": "sig-toast", "id": "forward-toast-0"}, "data"),
          Input("fw-export", "n_clicks"), State("fw-ref", "date"), State("fw-horizon", "value"),
          State("fw-fenetre", "value"), prevent_initial_call=True)
def exporter(n, ref, horizon, fenetre):
    if not n or not ref:
        raise PreventUpdate
    A = F.analyse(ref, horizon or "3 mois", fenetre or "5A", db.version_base())
    feuilles = {
        "Courbe implicite": A["matrice"].reset_index(names="Départ"),
        "Forward vs prévisions": A["courbes"].reset_index(names="Ténor"),
        "Backtest YTD": A["backtest"].reset_index() if not A["backtest"].empty else pd.DataFrame(),
        "Prime de terme": A["prime"].reset_index(names="Ténor"),
        "Courbe ZC": pd.DataFrame({"Maturité (années)": A["t_fin"], "Zéro-coupon (%)": A["zc_fin"],
                                   "Forward 1 an (%)": A["fwd1_fin"]}),
    }
    nom = f"forward_{A['t0']:%Y%m%d}_{(horizon or '3 mois').replace(' ', '')}.xlsx"
    return dcc.send_bytes(db.exporter_classeur(feuilles), nom), toast("Taux forward exportés en Excel.")
