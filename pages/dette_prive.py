"""
Dette privée — répertoire des émissions (Dette_Prive.xlsx) :
- Fiche titre : recherche par code (202943) ou ISIN, caractéristiques, dates de coupon, maturité initiale,
  ténors de la courbe et leur historique sur la semaine précédant l'émission ;
- Émetteur : suggestions des noms les plus proches, historique des émissions, échéancier des lignes
  actives, fréquence d'émission, ténors et formes préférés, dernières émissions.
La recherche par code renseigne l'émetteur ; l'émetteur peut aussi être cherché seul.
"""
from __future__ import annotations

import io
from datetime import date

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, ctx, dash_table, dcc, html, no_update
from dash.dash_table.Format import Format, Group, Scheme
from dash.exceptions import PreventUpdate
from plotly.subplots import make_subplots

import base_courbe as db
import dette_prive as D
import theme as T
from composants import carte, fdate, graphe, kpi, mise_en_page, note

dash.register_page(__name__, path="/dette-privee", name="Recherche", order=0, section="dette")

F0 = Format(precision=0, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ")
F2 = Format(precision=2, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ", decimal_delimiter=",")
F3 = Format(precision=3, scheme=Scheme.fixed, decimal_delimiter=",")
COULEURS = [T.NAVY, T.CIEL, T.MENTHE, "#3F7CC0", T.VERT, "#F07A86"]


# ---------------------------------------------------------------------------
# Petits éléments
# ---------------------------------------------------------------------------

def champ(lib, comp, classe=""):
    return html.Div(className=f"champ {classe}", children=[html.Label(lib), comp])


def ligne_info(lib, val, large=False):
    return html.Div(className="info-ligne" + (" large" if large else ""),
                    children=[html.Span(lib, className="info-lib"), html.Span(val if val not in (None, "") else "—",
                                                                                className="info-val info-libre")])


def mad(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:,.2f}".replace(",", " ")


def montant_lisible(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    if abs(v) >= 1e9:
        return f"{v / 1e9:,.2f} Mds MAD".replace(",", " ").replace(".", ",")
    return f"{v / 1e6:,.1f} M MAD".replace(",", " ").replace(".", ",")


def style_dp(textes: tuple, page: int = 10) -> dict:
    return dict(
        style_as_list_view=True, style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "transparent", "color": "var(--gris)", "fontWeight": 600, "fontSize": "0.6rem",
                      "letterSpacing": "0.1em", "textTransform": "uppercase", "border": "none",
                      "borderBottom": "1px solid var(--trait)", "padding": "0.5rem 0.55rem", "textAlign": "right",
                      "whiteSpace": "normal"},
        style_header_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in textes],
        style_cell={"backgroundColor": "transparent", "color": "var(--encre)", "border": "none",
                    "borderBottom": "1px solid var(--trait-doux)", "fontFamily": "inherit", "fontSize": "0.78rem",
                    "padding": "0.42rem 0.55rem", "textAlign": "right", "fontVariantNumeric": "tabular-nums"},
        style_cell_conditional=[*[{"if": {"column_id": c}, "textAlign": "left"} for c in textes],
                                {"if": {"column_id": "description"}, "maxWidth": "300px", "overflow": "hidden",
                                 "textOverflow": "ellipsis", "whiteSpace": "nowrap"},
                                {"if": {"column_id": "code"}, "fontWeight": 600, "color": "var(--navy)"}],
        style_data_conditional=[{"if": {"column_id": "total"}, "fontWeight": 700}],
        sort_action="native", page_action="native", page_size=page, cell_selectable=False,
    )


COL_TOMBEES = [
    {"id": "date_txt", "name": "Date"}, {"id": "code", "name": "Code"}, {"id": "description", "name": "Description"},
    {"id": "forme", "name": "Forme"},
    {"id": "coupon", "name": "Coupon (MAD)", "type": "numeric", "format": F2},
    {"id": "capital", "name": "Capital (MAD)", "type": "numeric", "format": F2},
    {"id": "total", "name": "Total (MAD)", "type": "numeric", "format": F2},
    {"id": "crd_avant", "name": "Capital restant dû avant (MAD)", "type": "numeric", "format": F2},
]
COL_ANNEES = [
    {"id": "annee", "name": "Année"},
    {"id": "coupon", "name": "Coupons (MAD)", "type": "numeric", "format": F2},
    {"id": "capital", "name": "Capital (MAD)", "type": "numeric", "format": F2},
    {"id": "total", "name": "Total (MAD)", "type": "numeric", "format": F2},
]
COL_DERNIERES = [
    {"id": "date_txt", "name": "Émission"}, {"id": "code", "name": "Code"}, {"id": "isin", "name": "ISIN"},
    {"id": "description", "name": "Description"}, {"id": "categorie", "name": "Type"},
    {"id": "forme", "name": "Forme"}, {"id": "maturite", "name": "Maturité"},
    {"id": "taux", "name": "Taux facial (%)", "type": "numeric", "format": F3},
    {"id": "montant", "name": "Montant (MAD)", "type": "numeric", "format": F0},
    {"id": "echeance_txt", "name": "Échéance"}, {"id": "frequence", "name": "Coupon"}, {"id": "statut", "name": "Statut"},
]
COL_REPART = [
    {"id": "libelle", "name": ""}, {"id": "nombre", "name": "Émissions", "type": "numeric", "format": F0},
    {"id": "part_nombre", "name": "Part (nb)", "type": "numeric", "format": Format(precision=1, scheme=Scheme.percentage)},
    {"id": "montant", "name": "Montant (MAD)", "type": "numeric", "format": F0},
    {"id": "part_montant", "name": "Part (montant)", "type": "numeric",
     "format": Format(precision=1, scheme=Scheme.percentage)},
]


# ---------------------------------------------------------------------------
# Mise en page
# ---------------------------------------------------------------------------

layout = html.Div(className="page", children=[
    dcc.Download(id="dp-dl"),
    html.Div(className="entete", children=[
        html.Div("Dette privée", className="titre-fin"),
        html.Div("Répertoire des émissions : recherche par code ou par émetteur", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),
    html.Div(className="carte barre dp-barre", children=[
        champ("Code ou ISIN", dcc.Input(id="dp-code", type="text", debounce=True, autoComplete="off",
                                         placeholder="202943 ou MA0002029439",
                                         className="input-date input-champ input-code")),
        champ("Émetteur recherché", dcc.Input(id="dp-saisie", type="text", debounce=True, autoComplete="off",
                                              placeholder="Nom approximatif : managem, attijari…",
                                              className="input-date input-champ"), "champ-large"),
        champ("Émetteur retenu (noms les plus proches)",
              dcc.Dropdown(id="dp-emetteur", options=[], value=None, clearable=True, searchable=True,
                           placeholder="Choisir un émetteur", maxHeight=320, optionHeight=34,
                           className="liste dp-dropdown"), "champ-large"),
        html.Button("Exporter", id="dp-export", className="btn btn-secondaire"),
    ]),
    html.Div(id="dp-info", className="note"),
    dcc.Tabs(id="dp-onglets", value="fiche", className="onglets", children=[
        dcc.Tab(label="Fiche titre", value="fiche", className="onglet", selected_className="onglet-actif",
                children=html.Div(id="dp-fiche", className="onglet-corps")),
        dcc.Tab(label="Émetteur", value="emetteur", className="onglet", selected_className="onglet-actif",
                children=html.Div(id="dp-vue-emetteur", className="onglet-corps")),
    ]),
])


# ---------------------------------------------------------------------------
# Suggestions d'émetteurs (saisie libre ou émetteur du code recherché)
# ---------------------------------------------------------------------------

def _options(liste, nb_par_emetteur) -> list[dict]:
    return [{"label": f"{nom}  ·  {nb_par_emetteur.get(nom, 0)} émission(s)", "value": nom} for nom in liste]


@callback(
    Output("dp-emetteur", "options"), Output("dp-emetteur", "value"), Output("dp-onglets", "value"),
    Output("dp-info", "children"),
    Input("dp-code", "value"), Input("dp-saisie", "value"), Input("dp-emetteur", "id"),
)
def suggerer(code, saisie, _montage):
    try:
        nb = D.emetteurs().set_index("emetteur")["n"].to_dict()
        info = D.info()
    except Exception as e:
        return [], None, no_update, html.Span(f"Base Dette privée illisible : {e}", className="alerte")
    declencheur = ctx.triggered_id
    if declencheur == "dp-code" and (code or "").strip():
        r = D.chercher_code(code)
        if r is None:
            return no_update, no_update, "fiche", info
        proches = [r["emetteur"]] + [n for n, _ in D.suggestions(r["emetteur"], 30) if n != r["emetteur"]]
        return _options(proches, nb), r["emetteur"], "fiche", info
    if declencheur == "dp-saisie" and (saisie or "").strip():
        proches = D.suggestions(saisie, 60)
        return _options([n for n, _ in proches], nb), proches[0][0] if proches else None, "emetteur", info
    tous = [n for n, _ in D.suggestions("", 400)]
    return _options(tous, nb), no_update, no_update, info


# ---------------------------------------------------------------------------
# Fiche titre
# ---------------------------------------------------------------------------

def _carte_fiche(f: dict) -> html.Div:
    base = (f"Oui — prime de risque {f['prime']:.0f} pb" if f["dans_base"] and f["prime"] is not None
            else "Oui — prime de risque non renseignée" if f["dans_base"] else "Non")
    titres = f"{f['nb_titres']:,.0f} titres".replace(",", " ") if pd.notna(f["nb_titres"]) else ""
    return html.Div(className="titre-carte", children=[
        html.Div(f["description"], className="titre-desc"),
        html.Div(className="info-grille info-grille-3", children=[
            ligne_info("Code", f["code"]), ligne_info("ISIN", f["isin"]),
            ligne_info("Type d'obligation", f["categorie"]),
            ligne_info("Type de paiement", f["paiement"]), ligne_info("Type de taux", f["type_taux"]),
            ligne_info("Taux facial", f"{f['taux']:.3f} %".replace(".", ",") if pd.notna(f["taux"]) else "—"),
            ligne_info("Montant de l'émission", f"{mad(f['montant'])} MAD ({titres})" if titres else mad(f["montant"])),
            ligne_info("Nominal", f"{mad(f['nominal'])} MAD"),
            ligne_info("Date d'émission", fdate(f["date_em"])),
            ligne_info("Date d'échéance", fdate(f["date_ech"])),
            ligne_info("Maturité initiale", f"{f['maturite']} ({f['maturite_jours']:.0f} jours)"
                       if pd.notna(f["maturite_jours"]) else "—"),
            ligne_info("Statut", f["statut"]),
            ligne_info("Fréquence des coupons", f["frequence"]),
            ligne_info("Fréquence d'amortissement", f["freq_amort"]),
            ligne_info("Garantie", f["garantie"] or "—"),
            ligne_info("Émetteur", f["emetteur"]),
            ligne_info("Teneur de registre (REGISTRAR)", f["registrar"]),
            ligne_info("Dans la base titres", base),
        ]),
    ])


def _carte_coupons(f: dict) -> html.Div:
    dates = f["coupons"]
    if not dates:
        corps = note("Aucune date de coupon renseignée dans la base (CouponPayDates vide).")
    else:
        auj = date.today()
        corps = html.Div(className="dp-coupons", children=[
            html.Span(d.strftime("%d/%m/%Y"), className="dp-date " + ("passee" if d <= auj else "future"))
            for d in dates])
    return carte("Dates de coupon", f"{len(dates)} date(s), ordonnées · {f['frequence']}", corps)


def _interpole(h: pd.DataFrame, tenors: list[str], jours: int) -> pd.Series | None:
    if len(tenors) != 2:
        return h[tenors[0]] if tenors else None
    a, b = tenors
    ja, jb = D.TENORS[a], D.TENORS[b]
    return h[a] + (h[b] - h[a]) * (jours - ja) / (jb - ja)


def _carte_tenors(f: dict, ui) -> html.Div:
    jours = f["maturite_jours"]
    if pd.isna(jours) or pd.isna(f["date_em"]):
        return carte("Maturité et courbe des taux", None, note("Dates d'émission ou d'échéance manquantes."))
    jours = int(jours)
    tenors, cols = f["tenors"], f["tenors_hist"]
    libs = " et ".join(D.LIB_TENORS[t] for t in tenors)
    expl = (f"Maturité pleine : ténor {libs}." if f["pleine"]
            else f"Maturité non standard : ténors encadrants {libs}." if len(tenors) == 2
            else f"Maturité hors de la grille : ténor le plus proche {libs}.")
    if f["revisable"]:
        expl += " Ligne révisable : historique du 52 semaines ajouté."
    with db.connexion() as conn:
        h = D.historique_tenors(conn, f["date_em"], cols)
    debut = (pd.Timestamp(f["date_em"]) - pd.Timedelta(days=6)).strftime("%d/%m/%Y")
    sous = f"Semaine précédant l'émission : du {debut} au {fdate(f['date_em'])}"
    if h.empty:
        return carte("Maturité et courbe des taux", sous, note(expl, "Pas de courbe dans l'historique sur cette "
                                                                   "période (historique à partir de 2004)."))
    interp = _interpole(h, tenors, jours) if not f["pleine"] else None
    fig = go.Figure()
    for i, t in enumerate(cols):
        fig.add_scatter(x=h["Date"], y=h[t], name=D.LIB_TENORS[t], mode="lines+markers",
                        line=dict(color=COULEURS[i % len(COULEURS)], width=2.4), marker=dict(size=7))
    if interp is not None:
        fig.add_scatter(x=h["Date"], y=interp, name=f"Interpolé {f['maturite']}", mode="lines+markers",
                        line=dict(color=T.GRIS, width=1.6, dash="dot"), marker=dict(size=5))
    mise_en_page(fig, 330, yaxis_title="Taux (%)", hovermode="x unified")
    fig.update_xaxes(tickformat="%d/%m", dtick=86400000)
    fig.update_traces(hovertemplate="%{y:.3f} %")

    tab = h.copy()
    if interp is not None:
        tab["interp"] = interp
    tab["Date"] = tab["Date"].dt.strftime("%d/%m/%Y")
    colonnes = [{"id": "Date", "name": "Date"}] + [
        {"id": t, "name": D.LIB_TENORS[t] + " (%)", "type": "numeric", "format": F3} for t in cols]
    if interp is not None:
        colonnes.append({"id": "interp", "name": f"Interpolé {f['maturite']} (%)", "type": "numeric", "format": F3})

    # Écart taux facial − courbe le jour de l'émission (révisable : marge sur le 52 semaines)
    ref = interp.iloc[-1] if interp is not None else h[tenors[0]].iloc[-1]
    if f["revisable"] and "52s" in h:
        ref_ecart, lib_ecart, sous_ecart = h["52s"].iloc[-1], "Écart taux facial − 52 semaines", "marge implicite (révisable)"
    else:
        ref_ecart, lib_ecart, sous_ecart = ref, "Écart taux facial − courbe", "prime implicite à l'émission"
    ecart = (float(f["taux"]) - float(ref_ecart)) * 100 if pd.notna(f["taux"]) and pd.notna(ref_ecart) else None
    facial = f"taux facial {f['taux']:.3f} %".replace(".", ",") if pd.notna(f["taux"]) else ""
    kpis = html.Div(className="kpi-grille", children=[
        kpi("Maturité initiale", f["maturite"], f"{jours} jours"),
        kpi("Ténor(s) de la courbe", libs, "pleine" if f["pleine"] else "encadrants"),
        kpi("Courbe à l'émission", f"{ref:.3f} %".replace(".", ",") if pd.notna(ref) else "—",
            f"au {h['Date'].iloc[-1]:%d/%m/%Y}" + (" (interpolé)" if interp is not None else "")),
        kpi(lib_ecart, f"{ecart:+.1f} pb".replace(".", ",") if ecart is not None else "—",
            " · ".join(x for x in (facial, sous_ecart) if x)),
    ])
    return carte("Maturité et courbe des taux", sous, kpis, note(expl),
                 dcc.Graph(figure=T.theme_fig(fig, ui), config={"displayModeBar": False}),
                 dash_table.DataTable(data=tab.to_dict("records"), columns=colonnes, **style_dp(("Date",), 10)))


@callback(Output("dp-fiche", "children"), Input("dp-code", "value"), Input("ui", "data"), Input("dp-fiche", "id"))
def afficher_fiche(code, ui, _montage):
    if not (code or "").strip():
        return note("Saisissez un code (ex. 202943) ou un ISIN (MA0002029439). Le code est cherché dans INSTRID : "
                    "MA, zéros de tête et clé de contrôle retirés (MA000 202943 9).",
                    "L'émetteur du titre est repris automatiquement dans l'onglet Émetteur.")
    try:
        f = D.fiche(code)
    except Exception as e:
        return html.Div(f"Base Dette privée illisible : {e}", className="alerte")
    if f is None:
        return html.Div(f"Le code {code.strip()} n'est pas dans la base Dette privée.", className="alerte")
    return html.Div(className="pile", children=[
        carte("Fiche du titre", f"{f['categorie']} · {f['forme']}", _carte_fiche(f)),
        html.Div(className="grille-large-gauche grille-2", children=[_carte_tenors(f, ui), _carte_coupons(f)]),
    ])


# ---------------------------------------------------------------------------
# Émetteur
# ---------------------------------------------------------------------------

def _fig_annees(pa: pd.DataFrame, ui):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=pa["annee"], y=pa["montant"] / 1e6, name="Montant émis (M MAD)", marker_color=T.NAVY,
                hovertemplate="%{y:,.0f} M MAD", secondary_y=False)
    fig.add_scatter(x=pa["annee"], y=pa["nombre"], name="Nombre d'émissions", mode="lines+markers",
                    line=dict(color=T.MENTHE, width=2.4), marker=dict(size=8), hovertemplate="%{y}",
                    secondary_y=True)
    mise_en_page(fig, 340)
    fig.update_yaxes(title_text="M MAD", secondary_y=False)
    pas = max(1, int(np.ceil(pa["nombre"].max() / 5))) if len(pa) else 1
    fig.update_yaxes(title_text="Émissions", secondary_y=True, showgrid=False, rangemode="tozero", dtick=pas,
                     tickformat="d")
    fig.update_xaxes(dtick=1)
    if len(pa) <= 3:
        fig.update_traces(width=0.35, selector=dict(type="bar"))
    return T.theme_fig(fig, ui)


def _fig_repart(rp: pd.DataFrame, ui, hauteur=300):
    fig = go.Figure(go.Bar(x=rp["nombre"], y=rp["libelle"], orientation="h", marker_color=T.CIEL,
                           customdata=rp["montant"] / 1e6, text=rp["nombre"], textposition="outside",
                           hovertemplate="%{x} émission(s) · %{customdata:,.0f} M MAD<extra></extra>"))
    mise_en_page(fig, hauteur, hovermode="closest", showlegend=False, bargap=0.35)
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(tickformat="d", rangemode="tozero")
    if len(rp) <= 3:
        fig.update_traces(width=0.45)
    return T.theme_fig(fig, ui)


def _fig_tombees(tb: pd.DataFrame, ui):
    m = tb.assign(mois=pd.to_datetime(tb["date"]).dt.to_period("M").dt.to_timestamp()) \
          .groupby("mois")[["coupon", "capital"]].sum().reset_index()
    fig = go.Figure()
    fig.add_bar(x=m["mois"], y=m["coupon"] / 1e6, name="Coupons", marker_color=T.MENTHE, hovertemplate="%{y:,.1f} M")
    fig.add_bar(x=m["mois"], y=m["capital"] / 1e6, name="Capital", marker_color=T.NAVY, hovertemplate="%{y:,.1f} M")
    mise_en_page(fig, 320, barmode="stack", yaxis_title="M MAD")
    fig.update_xaxes(tickformat="%m/%Y")
    return T.theme_fig(fig, ui)


@callback(Output("dp-vue-emetteur", "children"), Input("dp-emetteur", "value"), Input("ui", "data"),
          Input("dp-vue-emetteur", "id"))
def afficher_emetteur(emetteur, ui, _montage):
    if not emetteur:
        return note("Cherchez un émetteur (nom approximatif accepté) ou un code dans la barre du haut : "
                    "la liste propose les noms les plus proches, à faire défiler.")
    e = D.emissions(emetteur)
    if e.empty:
        return html.Div(f"Aucune émission pour {emetteur}.", className="alerte")
    st = D.statistiques(e)
    act = D.actives(e)
    tb = D.echeancier_emetteur(e)
    enc = float(tb["capital"].sum()) if len(tb) else 0.0
    prochaine = tb.iloc[0] if len(tb) else None
    pa = D.par_annee(e)

    kpis = html.Div(className="kpi-grille", children=[
        kpi("Émissions", f"{st['nombre']}", f"{st['jours_emission']} dates d'émission"),
        kpi("Montant total émis", montant_lisible(st["montant"]), f"{st['annees_actives']} année(s) avec émission"),
        kpi("Lignes actives", f"{len(act)}", f"encours approché {montant_lisible(enc)}"),
        kpi("Première / dernière", f"{fdate(st['premiere'])}", f"dernière : {fdate(st['derniere'])}"),
        kpi("Fréquence d'émission",
            f"tous les {st['ecart_median']:.0f} j" if st["ecart_median"] else "—",
            ("médiane · " if st["ecart_median"] else "une seule date d'émission · ")
            + (f"{st['par_an']:.1f} / an en moyenne · {st['par_an_3ans']:.1f} / an sur 3 ans".replace(".", ",")
               if st["par_an"] else "")),
        kpi("Prochaine tombée", fdate(prochaine["date"]) if prochaine is not None else "—",
            f"{montant_lisible(tb[tb['date'] == prochaine['date']]['total'].sum())}" if prochaine is not None else ""),
    ])

    # Répartitions
    rm = D.repartition(e, "maturite").rename(columns={"maturite": "libelle"})
    rm = rm.assign(o=rm["libelle"].map(D.ordre_maturite)).sort_values(["nombre", "o"], ascending=[False, True])
    rf = D.repartition(e, "forme").rename(columns={"forme": "libelle"})
    rc = D.repartition(e, "categorie").rename(columns={"categorie": "libelle"})
    top_t = rm.iloc[0] if len(rm) else None
    top_f = rf.iloc[0] if len(rf) else None
    resume = note(
        f"Ténor le plus émis : {top_t['libelle']} ({top_t['nombre']} émissions, {top_t['part_nombre']:.0%} du nombre, "
        f"{montant_lisible(top_t['montant'])})." if top_t is not None else "",
        f"Forme la plus émise : {top_f['libelle']} ({top_f['nombre']} émissions, {top_f['part_nombre']:.0%})."
        if top_f is not None else "")

    # Échéancier
    if len(tb):
        tb_aff = tb.assign(date_txt=pd.to_datetime(tb["date"]).dt.strftime("%d/%m/%Y"))
        an = tb.assign(annee=pd.to_datetime(tb["date"]).dt.year.astype(str)).groupby("annee")[
            ["coupon", "capital", "total"]].sum().reset_index()
        an = pd.concat([an, pd.DataFrame([{"annee": "Total", **an[["coupon", "capital", "total"]].sum().to_dict()}])])
        bloc_ech = [
            dcc.Graph(figure=_fig_tombees(tb, ui), config={"displayModeBar": False}),
            html.Div(className="dp-annees", children=dash_table.DataTable(
                data=an.to_dict("records"), columns=COL_ANNEES, **style_dp(("annee",), 15))),
            dash_table.DataTable(data=tb_aff.to_dict("records"), columns=COL_TOMBEES,
                                 **style_dp(("date_txt", "code", "description", "forme"), 12)),
            note("Montants pour toute l'émission (nombre de titres × nominal). Dates : CouponPayDates, ou "
                 "reconstituées depuis l'échéance selon la fréquence. Amortissables : amortissement linéaire "
                 "approché sur toutes les dates ; révisables : dernier taux connu ; zéro coupon : nominal à l'échéance."),
        ]
    else:
        bloc_ech = [note("Aucune ligne active : pas de tombée à venir.")]

    dern = e.sort_values("date_em", ascending=False).head(5).assign(
        date_txt=lambda x: x["date_em"].dt.strftime("%d/%m/%Y"),
        echeance_txt=lambda x: x["date_ech"].dt.strftime("%d/%m/%Y"))
    cols_d = [c["id"] for c in COL_DERNIERES]

    return html.Div(className="pile", children=[
        carte(emetteur, f"{st['nombre']} émission(s) · teneur(s) de registre : "
                        f"{', '.join(e['registrar'].value_counts().index[:3])}", kpis),
        html.Div(className="grille-2", children=[
            carte("Historique des émissions", "par année : nombre et montant global",
                  dcc.Graph(figure=_fig_annees(pa, ui), config={"displayModeBar": False})),
            carte("Ténors émis", "maturité initiale · nombre d'émissions (montant au survol)",
                  dcc.Graph(figure=_fig_repart(rm.head(12), ui, 340), config={"displayModeBar": False})),
        ]),
        html.Div(className="grille-2", children=[
            carte("Formes émises", "paiement et type de taux", resume,
                  dash_table.DataTable(data=rf.to_dict("records"), columns=COL_REPART, **style_dp(("libelle",), 10))),
            carte("Types d'obligation", None,
                  dash_table.DataTable(data=rc.to_dict("records"), columns=COL_REPART, **style_dp(("libelle",), 10))),
        ]),
        carte("Échéancier des lignes actives", f"{len(act)} ligne(s) · tombées de coupons et de capital", *bloc_ech),
        carte("5 dernières émissions", None,
              dash_table.DataTable(data=dern[cols_d].to_dict("records"), columns=COL_DERNIERES,
                                   **style_dp(("date_txt", "code", "isin", "description", "categorie", "forme",
                                               "maturite", "echeance_txt", "frequence", "statut"), 5))),
    ])


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@callback(Output("dp-dl", "data"), Input("dp-export", "n_clicks"),
          State("dp-code", "value"), State("dp-emetteur", "value"), prevent_initial_call=True)
def exporter(n, code, emetteur):
    if not n:
        raise PreventUpdate
    feuilles = {}
    f = D.fiche(code) if (code or "").strip() else None
    if f:
        champs = ["code", "isin", "categorie", "description", "paiement", "type_taux", "taux", "montant", "nb_titres",
                  "nominal", "date_em", "date_ech", "maturite", "maturite_jours", "frequence", "freq_amort",
                  "emetteur", "registrar", "statut", "garantie"]
        fiche = pd.DataFrame({"Champ": champs, "Valeur": [f[c] for c in champs]})
        fiche.loc[len(fiche)] = ["dates_coupon", " ; ".join(d.strftime("%d/%m/%Y") for d in f["coupons"])]
        fiche.loc[len(fiche)] = ["base_titres", f"Oui, prime {f['prime']} pb" if f["dans_base"] else "Non"]
        feuilles["Fiche"] = fiche
        with db.connexion() as conn:
            feuilles["Historique ténors"] = D.historique_tenors(conn, f["date_em"], f["tenors_hist"])
    if emetteur:
        e = D.emissions(emetteur)
        feuilles["Émissions"] = e.drop(columns=["coupons", "emetteur_norm", "desc_norm"], errors="ignore")
        feuilles["Par année"] = D.par_annee(e)
        feuilles["Échéancier actives"] = D.echeancier_emetteur(e)
        feuilles["Ténors"] = D.repartition(e, "maturite")
        feuilles["Formes"] = D.repartition(e, "forme")
    if not feuilles:
        raise PreventUpdate
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for nom, df in feuilles.items():
            df.to_excel(xw, sheet_name=nom[:31], index=False)
    nom = "_".join(x for x in ("dette_privee", (code or "").strip(), (emetteur or "").replace(" ", "_")) if x)
    return dcc.send_bytes(buf.getvalue(), f"{nom}.xlsx")
