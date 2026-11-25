"""
Vue Portefeuille (gabarit commun) : composition depuis Manar, revalorisation des titres, comparaison avec la
valorisation Manar, répartitions par maturité résiduelle et par émetteur, prime moyenne.

Chaque page de portefeuille (pages/fmgc.py, fcm.py, fml.py, fcob.py, fms.py,
fco.py, fcor.py, fmb.py) appelle construire("CODE") :
seul le code du portefeuille transmis à la requête change. Les identifiants des composants sont préfixés
par le code (ex. « fcm-table ») pour que les pages coexistent.
"""

from __future__ import annotations

import time

from datetime import date, datetime

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dash_table, dcc, html, no_update
from dash.dash_table.Format import Format, Group, Scheme, Sign
from dash.exceptions import PreventUpdate

import base_courbe as db
import base_titre as BT
import controle as C
import portefeuille as P
import pricer as PR
import swap as SW
import theme as T
from composants import signaux, carte, etiquette, fdate, graphe, kpi, mise_en_page, note, segment, table, toast


F0 = Format(precision=0, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ")
F2 = Format(precision=2, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ")
F2S = Format(precision=2, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ", sign=Sign.positive)
F3 = Format(precision=3, scheme=Scheme.fixed)
F3S = Format(precision=3, scheme=Scheme.fixed, sign=Sign.positive)
PCT = Format(precision=2, scheme=Scheme.percentage)

COL_TITRES = [
    {"id": "isin", "name": "ISIN"}, {"id": "code", "name": "Code"}, {"id": "libelle", "name": "Libellé"},
    {"id": "categorie", "name": "Cat."}, {"id": "type_lib", "name": "Type"}, {"id": "echeance_txt", "name": "Échéance"},
    {"id": "quantite", "name": "Quantité", "type": "numeric", "format": F0},
    {"id": "prix_unitaire", "name": "Prix calculé", "type": "numeric", "format": F2},
    {"id": "total_valo", "name": "Total valo", "type": "numeric", "format": F2},
    {"id": "valorisation", "name": "Valorisation Manar", "type": "numeric", "format": F2},
    {"id": "ecart", "name": "Écart (MAD)", "type": "numeric", "format": F2S},
    {"id": "ecart_unitaire", "name": "Écart / titre", "type": "numeric", "format": F2S},
    {"id": "poids", "name": "Poids", "type": "numeric", "format": PCT},
    {"id": "taux_rendement", "name": "TRI Manar", "type": "numeric", "format": F3},
    {"id": "taux_calcul", "name": "Taux calculé", "type": "numeric", "format": F3},
    {"id": "sensibilite", "name": "Sensi", "type": "numeric", "format": F3},
    {"id": "statut_lib", "name": "Statut"}, {"id": "cause", "name": "Cause probable"},
]
COL_OPCVM = [
    {"id": "CODE_ISIN", "name": "ISIN"}, {"id": "LIBELLE", "name": "Libellé"},
    {"id": "QUANTITE", "name": "Quantité", "type": "numeric", "format": F0},
    {"id": "VALORISATION", "name": "Valorisation", "type": "numeric", "format": F2},
    {"id": "POID", "name": "Poids", "type": "numeric", "format": PCT},
]
TEXTE = ("isin", "code", "libelle", "categorie", "type_lib", "echeance_txt", "statut_lib", "cause", "CODE_ISIN", "LIBELLE")


def style_table(selection: bool, page: int = 20) -> dict:
    return dict(
        style_as_list_view=True, style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "transparent", "color": "var(--gris)", "fontWeight": 600, "fontSize": "0.6rem",
                      "letterSpacing": "0.1em", "textTransform": "uppercase", "border": "none",
                      "borderBottom": "1px solid var(--trait)", "padding": "0.5rem 0.55rem", "textAlign": "right",
                      "whiteSpace": "normal"},
        style_header_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in TEXTE],
        style_cell={"backgroundColor": "transparent", "color": "var(--encre)", "border": "none",
                    "borderBottom": "1px solid var(--trait-doux)", "fontFamily": "inherit", "fontSize": "0.78rem",
                    "padding": "0.42rem 0.55rem", "textAlign": "right", "fontVariantNumeric": "tabular-nums"},
        style_cell_conditional=[
            *[{"if": {"column_id": c}, "textAlign": "left"} for c in TEXTE],
            {"if": {"column_id": "isin"}, "fontWeight": 600, "color": "var(--navy)"},
            {"if": {"column_id": "CODE_ISIN"}, "fontWeight": 600, "color": "var(--navy)"},
            {"if": {"column_id": "libelle"}, "maxWidth": "240px", "overflow": "hidden", "textOverflow": "ellipsis",
             "whiteSpace": "nowrap"},
            {"if": {"column_id": "statut_lib"}, "fontWeight": 600},
            {"if": {"column_id": "cause"}, "minWidth": "280px", "whiteSpace": "normal", "fontSize": "0.72rem",
             "color": "var(--gris)", "lineHeight": "1.3"},
        ],
        style_data_conditional=[
            {"if": {"filter_query": '{statut_lib} = "Conforme"', "column_id": "statut_lib"}, "color": "var(--vert)"},
            {"if": {"filter_query": '{statut_lib} = "Écart de valorisation"', "column_id": "statut_lib"}, "color": "var(--rouge)"},
            {"if": {"column_id": "total_valo"}, "fontWeight": 700},
        ],
        sort_action="native", page_action="native", page_size=page, cell_selectable=False,
        **({"row_selectable": "single"} if selection else {}),
    )


def champ(lib, comp, classe=""):
    return html.Div(className=f"champ {classe}", children=[html.Label(lib), comp])


def jour_fr(id_):
    return dcc.DatePickerSingle(id=id_, display_format="DD/MM/YYYY", placeholder="JJ/MM/AAAA", first_day_of_week=1,
                                month_format="MMMM YYYY", className="date-fr")


def mad(v, signe=False):
    if v is None or pd.isna(v):
        return "—"
    return (f"{v:+,.2f}" if signe else f"{v:,.2f}").replace(",", " ")


def millions(v, signe=False):
    if v is None or pd.isna(v):
        return "—"
    return (f"{v / 1e6:+,.2f} M" if signe else f"{v / 1e6:,.2f} M").replace(",", " ")


def construire(PORTEFEUILLE: str) -> html.Div:
    """Construit la vue du portefeuille (mise en page + callbacks) et renvoie la mise en page."""
    px = PORTEFEUILLE.lower()

    layout = html.Div(className="page", children=[
        dcc.Store(id=f"{px}-donnees"), dcc.Store(id=f"{px}-vl"), dcc.Download(id=f"{px}-dl"),
        html.Div(className="entete", children=[
            html.Div(PORTEFEUILLE, className="titre-fin"),
            html.Div("Portefeuille · revalorisation depuis Manar", className="titre-gras"),
            html.Div(className="titre-trait"),
        ]),
        html.Div(className="carte barre", children=[
            champ("Utilisateur Manar", dcc.Input(id=f"{px}-user", type="text", autoComplete="username",
                                                 className="input-date input-champ input-court")),
            champ("Mot de passe", dcc.Input(id=f"{px}-mdp", type="password", autoComplete="current-password",
                                            className="input-date input-champ input-court")),
            champ("Date du portefeuille", jour_fr(f"{px}-date")),
            champ("Date de la courbe", jour_fr(f"{px}-courbe")),
            champ("Date de valorisation", jour_fr(f"{px}-valo")),
            champ("Tolérance (MAD / titre)", dcc.Input(id=f"{px}-tol", type="number", value=0.05, min=0, step="any",
                                                       debounce=True, className="input-date input-champ input-court")),
            html.Button("Charger depuis Manar", id=f"{px}-charger", className="btn btn-primaire"),
            html.Button("Exporter", id=f"{px}-export", className="btn btn-secondaire"),
        ]),
        dcc.Loading(type="dot", color=T.NAVY, children=html.Div(id=f"{px}-info", className="note")),
        carte("Calcul de la VL", "à la date de valorisation · données Manar du jour et valorisation des titres",
              dcc.Loading(type="dot", color=T.NAVY, children=html.Div(id=f"{px}-vl-contenu")),
              outils=html.Div(className="vl-outils", children=[
                  html.Label("Frais de gestion (% / an)", htmlFor=f"{px}-fg"),
                  dcc.Input(id=f"{px}-fg", type="number", min=0, step="any", debounce=True, placeholder="ex. 1,5",
                            persistence=True, persistence_type="local",
                            className="input-date input-champ input-court"),
                  html.Label("Banque actif (saisie manuelle)", htmlFor=f"{px}-banque"),
                  dcc.Input(id=f"{px}-banque", type="number", step="any", debounce=True,
                            placeholder="calculée si vide", className="input-date input-champ vl-banque"),
                  html.Button("Actualiser la VL", id=f"{px}-charger-vl", className="btn btn-secondaire"),
              ])),
        html.Div(id=f"{px}-contenu", style={"display": "none"}, children=[
            html.Div(className="carte", children=[html.Div(id=f"{px}-kpi", className="kpi-grille kpi-6")]),
            html.Div(className="grille-2 grille-backtest", children=[
                carte("Composition par catégorie", "titres comparés et OPCVM externes",
                      html.Div(id=f"{px}-categories", className="tableau-defile")),
                carte("Poids par catégorie", "en % de l'actif chargé", graphe(f"{px}-g-poids", 300)),
            ]),
            html.Div(className="carte", children=[html.Div(id=f"{px}-kpi-prime", className="kpi-grille kpi-6")]),
            html.Div(className="grille-2 grille-backtest", children=[
                carte("Répartition par maturité résiduelle", "poids, prime moyenne et part de la prime par tranche",
                      html.Div(id=f"{px}-maturite", className="tableau-defile"),
                      outils=segment(f"{px}-echeance", [("Échéance retenue", "retenue"), ("Échéance finale", "finale")],
                                     "retenue")),
                carte("Poids et prime par maturité", "barres : poids ; points : prime moyenne (pb)",
                      graphe(f"{px}-g-maturite", 330)),
            ]),
            html.Div(className="grille-2 grille-backtest", children=[
                carte("Répartition par émetteur", "en % des titres", html.Div(id=f"{px}-emetteurs", className="tableau-defile")),
                carte("Principaux émetteurs", "poids en % des titres", graphe(f"{px}-g-emetteurs", 380)),
            ]),
            carte("Titres valorisés", "prix plein calculé × quantité = total valo, comparé à la valorisation Manar ; "
                  "cochez une ligne pour le détail", html.Div(id=f"{px}-n", className="note"),
                  dash_table.DataTable(id=f"{px}-table", columns=COL_TITRES, data=[], **style_table(True)),
                  outils=segment(f"{px}-filtre", [("Toutes", "toutes"), ("Écarts", "ecarts"), ("Non comparées", "non")], "toutes")),
            html.Div(id=f"{px}-detail"),
            carte("Titres OPCVM", "lignes sans date d'émission, de jouissance ni d'échéance · cochées : OPCVM internes, "
                  "retirés de la base des frais de gestion",
                  html.Div(id=f"{px}-n-opcvm", className="note"),
                  dash_table.DataTable(id=f"{px}-table-opcvm", columns=COL_OPCVM, data=[],
                                       **{**style_table(False, 15), "row_selectable": "multi"})),
        ]),
        carte("Méthode", None, note(
            f"La composition du portefeuille {PORTEFEUILLE} (actifs de type D) est lue dans Manar à la date du portefeuille. "
            "Les identifiants servent uniquement à la connexion : ils ne sont ni enregistrés ni conservés.",
            "Les lignes dont la date d'émission, la date de jouissance et la date d'échéance sont vides sont classées en "
            "OPCVM externes. Les autres titres sont retrouvés dans base_titre.xlsx par leur code ISIN et valorisés avec "
            "les règles du Pricer sur la courbe choisie (par défaut : courbe = date du portefeuille, valorisation = jour "
            "ouvré suivant) ; total valo = prix plein unitaire × quantité.",
            "Une ligne est conforme si l'écart par titre (total valo − valorisation Manar, divisé par la quantité) reste "
            "sous la tolérance. Sensibilité et duration du portefeuille : moyennes pondérées par le total valo.",
            "Répartitions par maturité et par émetteur, prime moyenne : titres pondérés par la valorisation Manar (hors "
            "OPCVM externes). Échéance retenue = prochaine révision pour un révisable, échéance sinon ; échéance finale = "
            "échéance du titre. La part de la prime d'une tranche est sa contribution Σ poids × prime rapportée à la "
            "prime moyenne du portefeuille. La ligne MA0000094351 (9435) est toujours valorisée à 0.")),
    ])


    # ---------------------------------------------------------------------------
    # Chargement depuis Manar
    # ---------------------------------------------------------------------------

    @callback(Output(f"{px}-date", "date"), Input("maj", "data"), Input(f"{px}-date", "id"), State(f"{px}-date", "date"))
    def date_defaut(_, _montage, d):
        if d:
            raise PreventUpdate
        with db.connexion() as conn:
            dc = conn.execute("SELECT MAX(date) FROM tenors").fetchone()[0]
        return dc


    @callback(Output(f"{px}-courbe", "date"), Output(f"{px}-valo", "date"), Input(f"{px}-date", "date"))
    def dates_liees(d):
        """Courbe = date du portefeuille, valorisation = jour ouvré suivant (modifiables ensuite)."""
        if not d:
            raise PreventUpdate
        return d, C.jour_ouvre_suivant(date.fromisoformat(d)).isoformat()


    @callback(Output(f"{px}-donnees", "data"), Output(f"{px}-info", "children", allow_duplicate=True),
              Output({"type": "sig-toast", "id": f"{px}-toast-0"}, "data"),
              Input(f"{px}-charger", "n_clicks"), State(f"{px}-user", "value"), State(f"{px}-mdp", "value"), State(f"{px}-date", "date"),
              prevent_initial_call=True, running=[(Output(f"{px}-charger", "disabled"), True, False)])
    def charger(n, user, mdp, d):
        if not n:
            raise PreventUpdate
        if not (user and mdp and d):
            return no_update, no_update, toast("Renseignez l'utilisateur, le mot de passe et la date du portefeuille.", "erreur")
        try:
            df = P.charger_portefeuille(user.strip(), mdp, date.fromisoformat(d), PORTEFEUILLE)
        except ModuleNotFoundError:
            return no_update, no_update, toast("Module oracledb absent : pip install oracledb", "erreur")
        except Exception as e:                                                   # noqa: BLE001
            msg = str(e).split("\n")[0][:220]
            return no_update, no_update, toast(f"Connexion à Manar impossible : {msg}", "erreur")
        if df.empty:
            return None, f"Aucune ligne pour {PORTEFEUILLE} au {fdate(d)}.", toast("Requête vide.", "erreur")
        df = P.normaliser(df)
        for c in P.DATES:
            df[c] = df[c].dt.strftime("%Y-%m-%d")
        donnees = {"date": d, "charge": datetime.now().strftime("%d/%m/%Y %H:%M"), "lignes": df.to_dict("records")}
        return donnees, no_update, toast(f"{len(df)} lignes chargées depuis Manar.")


    def _calcul(donnees, valo, courbe, tol):
        df = pd.DataFrame(donnees["lignes"])
        titres, opcvm = P.separer(df)
        courbe_df, info = SW.courbe_en_base(courbe)
        if courbe_df.empty:
            return titres, opcvm, pd.DataFrame(), info
        return titres, opcvm, P.valoriser(titres, date.fromisoformat(valo), courbe_df, float(tol or 0)), info


    @callback(Output(f"{px}-info", "children"), Output(f"{px}-contenu", "style"), Output(f"{px}-kpi", "children"),
              Output(f"{px}-categories", "children"), Output(f"{px}-g-poids", "figure"),
              Output(f"{px}-table-opcvm", "data"), Output(f"{px}-n-opcvm", "children"),
              Output(f"{px}-table-opcvm", "selected_rows"),
              Input(f"{px}-donnees", "data"), Input(f"{px}-valo", "date"), Input(f"{px}-courbe", "date"), Input(f"{px}-tol", "value"),
              Input("maj", "data"), Input("ui", "data"), State(f"{px}-table-opcvm", "selected_rows"),
              State(f"{px}-table-opcvm", "data"))
    def afficher(donnees, valo, courbe, tol, _, ui, sel_avant, data_avant):
        cache = {"display": "none"}
        if not donnees:
            return (f"Saisissez vos identifiants Manar et la date, puis « Charger depuis Manar ».", cache,
                    *[no_update] * 6)
        if not (valo and courbe):
            raise PreventUpdate
        titres, opcvm, v, info = _calcul(donnees, valo, courbe, tol)
        entete = (f"{PORTEFEUILLE} au {fdate(donnees['date'])} · chargé le {donnees['charge']} · "
                  f"{len(titres) + len(opcvm)} lignes · valorisation au {fdate(valo)} · {info}")
        if v.empty:
            return entete, cache, *[no_update] * 6
        R = P.resume(v, opcvm)
        kpis = [
            kpi("Actif chargé", millions(R["actif_total"]), f"titres {millions(R['valo_titres'])} · OPCVM {millions(R['valo_opcvm'])}"),
            kpi("Valorisation Manar", millions(R["total_manar"]), f"{R['n_comparees']} titres comparés"),
            kpi("Total recalculé", millions(R["total_calcul"]), "prix plein × quantité"),
            kpi("Écart", mad(R["ecart"], True), f"{R['ecart_pb']:+.2f} pb" if pd.notna(R["ecart_pb"]) else "",
                T.ROUGE if R["n_ecart"] else None),
            kpi("Lignes conformes", f"{R['n_ok']} / {R['n_comparees']}",
                f"{R['n_ecart']} écart(s) · {R['n_non']} non comparée(s)", T.ROUGE if R["n_ecart"] else None),
            kpi("Sensibilité", f"{R['sensibilite']:.3f}" if pd.notna(R["sensibilite"]) else "—",
                f"duration {R['duration']:.3f} ans" if pd.notna(R["duration"]) else ""),
        ]
        comp = v[v["statut"].isin(P.COMPAREES)].copy()
        comp["categorie"] = comp["categorie"].fillna("—")
        g = comp.groupby("categorie")
        cat = pd.DataFrame({"Lignes": g.size(), "Valorisation Manar": g["valorisation"].sum(), "Total valo": g["total_valo"].sum()})
        cat["Écart"] = cat["Total valo"] - cat["Valorisation Manar"]
        if len(opcvm):
            cat.loc["OPCVM externes"] = [len(opcvm), opcvm["VALORISATION"].sum(), np.nan, np.nan]
        non = v[~v["statut"].isin(P.COMPAREES)]
        if len(non):
            cat.loc["Non comparées"] = [len(non), non["valorisation"].sum(), np.nan, np.nan]
        cat["Poids"] = cat["Valorisation Manar"] / cat["Valorisation Manar"].sum()
        cat.loc["Total"] = [cat["Lignes"].sum(), cat["Valorisation Manar"].sum(), cat["Total valo"].sum(),
                            cat["Écart"].sum(), 1.0]
        spec = [{"col": "Lignes", "lib": "Lignes", "genre": "int", "cls": ""},
                {"col": "Valorisation Manar", "lib": "Valo Manar", "genre": "mad", "cls": ""},
                {"col": "Total valo", "lib": "Total valo", "genre": "mad", "cls": "j"},
                {"col": "Écart", "lib": "Écart", "genre": "mad+", "cls": ""},
                {"col": "Poids", "lib": "Poids", "genre": "part", "cls": ""}]
        t_cat = table(cat, spec, premiere="Catégorie", dense="dense",
                      styles=lambda i, c, val: {"fontWeight": 700} if i == "Total" else None)

        poids = cat.drop(index="Total")["Poids"].sort_values()
        fig = go.Figure(go.Bar(x=poids.values * 100, y=poids.index, orientation="h", marker_color=T.NAVY,
                               text=[f"{x:.1%}" for x in poids.values], textposition="outside",
                               hovertemplate="%{y} : %{x:.2f} %<extra></extra>"))
        fig = mise_en_page(fig, 300, xaxis_title="%", margin=dict(l=110, r=40, t=10, b=40))
        fig.update_xaxes(range=[0, max(float(poids.max()) * 100 * 1.18, 5)])

        op_df = opcvm[[c["id"] for c in COL_OPCVM if c["id"] in opcvm.columns]].copy()
        op_df["id"] = opcvm["CODE_ISIN"].astype(str).values if len(opcvm) else []
        op = op_df.replace({np.nan: None}).to_dict("records")
        # OPCVM internes : détection automatique, sauf si la même liste est déjà affichée (choix de l'utilisateur gardé)
        if data_avant is not None and [r.get("id") for r in data_avant] == [r["id"] for r in op] and sel_avant is not None:
            sel = sel_avant
        else:
            sel = [i for i, b in enumerate(P.opcvm_internes(opcvm).tolist()) if b]
        return entete, {}, kpis, t_cat, T.theme_fig(fig, ui), op, \
            f"{len(opcvm)} OPCVM · {mad(R['valo_opcvm'])} MAD (valorisation Manar, non revalorisés).", sel


    @callback(Output(f"{px}-kpi-prime", "children"), Output(f"{px}-maturite", "children"),
              Output(f"{px}-g-maturite", "figure"), Output(f"{px}-emetteurs", "children"),
              Output(f"{px}-g-emetteurs", "figure"),
              Input(f"{px}-donnees", "data"), Input(f"{px}-valo", "date"), Input(f"{px}-courbe", "date"),
              Input(f"{px}-tol", "value"), Input(f"{px}-echeance", "value"), Input("maj", "data"), Input("ui", "data"))
    def analyses(donnees, valo, courbe, tol, echeance, _, ui):
        if not (donnees and valo and courbe):
            raise PreventUpdate
        _, _, v, _ = _calcul(donnees, valo, courbe, tol)
        if v.empty:
            raise PreventUpdate
        d = P.base_analyse(v, date.fromisoformat(valo), echeance or "retenue")
        I = P.indicateurs_prime(d)
        lib_ech = "échéance retenue" if (echeance or "retenue") == "retenue" else "échéance finale"
        kpis = [
            kpi("Prime moyenne", f"{I['prime_moyenne']:.1f} pb" if pd.notna(I["prime_moyenne"]) else "—",
                "pondérée par la valorisation des titres"),
            kpi("Prime hors État", f"{I['prime_hors_etat']:.1f} pb" if pd.notna(I["prime_hors_etat"]) else "—",
                f"{I['part_hors_etat']:.1%} des titres"),
            kpi("Concentration de la prime", I["tranche_concentration"] or "—",
                f"{I['part_concentration']:.0%} de la prime pour {I['poids_concentration']:.1%} des titres"
                if I["tranche_concentration"] else "aucune prime"),
            kpi("Maturité moyenne", f"{I['maturite_moyenne']:.2f} ans" if pd.notna(I["maturite_moyenne"]) else "—", lib_ech),
            kpi("Part à moins d'un an", f"{I['part_moins_1an']:.1%}", lib_ech),
            kpi("Part à 10 ans et plus", f"{I['part_plus_10ans']:.1%}", lib_ech),
        ]
        tm = P.repartition_maturite(d)
        spec_m = [{"col": "Lignes", "lib": "Lignes", "genre": "int", "cls": ""},
                  {"col": "Valorisation", "lib": "Valorisation", "genre": "mad", "cls": ""},
                  {"col": "Poids", "lib": "Poids", "genre": "part", "cls": "j"},
                  {"col": "Prime moyenne (pb)", "lib": "Prime moy. (pb)", "genre": "pb1", "cls": ""},
                  {"col": "Part de la prime", "lib": "Part de la prime", "genre": "part", "cls": ""},
                  {"col": "Sensibilité", "lib": "Sensi", "genre": "n3", "cls": ""}]
        conc = I["tranche_concentration"]
        t_mat = table(tm, spec_m, premiere="Maturité", dense="dense",
                      styles=lambda i, c, val: {"fontWeight": 700} if i == "Total" else
                      ({"background": "var(--surligne)"} if i == conc else None))

        tr = tm.drop(index="Total")
        fig = go.Figure()
        fig.add_bar(x=tr.index, y=tr["Poids"] * 100, name="Poids (%)",
                    marker_color=[T.MENTHE if i == conc else T.NAVY for i in tr.index],
                    hovertemplate="%{x} : %{y:.1f} %<extra>Poids</extra>")
        fig.add_scatter(x=tr.index, y=tr["Prime moyenne (pb)"], name="Prime moyenne (pb)", yaxis="y2", mode="markers+lines",
                        line=dict(color=T.ROUGE, width=1.5, dash="dot"), marker=dict(size=8, color=T.ROUGE),
                        hovertemplate="%{x} : %{y:.1f} pb<extra>Prime moyenne</extra>")
        fig = mise_en_page(fig, 330, yaxis_title="Poids (%)", legend=dict(orientation="h", y=1.12, x=0),
                           yaxis2=dict(title="Prime (pb)", overlaying="y", side="right", showgrid=False, rangemode="tozero"))
        fig.update_xaxes(type="category")

        te = P.repartition_emetteur(d)
        spec_e = [{"col": "Lignes", "lib": "Lignes", "genre": "int", "cls": ""},
                  {"col": "Valorisation", "lib": "Valorisation", "genre": "mad", "cls": ""},
                  {"col": "Poids", "lib": "Poids", "genre": "part", "cls": "j"},
                  {"col": "Prime moyenne (pb)", "lib": "Prime moy. (pb)", "genre": "pb1", "cls": ""},
                  {"col": "Maturité moyenne (ans)", "lib": "Mat. moy. (ans)", "genre": "n2", "cls": ""},
                  {"col": "Sensibilité", "lib": "Sensi", "genre": "n3", "cls": ""}]
        t_em = table(te, spec_e, premiere="Émetteur", dense="dense",
                     styles=lambda i, c, val: {"fontWeight": 700} if i == "Total" else None)
        top = te.drop(index="Total")["Poids"]
        if len(top) > 12:
            top = pd.concat([top.iloc[:12], pd.Series({"Autres": top.iloc[12:].sum()})])
        top = top.iloc[::-1]
        fe = go.Figure(go.Bar(x=top.values * 100, y=top.index, orientation="h", marker_color=T.NAVY,
                              text=[f"{x:.1%}" for x in top.values], textposition="outside",
                              hovertemplate="%{y} : %{x:.2f} %<extra></extra>"))
        fe = mise_en_page(fe, 380, xaxis_title="%", margin=dict(l=120, r=40, t=10, b=40))
        fe.update_xaxes(range=[0, max(float(top.max()) * 100 * 1.2, 5)])
        return kpis, t_mat, T.theme_fig(fig, ui), t_em, T.theme_fig(fe, ui)

    @callback(Output(f"{px}-table", "data"), Output(f"{px}-table", "selected_rows"), Output(f"{px}-n", "children"),
              Input(f"{px}-donnees", "data"), Input(f"{px}-valo", "date"), Input(f"{px}-courbe", "date"), Input(f"{px}-tol", "value"),
              Input(f"{px}-filtre", "value"), Input("maj", "data"))
    def lignes(donnees, valo, courbe, tol, filtre, _):
        if not (donnees and valo and courbe):
            raise PreventUpdate
        _, _, v, _ = _calcul(donnees, valo, courbe, tol)
        if v.empty:
            return [], [], None
        sel = {"ecarts": v["statut"] == "prix", "non": ~v["statut"].isin(P.COMPAREES)}.get(filtre, pd.Series(True, index=v.index))
        d = v[sel].copy()
        d = d.reindex(d["ecart"].abs().sort_values(ascending=False, na_position="last").index) if filtre != "toutes" \
            else d.sort_values("poids", ascending=False)
        d["echeance_txt"] = pd.to_datetime(d["echeance"]).dt.strftime("%d/%m/%Y")
        d["id"] = d["isin"]
        cols = [c["id"] for c in COL_TITRES] + ["id"]
        return d[cols].replace({np.nan: None}).to_dict("records"), [], f"{len(d)} titres."


    @callback(Output(f"{px}-detail", "children"), Input(f"{px}-table", "selected_row_ids"),
              State(f"{px}-donnees", "data"), State(f"{px}-valo", "date"), State(f"{px}-courbe", "date"), State(f"{px}-tol", "value"))
    def detail(ids, donnees, valo, courbe, tol):
        if not ids or not donnees:
            return None
        _, _, v, _ = _calcul(donnees, valo, courbe, tol)
        r = v[v["isin"] == ids[0]].iloc[0]
        if not r.get("code") or pd.isna(r.get("code")):
            return html.Div(className="carte", children=[etiquette("Détail", r["isin"]),
                                                        html.Div("ISIN absent de base_titre.xlsx.", className="alerte")])
        f = BT.fiche(r["code"])
        params = [("Code base", r["code"]), ("Type", r["type_lib"]), ("Émetteur", f["emetteur"]), ("Nominal", mad(f["nominal"])),
                  ("Taux facial", f"{f['tf']:.3f} %"), ("Base du coupon", "Exact/360" if f["base_coupon"] == "360" else "Exact/exact"),
                  ("Spread", f"{f['prime']:g} pb"), ("Émission", fdate(f["em"])), ("Jouissance", fdate(f["jouis"])),
                  ("Échéance", fdate(f["ech"])), ("Prochaine révision", fdate(f["revisions"][0]) if f["revisions"] else "—"),
                  ("Délai de grâce", f"{f['grace']:g} an(s)" if f.get("grace") else "—")]
        cmp_rows = [("Quantité", f"{r['quantite']:,.0f}".replace(",", " "), "", ""),
                    ("Prix unitaire", mad(r["prix_manar"]), mad(r["prix_unitaire"]), mad(r["ecart_unitaire"], True)),
                    ("Valorisation", mad(r["valorisation"]), mad(r["total_valo"]), mad(r["ecart"], True)),
                    ("Taux", f"{r['taux_rendement']:.3f} %" if pd.notna(r["taux_rendement"]) else "—",
                     f"{r['taux_calcul']:.3f} %" if pd.notna(r["taux_calcul"]) else "—", "")]
        enfants = [
            html.Div(className="carte-tete", children=[etiquette("Détail", f"{r['isin']} — {r['libelle']}")]),
            html.Div(className="swap-grille", children=[
                html.Div(className="swap-ligne", children=[
                    html.Div("Paramètres (base titre)", className="swap-titre"),
                    html.Div(className="info-grille info-grille-3", children=[
                        html.Div(className="info-ligne", children=[html.Span(a, className="info-lib"), html.Span(b, className="info-val")])
                        for a, b in params]),
                ]),
                html.Div(className="swap-ligne", children=[
                    html.Div("Manar / calcul", className="swap-titre"),
                    html.Table(className="tbl dense", children=[
                        html.Thead(html.Tr([html.Th(""), html.Th("Manar"), html.Th("Calcul"), html.Th("Écart")])),
                        html.Tbody([html.Tr([html.Td(a, className="premiere"), html.Td(b), html.Td(c), html.Td(d)])
                                    for a, b, c, d in cmp_rows])]),
                    html.Div(className="info-grille", children=[
                        html.Div(className="info-ligne large", children=[html.Span("Statut", className="info-lib"),
                                                                        html.Span(r["statut_lib"], className="info-val")]),
                        html.Div(className="info-ligne large", children=[html.Span("Cause probable", className="info-lib"),
                                                                        html.Span(r["cause"] or "—", className="info-val info-libre")]),
                    ]),
                ]),
            ]),
        ]
        if r["statut"] in P.COMPAREES:
            courbe_df, _ = SW.courbe_en_base(courbe)
            val = PR.valoriser(SW._parametres(f, date.fromisoformat(valo)), courbe_df)
            tab = val["tab"].set_index("date_de_reglement")
            spec = [{"col": "maturite_residuelle_en_annee", "lib": "Maturité (ans)", "genre": "n3", "cls": "ref"},
                    {"col": "taux_de_valorisation", "lib": "Taux de valo.", "genre": "pct", "cls": "ref"},
                    {"col": "flux", "lib": "Flux par titre", "genre": "mad", "cls": "j"},
                    {"col": "flux_actualise", "lib": "Flux actualisé", "genre": "mad", "cls": "j"}]
            enfants.append(html.Div(className="tableau-defile", style={"marginTop": "1rem"}, children=[
                html.Div(f"Échéancier par titre · coupon couru {mad(val['cc'])} MAD", className="note"),
                table(tab, spec, premiere="Date", dense="dense")]))
        return html.Div(className="carte", children=enfants)


    @callback(Output(f"{px}-dl", "data"), Output({"type": "sig-toast", "id": f"{px}-toast-1"}, "data"),
              Input(f"{px}-export", "n_clicks"), State(f"{px}-donnees", "data"), State(f"{px}-valo", "date"),
              State(f"{px}-courbe", "date"), State(f"{px}-tol", "value"), State(f"{px}-vl", "data"),
              State(f"{px}-fg", "value"), State(f"{px}-table-opcvm", "selected_row_ids"), State(f"{px}-banque", "value"),
              prevent_initial_call=True)
    def exporter(n, donnees, valo, courbe, tol, vl_data, fg, internes, banque):
        if not n:
            raise PreventUpdate
        if not donnees:
            return no_update, toast("Chargez d'abord le portefeuille depuis Manar.", "erreur")
        titres, opcvm, v, _ = _calcul(donnees, valo, courbe, tol)
        R = P.resume(v, opcvm)
        resume = pd.DataFrame([
            ("Portefeuille", PORTEFEUILLE), ("Date du portefeuille", fdate(donnees["date"])), ("Date de la courbe", fdate(courbe)),
            ("Date de valorisation", fdate(valo)), ("Tolérance (MAD / titre)", tol), ("Titres", R["n_titres"]),
            ("OPCVM externes", R["n_opcvm"]), ("Titres comparés", R["n_comparees"]), ("Conformes", R["n_ok"]),
            ("Écarts", R["n_ecart"]), ("Valorisation Manar (titres comparés)", R["total_manar"]),
            ("Total recalculé", R["total_calcul"]), ("Écart (MAD)", R["ecart"]), ("Écart (pb)", R["ecart_pb"]),
            ("Valorisation OPCVM externes", R["valo_opcvm"]), ("Sensibilité du portefeuille", R["sensibilite"]),
            ("Duration du portefeuille", R["duration"]),
            *[(lib, P.indicateurs_prime(P.base_analyse(v, date.fromisoformat(valo)))[k]) for lib, k in (
                ("Prime moyenne (pb)", "prime_moyenne"), ("Prime hors État (pb)", "prime_hors_etat"),
                ("Tranche de concentration de la prime", "tranche_concentration"),
                ("Part de la prime dans cette tranche", "part_concentration"),
                ("Maturité moyenne retenue (ans)", "maturite_moyenne"))],
        ], columns=["Indicateur", "Valeur"]).astype({"Valeur": str})
        noms = {"isin": "CODE_ISIN", "code": "Code base", "libelle": "Libellé", "categorie": "Catégorie", "type_lib": "Type",
                "emetteur": "Émetteur", "echeance": "Échéance", "quantite": "QUANTITE", "prix_manar": "Prix unitaire Manar",
                "prix_unitaire": "Prix unitaire calculé", "total_valo": "TOTAL_VALO", "valorisation": "VALORISATION",
                "ecart": "Écart (MAD)", "ecart_unitaire": "Écart par titre", "ecart_pb": "Écart (pb)", "poids": "POID",
                "taux_rendement": "TAUX_RENDEMENT Manar", "taux_calcul": "Taux calculé", "sensibilite": "Sensibilité",
                "duration": "Duration", "statut_lib": "Statut", "cause": "Cause probable"}
        t = v[[c for c in noms if c in v.columns]].rename(columns=noms)
        d_ret = P.base_analyse(v, date.fromisoformat(valo), "retenue")
        d_fin = P.base_analyse(v, date.fromisoformat(valo), "finale")
        feuilles = {"Résumé": resume, "Titres": t, "Écarts": t[(v["statut"] == "prix").to_numpy()],
                    "Maturité (retenue)": P.repartition_maturite(d_ret).reset_index(),
                    "Maturité (finale)": P.repartition_maturite(d_fin).reset_index(),
                    "Émetteurs": P.repartition_emetteur(d_ret).reset_index(names="Émetteur"),
                    "OPCVM": opcvm}
        if vl_data:
            V = P.calcul_vl(v, opcvm, _lire_vl(vl_data), date.fromisoformat(valo), fg, internes, banque)
            feuilles["Calcul VL"] = pd.DataFrame([
                ("Date de valorisation", fdate(valo)), ("Banque J-1", V["banque_j1"]),
                ("Σ MONTANT_2 signé (date de valeur = J)", V["esp_valeur"]),
                ("Σ MONTANT_4 signé (date d'échéance = J)", V["esp_echeance"]),
                ("Somme position espèce", V["position_espece"]), ("Banque actif calculée", V["banque_actif_calcule"]),
                ("Banque actif retenue" + (" (saisie manuelle)" if V["banque_manuelle"] else ""), V["banque_actif"]),
                ("Charges en cours", V["charges"]), ("Agios", V["agios"]), ("Espèce actif", V["espece_actif"]),
                ("Total valo des titres valorisés", V["titres_prices"]), ("Valorisation des OPCVM", V["valo_opcvm"]),
                ("Valo des actifs", V["valo_actifs"]),
                ("Opérations en cours", V["operations_cours"]), ("Actif brut", V["actif_brut"]),
                ("OPCVM internes", V["opcvm_internes"]), ("Base de frais de gestion", V["base_fg"]),
                ("Taux de frais de gestion (% / an)", V["taux_fg"]), ("Jours", V["jours"]),
                ("Frais de gestion", V["frais"]), ("Actif net", V["actif_net"]), ("Nombre de parts", V["parts"]),
                ("VL", V["vl"]), ("Actif net Manar", V["actif_net_manar"]), ("VL Manar", V["vl_manar"]),
                ("Écart actif net", V["ecart_an"]), ("Écart VL", V["ecart_vl"]),
            ], columns=["Poste", "Montant"])
            feuilles["Opérations du jour"] = V["operations"]
            feuilles["Opérations en cours"] = V["engagements"]
            feuilles["HISTO_PORTEF_FP_GROUP"] = V["fp"]
        return dcc.send_bytes(db.exporter_classeur(feuilles), f"{PORTEFEUILLE}_{(valo or '').replace('-', '')}.xlsx"), \
            toast("Portefeuille exporté en Excel.")


    # ---------------------------------------------------------------------------
    # Calcul de la VL
    # ---------------------------------------------------------------------------

    @callback(Output(f"{px}-vl", "data"), Output({"type": "sig-toast", "id": f"{px}-toast-2"}, "data"),
              Input(f"{px}-charger", "n_clicks"), Input(f"{px}-charger-vl", "n_clicks"),
              State(f"{px}-user", "value"), State(f"{px}-mdp", "value"), State(f"{px}-valo", "date"),
              prevent_initial_call=True, running=[(Output(f"{px}-charger-vl", "disabled"), True, False)])
    def charger_vl(n1, n2, user, mdp, valo):
        if not (n1 or n2):
            raise PreventUpdate
        if not (user and mdp and valo):
            return no_update, toast("Renseignez l'utilisateur, le mot de passe et la date de valorisation.", "erreur")
        try:
            res = P.charger_vl(user.strip(), mdp, date.fromisoformat(valo), PORTEFEUILLE)
        except ModuleNotFoundError:
            return no_update, toast("Module oracledb absent : pip install oracledb", "erreur")
        except Exception as e:                                                   # noqa: BLE001
            return no_update, toast(f"Données VL Manar indisponibles : {str(e).splitlines()[0][:200]}", "erreur")
        data = {"date": valo, "charge": datetime.now().strftime("%d/%m/%Y %H:%M")}
        for k, df in res.items():
            data[k] = df.to_json(orient="split", date_format="iso")
        alerte = "" if len(res.get("fp", [])) else " · aucune ligne HISTO_PORTEF_FP_GROUP à cette date"
        return data, toast(f"Données VL du {fdate(valo)} chargées{alerte}.", "erreur" if alerte else "ok")


    def _dates_fr(df: pd.DataFrame) -> pd.DataFrame:
        """Colonnes de dates (ou nommées DATE_*) au format jj/mm/aaaa pour l'affichage."""
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]) or str(c).upper().startswith("DATE"):
                d = pd.to_datetime(df[c], errors="coerce")
                if d.notna().any():
                    df[c] = d.dt.strftime("%d/%m/%Y").where(d.notna(), None)
        return df


    def _lire_vl(data) -> dict:
        from io import StringIO
        return {k: pd.read_json(StringIO(data[k]), orient="split") for k in ("engagements", "operations", "fp") if k in data}


    def _vl(donnees, vl_data, valo, courbe, tol, fg, internes):
        titres, opcvm, v, _ = _calcul(donnees, valo, courbe, tol)
        return P.calcul_vl(v, opcvm, _lire_vl(vl_data), date.fromisoformat(valo), fg, internes)


    def _ligne(lib, val, genre="ligne", signe="", detail=None):
        return html.Tr(className=f"vl-{genre}", children=[
            html.Td(signe, className="vl-signe"),
            html.Td([lib, html.Span(detail, className="vl-detail")] if detail else lib, className="gauche"),
            html.Td(mad(val) if val is not None and pd.notna(val) else "—", className="vl-montant"),
        ])


    def _table_vl(V):
        fg_txt = (f"{V['taux_fg']:.4g} % × {V['jours']} j / 360 (depuis le {fdate(V['jour_precedent'])})"
                  if V["taux_fg"] not in (None, "") else "saisir le taux de frais de gestion")
        lignes = [
            _ligne("Banque J-1", V["banque_j1"], detail="BANQUE_DEB_JOUR"),
            _ligne("Somme position espèce", V["position_espece"], signe="+",
                   detail=f"MONTANT_2 en valeur J {mad(V['esp_valeur'])} ({V['n_neg_valeur']} poste(s) PENSRO, "
                          f"PENSRT, ABDT, ATCN, AOBL en négatif) + MONTANT_4 à échéance J {mad(V['esp_echeance'])} "
                          f"({V['n_neg_echeance']} poste(s) PENSDOB, PENSRTOB en négatif)"),
            (_ligne("Banque actif (saisie manuelle)", V["banque_actif"], "total", "=",
                    detail=f"calculée : {mad(V['banque_actif_calcule'])} (banque J-1 + position espèce) · écart "
                           f"{mad(V['banque_actif'] - V['banque_actif_calcule'], True)}")
             if V["banque_manuelle"] else _ligne("Banque actif", V["banque_actif"], "total", "=")),
            _ligne("Charges en cours", V["charges"], signe="−",
                   detail=f"ESP_CHANGES_ENCOURS {mad(V['charges_esp'])} + CUMUL_PROVISIONS_CHARGES {mad(V['charges_prov'])}"),
            _ligne("Agios", V["agios"], signe="−", detail="CUMUL_PROVISIONS_AGIOS"),
            _ligne("Espèce actif", V["espece_actif"], "total", "="),
            _ligne("Valo des actifs", V["valo_actifs"], signe="+",
                   detail=f"total valo des titres valorisés {mad(V['titres_prices'])} ({V['n_titres_valorises']} lignes) "
                          f"+ OPCVM {mad(V['valo_opcvm'])}"
                          + (f" · {V['n_titres_non_valorises']} titre(s) non valorisé(s) non repris"
                             if V["n_titres_non_valorises"] else "")),
            _ligne("Opérations en cours", V["operations_cours"], signe="+", detail="Σ MONTANT_CV (VALO_ENGAGEMENT)"),
            _ligne("Actif brut", V["actif_brut"], "total", "="),
            _ligne("OPCVM internes", V["opcvm_internes"], "detail-ligne", "−", detail="cochés dans la table des OPCVM"),
            _ligne("Base de frais de gestion", V["base_fg"], "sous-total", "="),
            _ligne("Frais de gestion", V["frais"], signe="−", detail=fg_txt),
            _ligne("Actif net", V["actif_net"], "resultat", "="),
            _ligne("Nombre de parts", None if pd.isna(V["parts"]) else V["parts"], detail="NOMBRE_PARTS_AV_VL"),
            html.Tr(className="vl-resultat vl-vl", children=[
                html.Td("", className="vl-signe"), html.Td("VL = actif net / nombre de parts", className="gauche"),
                html.Td(f"{V['vl']:,.2f}".replace(",", " ") if pd.notna(V["vl"]) else "—", className="vl-montant")]),
        ]
        return html.Table(className="tbl vl-table", children=[html.Tbody(lignes)])


    def _fig_vl(V, ui):
        """Passage de la valo des actifs à l'actif net (axe resserré pour lire les petits montants)."""
        frais = V["frais"] if pd.notna(V["frais"]) else 0.0
        etapes = [("Valo des actifs", V["valo_actifs"], "absolute"), ("Espèce actif", V["espece_actif"], "relative"),
                  ("Opérations en cours", V["operations_cours"], "relative"), ("Actif brut", 0, "total"),
                  ("Frais de gestion", -frais, "relative"), ("Actif net", 0, "total")]
        cumuls = np.cumsum([V["valo_actifs"], V["espece_actif"], V["operations_cours"], 0, -frais])
        lo, hi = float(min(cumuls.min(), V["valo_actifs"])), float(max(cumuls.max(), V["actif_brut"]))
        marge = max((hi - lo) * 0.25, abs(hi) * 0.0005, 1.0)
        fig = go.Figure(go.Waterfall(
            x=[e[0] for e in etapes], y=[e[1] / 1e6 for e in etapes], measure=[e[2] for e in etapes],
            text=[f"{(e[1] if e[2] != 'total' else (V['actif_brut'] if e[0] == 'Actif brut' else V['actif_net'])) / 1e6:,.2f}"
                  .replace(",", " ") for e in etapes], textposition="outside",
            increasing=dict(marker=dict(color=T.VERT)), decreasing=dict(marker=dict(color=T.ROUGE)),
            totals=dict(marker=dict(color=T.NAVY)), connector=dict(line=dict(color=T.GRIS_CLAIR, width=1)),
            hovertemplate="%{x} : %{y:,.2f} M MAD<extra></extra>"))
        fig = mise_en_page(fig, 380, yaxis_title="M MAD", showlegend=False, hovermode="closest",
                           margin=dict(l=10, r=10, t=20, b=60))
        fig.update_yaxes(range=[(lo - marge) / 1e6, (hi + marge) / 1e6])
        return T.theme_fig(fig, ui)


    @callback(Output(f"{px}-vl-contenu", "children"),
              Input(f"{px}-donnees", "data"), Input(f"{px}-vl", "data"), Input(f"{px}-valo", "date"),
              Input(f"{px}-courbe", "date"), Input(f"{px}-tol", "value"), Input(f"{px}-fg", "value"),
              Input(f"{px}-table-opcvm", "selected_row_ids"), Input(f"{px}-banque", "value"), Input("ui", "data"))
    def afficher_vl(donnees, vl_data, valo, courbe, tol, fg, internes, banque, ui):
        if not vl_data:
            return note("Les données VL (opérations, engagements, banque, charges) se chargent avec « Charger depuis "
                        "Manar » ; « Actualiser la VL » les relit à la date de valorisation choisie.")
        if not donnees:
            return html.Div("Données VL chargées, mais pas la composition du portefeuille : cliquez sur « Charger "
                            "depuis Manar » pour valoriser les titres.", className="alerte")
        if not (valo and courbe):
            return html.Div("Renseignez la date de valorisation et la date de la courbe.", className="alerte")
        try:
            return _contenu_vl(donnees, vl_data, valo, courbe, tol, fg, internes, banque, ui)
        except Exception as e:                                                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            return html.Div(className="alerte", children=[
                html.Strong("Calcul de la VL impossible : "), f"{type(e).__name__} : {e}",
                html.Details([html.Summary("Détail technique"),
                              html.Pre(traceback.format_exc()[-2500:], className="vl-trace")])])


    def _contenu_vl(donnees, vl_data, valo, courbe, tol, fg, internes, banque, ui):
        titres, opcvm, v, info_courbe = _calcul(donnees, valo, courbe, tol)
        V = P.calcul_vl(v, opcvm, _lire_vl(vl_data), date.fromisoformat(valo), fg, internes, banque)
        avert = None
        if v.empty and len(titres):
            avert = html.Div(f"Titres non valorisés ({info_courbe}) : la valo des actifs ne reprend que les OPCVM.",
                             className="alerte")
        if vl_data["date"] != valo:
            avert = html.Div(f"Données VL chargées pour le {fdate(vl_data['date'])}, date de valorisation : {fdate(valo)}. "
                             "Cliquez sur « Actualiser la VL ».", className="alerte")
        sans_fg = V["taux_fg"] in (None, "")
        rouge = lambda x: T.ROUGE if pd.notna(x) and abs(x) > 0.005 else None
        kpis = html.Div(className="kpi-grille", children=[
            kpi("Actif net calculé", millions(V["actif_net"]) if not sans_fg else "—",
                f"actif brut {millions(V['actif_brut'])}"),
            kpi("VL calculée", f"{V['vl']:,.2f}".replace(",", " ") if pd.notna(V["vl"]) else "—",
                f"{mad(V['parts'])} parts" if pd.notna(V["parts"]) else "parts Manar absentes"),
            kpi("Actif net Manar", millions(V["actif_net_manar"]) if pd.notna(V["actif_net_manar"]) else "—",
                f"écart {mad(V['ecart_an'], True)}" if pd.notna(V["ecart_an"]) else "ACTIF_NET_AV_VL", rouge(V["ecart_an"])),
            kpi("VL Manar", f"{V['vl_manar']:,.2f}".replace(",", " ") if pd.notna(V["vl_manar"]) else "—",
                f"écart {V['ecart_vl']:+,.4f}".replace(",", " ") if pd.notna(V["ecart_vl"]) else "actif net Manar / parts",
                rouge(V["ecart_vl"])),
        ])
        ops = V["operations"]
        details = []
        if len(ops):
            o = _dates_fr(ops.copy())
            o["EXCLUE"] = np.where(o["EXCLUE"].astype(bool), "Oui", "Non")
            details.append(html.Details(className="vl-details", children=[
                html.Summary(f"Opérations du jour : {V['n_operations']} ligne(s), dont {V['n_exclues']} FRGES- / "
                             "PROCDVM- exclue(s)"),
                dash_table.DataTable(data=o.astype(object).where(o.notna(), None).to_dict("records"),
                                     columns=[{"id": c, "name": c} for c in o.columns],
                                     style_data_conditional=[{"if": {"filter_query": '{EXCLUE} = "Oui"'},
                                                              "opacity": 0.45, "textDecoration": "line-through"}],
                                     **{k: v for k, v in style_table(False, 10).items() if k != "style_data_conditional"})]))
        for nom, cle in (("Opérations en cours (VALO_ENGAGEMENT)", "engagements"), ("Ligne Manar (HISTO_PORTEF_FP_GROUP)", "fp")):
            d = V[cle]
            if len(d):
                d = _dates_fr(d.copy())
                details.append(html.Details(className="vl-details", children=[
                    html.Summary(f"{nom} : {len(d)} ligne(s)"),
                    dash_table.DataTable(data=d.astype(object).where(d.notna(), None).to_dict("records"),
                                         columns=[{"id": c, "name": c} for c in d.columns], **style_table(False, 10))]))
        return html.Div([
            avert, kpis,
            html.Div(className="grille-2 vl-grille", children=[
                _table_vl(V), dcc.Graph(figure=_fig_vl(V, ui), config={"displayModeBar": False})]),
            note(f"Données Manar du {fdate(vl_data['date'])} (chargées le {vl_data['charge']}). Frais de gestion : base × "
                 f"taux annuel × jours depuis le dernier jour ouvré / 360. Lignes d'opérations FRGES- et PROCDVM- exclues."),
            *details,
        ])

    layout.children.append(signaux([f"{px}-toast-0", f"{px}-toast-1", f"{px}-toast-2"]))
    return layout
