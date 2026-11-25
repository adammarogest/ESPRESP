"""
Espresso — tableau de bord taux, titres, portefeuilles et OPCVM (Dash). Créé par EL KAMLI Adam.

Lancer depuis le dossier qui contient COURBE.py et histo_courbe.db :
    python app.py
puis ouvrir http://127.0.0.1:8050 (le navigateur s'ouvre automatiquement).
"""

from __future__ import annotations

import threading
import webbrowser
from datetime import date

import dash
from dash.exceptions import PreventUpdate
from dash import ALL, Dash, Input, Output, Patch, State, callback, clientside_callback, dcc, html, no_update

import base_courbe as db
from composants import rendu_toast, signaux, toast

# --- Base SQLite : création des tables ; taux directeur importé une fois s'il manque -------
with db.connexion() as conn:
    db.initialiser(conn)
    vide_td = conn.execute("SELECT COUNT(*) FROM taux_directeur").fetchone()[0] == 0
    if vide_td and db.TD_EXCEL_PATH.exists():
        n = db.enregistrer_taux_directeur(conn, db.lire_excel_taux_directeur())
        print(f"Taux directeur : {n} décisions importées depuis {db.TD_EXCEL_PATH.name}.")

app = Dash(
    __name__,
    use_pages=True,
    title="Espresso",
    update_title=None,
    suppress_callback_exceptions=True,
    external_stylesheets=[
        "https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap",
    ],
)


def barre_laterale() -> html.Aside:
    return html.Aside(className="sidebar", children=[
        html.Div(className="marque", children=[
            html.Div("Espresso", className="marque-fin marque-nom"),
            html.Div("Taux · Titres · OPCVM", className="marque-gras"),
            html.Div(className="marque-trait"),
        ]),
        html.Nav(id="nav", className="nav"),

        html.Div(className="side-bloc", children=[
            html.Div("Base historique", className="side-titre"),
            html.Div(id="base-stats", className="side-texte"),
        ]),

        html.Div(className="side-bloc", children=[
            html.Div("Export Excel", className="side-titre"),
            html.Div(className="side-dates", children=[
                html.Label("Du", htmlFor="export-debut"),
                dcc.Input(id="export-debut", type="date", className="input-date sombre"),
                html.Label("Au", htmlFor="export-fin"),
                dcc.Input(id="export-fin", type="date", className="input-date sombre"),
            ]),
            html.Button("Exporter la période", id="btn-export", className="btn btn-clair"),
            dcc.Download(id="dl-export"),
        ]),

        html.Div(className="signature", children=[
            html.Span("Créé par", className="signature-lib"), html.Span("EL KAMLI Adam", className="signature-nom"),
        ]),
    ])


app.layout = html.Div(className="shell", children=[
    dcc.Location(id="url"),
    dcc.Store(id="maj", data=0),           # horodatage de la dernière écriture en base (courbe)
    dcc.Store(id="toast-vu", data=0),      # horodatage de la dernière notification affichée
    signaux(["app-toast-0"]),
    dcc.Store(id="pages-titres", data={p["relative_path"]: p["name"] for p in dash.page_registry.values()}),
    # Préférences d'affichage, mémorisées dans le navigateur (menu ouvert, mode nuit)
    dcc.Store(id="ui", storage_type="local", data={"menu": True, "nuit": False}),
    html.Div(id="ui-effet", style={"display": "none"}),
    html.Div(id="toast-zone", className="toast-zone"),
    barre_laterale(),
    html.Main(className="contenu", children=[
        html.Div(className="barre-outils", children=[
            html.Button("☰", id="btn-menu", className="outil", title="Afficher / masquer le menu"),
            html.Button(id="btn-theme", className="outil outil-theme", title="Mode jour / nuit"),
        ]),
        # Pages « figées » : chaque page est construite à sa première visite puis conservée (masquée
        # quand on en ouvre une autre) ; elle retrouve exactement son dernier état.
        dcc.Store(id="pages-montees", data=[]),
        html.Div(id="pages", className="pages", children=[]),
        html.Div(id="pages-effet", style={"display": "none"}),
        html.Footer(className="pied", children=["Espresso · créé par ", html.Strong("EL KAMLI Adam")]),
    ]),
])


def _page(chemin: str):
    pages = {p["relative_path"]: p for p in dash.page_registry.values()}
    return pages.get(chemin) or pages.get("/") or next(iter(pages.values()))


@callback(Output("pages", "children"), Output("pages-montees", "data"),
          Input("url", "pathname"), State("pages-montees", "data"))
def monter_page(chemin, montees):
    """Ajoute la page demandée la première fois ; les pages déjà construites ne sont pas touchées."""
    page = _page(chemin or "/")
    cle = page["relative_path"]
    montees = montees or []
    if cle in montees:
        return no_update, no_update
    contenu = page["layout"]() if callable(page["layout"]) else page["layout"]
    p = Patch()
    p.append(html.Div(contenu, id={"type": "page", "chemin": cle}, className="page-conteneur"))
    return p, montees + [cle]


AFFICHER_PAGE = """
    function(chemin, titres) {
        chemin = chemin || "/";
        if (!(titres || {})[chemin]) { chemin = "/"; }
        document.querySelectorAll(".page-conteneur").forEach(function(e) {
            var id = JSON.parse(e.id);
            e.style.display = (id.chemin === chemin) ? "" : "none";
        });
        document.title = ((titres || {})[chemin] || "") + " · Espresso";
        setTimeout(function() { window.dispatchEvent(new Event("resize")); }, 60);
        return chemin;
    }
"""
# Affichage immédiat au changement d'adresse (ne dépend pas du serveur : instantané pour une page déjà construite)
clientside_callback(
    AFFICHER_PAGE.replace("function(chemin, titres) {", "function(chemin, titres) { window.scrollTo(0, 0);"),
    Output("pages-effet", "children"), Input("url", "pathname"), State("pages-titres", "data"),
)
# … et une fois une nouvelle page ajoutée (première visite)
clientside_callback(
    AFFICHER_PAGE.replace("function(chemin, titres) {", "function(montees, chemin, titres) {"),
    Output("pages-effet", "title"), Input("pages-montees", "data"), State("url", "pathname"), State("pages-titres", "data"),
)


# --- Affichage : menu repliable et mode nuit (côté navigateur, sans aller-retour serveur) ---

clientside_callback(
    """
    function(nMenu, nTheme, ui) {
        ui = Object.assign({menu: true, nuit: false}, ui || {});
        const id = (dash_clientside.callback_context.triggered_id || "");
        if (id === "btn-menu") { ui.menu = !ui.menu; }
        if (id === "btn-theme") { ui.nuit = !ui.nuit; }
        return ui;
    }
    """,
    Output("ui", "data"),
    Input("btn-menu", "n_clicks"), Input("btn-theme", "n_clicks"), State("ui", "data"),
    prevent_initial_call=True,
)

clientside_callback(
    """
    function(ui) {
        ui = Object.assign({menu: true, nuit: false}, ui || {});
        const html = document.documentElement;
        html.dataset.menu = ui.menu ? "ouvert" : "ferme";
        html.dataset.theme = ui.nuit ? "nuit" : "jour";
        // Les graphiques se redimensionnent quand la largeur du contenu change
        setTimeout(function() { window.dispatchEvent(new Event("resize")); }, 320);
        return ui.nuit ? "☀ Jour" : "☾ Nuit";
    }
    """,
    Output("btn-theme", "children"),
    Input("ui", "data"),
)


# --- Navigation (les prochaines fenêtres s'y ajoutent automatiquement) ---------------------

SECTIONS = [("courbe", "Analyse de la courbe des taux"), ("titre", "Analyse de titre"), ("portefeuille", "Portefeuille"),
            ("dette", "Dette privée"),
            ("performance", "Performance")]


@callback(Output("nav", "children"), Input("url", "pathname"))
def navigation(chemin):
    groupes = []
    for code, titre in SECTIONS:
        pages = sorted((p for p in dash.page_registry.values() if p.get("section", "courbe") == code),
                       key=lambda p: p.get("order", 0))
        if not pages:
            continue
        liens = [dcc.Link(p["name"], href=p["relative_path"],
                          className="nav-lien actif" if p["relative_path"] == chemin else "nav-lien")
                 for p in pages]
        groupes.append(html.Details(open=True, className="nav-groupe", children=[
            html.Summary(titre, className="nav-titre"), html.Div(liens, className="nav-liens")]))
    return groupes


# --- Base : statistiques, import, export ----------------------------------------------------

@callback(Output("base-stats", "children"), Output("export-debut", "value"),
          Output("export-fin", "value"), Input("maj", "data"),
          State("export-debut", "value"), State("export-fin", "value"))
def stats_base(_, debut, fin):
    with db.connexion() as conn:
        s = db.statistiques(conn)
        td = db.taux_directeur_en_vigueur(conn)
    if not s["nb_dates"]:
        return "Base vide.", no_update, no_update
    texte = [html.Strong(f"{s['nb_dates']} dates"), html.Br(),
             f"du {s['premiere']:%d/%m/%Y} au {s['derniere']:%d/%m/%Y}"]
    if td:
        texte += [html.Div(className="side-td", children=[
            html.Span("Taux directeur"), html.Strong(f"{td[1]:.2f}%"),
            html.Span(f"décision du {td[0]:%d/%m/%Y}", className="side-td-date")])]
    if debut and fin:
        return texte, no_update, no_update
    return texte, date(s["derniere"].year, 1, 1).isoformat(), s["derniere"].date().isoformat()


@callback(Output("dl-export", "data"),
          Output({"type": "sig-toast", "id": "app-toast-0"}, "data"),
          Input("btn-export", "n_clicks"),
          State("export-debut", "value"), State("export-fin", "value"), prevent_initial_call=True)
def exporter(n_clicks, debut, fin):
    if not n_clicks:                     # pas de clic réel (insertion de la page)
        raise PreventUpdate
    if not debut or not fin or debut > fin:
        return no_update, toast("Choisissez une période valide.", "erreur")
    with db.connexion() as conn:
        df = db.lire_historique(conn, debut, fin)
    if df.empty:
        return no_update, toast("Aucune donnée sur cette période.", "erreur")
    nom = f"courbe_bdt_{debut.replace('-', '')}_{fin.replace('-', '')}.xlsx"
    return dcc.send_bytes(db.exporter_excel(df), nom), toast(f"{len(df)} date(s) exportée(s).")


# --- Collecteurs : les pages émettent dans leurs propres stores (signaux), seuls ces callbacks écrivent
#     dans les composants communs (évite que Dash relance les callbacks des pages déjà montées).

@callback(Output("toast-zone", "children"), Output("toast-vu", "data"),
          Input({"type": "sig-toast", "id": ALL}, "data"), State("toast-vu", "data"))
def collecter_toasts(signaux_toast, vu):
    recents = [x for x in signaux_toast if x and x.get("t", 0) > (vu or 0)]
    if not recents:
        return no_update, no_update
    dernier = max(recents, key=lambda x: x["t"])
    return rendu_toast(dernier), dernier["t"]


def _collecteur(canal: str, sortie: str):
    @callback(Output(sortie, "data"), Input({"type": f"sig-{canal}", "id": ALL}, "data"), State(sortie, "data"))
    def collecter(valeurs, actuel):
        t = max([v for v in valeurs if isinstance(v, (int, float))] or [0])
        return t if t > (actuel or 0) else no_update
    return collecter


_collecteur("maj", "maj")


if __name__ == "__main__":
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8050")).start()
    app.run(debug=False, port=8050)
