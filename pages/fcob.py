"""Portefeuille — FCOB : composition depuis Manar (portefeuille = 'FCOB'), revalorisation et analyses."""

import dash

import vue_portefeuille as V

dash.register_page(__name__, path="/portefeuille/fcob", name="FCOB", order=3, section="portefeuille")

layout = V.construire("FCOB")
