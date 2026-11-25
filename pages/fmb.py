"""Portefeuille — FMB : composition depuis Manar (portefeuille = 'FMB'), revalorisation et analyses."""

import dash

import vue_portefeuille as V

dash.register_page(__name__, path="/portefeuille/fmb", name="FMB", order=7, section="portefeuille")

layout = V.construire("FMB")
