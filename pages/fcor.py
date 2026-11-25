"""Portefeuille — FCOR : composition depuis Manar (portefeuille = 'FCOR'), revalorisation et analyses."""

import dash

import vue_portefeuille as V

dash.register_page(__name__, path="/portefeuille/fcor", name="FCOR", order=6, section="portefeuille")

layout = V.construire("FCOR")
