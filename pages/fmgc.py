"""Portefeuille — FMGC : composition depuis Manar (portefeuille = 'FMGC'), revalorisation et analyses."""

import dash

import vue_portefeuille as V

dash.register_page(__name__, path="/portefeuille/fmgc", name="FMGC", order=0, section="portefeuille")

layout = V.construire("FMGC")
