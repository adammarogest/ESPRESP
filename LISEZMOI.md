# Espresso — tableau de bord taux, titres, portefeuilles et OPCVM (Dash)

Créé par EL KAMLI Adam.

## Charte visuelle
Mode jour « Porcelaine » (fond porcelaine, encre torréfiée, pétrole pour les données, touche crema)
et mode nuit « Ristretto » (brun espresso, texte crème, données en crema dorée), bouton Jour / Nuit
en haut de chaque page. Police : Manrope (chiffres tabulaires dans les tableaux). Couleurs des
graphiques : theme.py ; feuille de style : assets/style.css (section « CHARTE ESPRESSO »).

## Lancement
Depuis ce dossier :

    pip install -r requirements.txt
    python app.py

Le navigateur s'ouvre sur http://127.0.0.1:8050 (arrêt : Ctrl+C dans le terminal).

## Données
Tout est dans histo_courbe.db (SQLite) :
- tenors          historique des ténors (migré depuis histo.xlsx, qui n'est plus utilisé)
- courbe_bkam     points de marché bruts de chaque courbe téléchargée
- taux_directeur  décisions de Bank Al-Maghrib (taux, réserve obligatoire, rémunération)

Nouvelle décision de taux directeur : ajouter une ligne, par exemple en Python :
    import base_courbe as db, pandas as pd
    with db.connexion() as c:
        db.enregistrer_taux_directeur(c, pd.DataFrame([{"date": "2026-12-17", "taux": 2.25}]))

## Fenêtres
Le menu est organisé en deux sections.

### Analyse de la courbe des taux
- Courbe des taux    téléchargement BKAM, variations J-1 / hebdo / YTD, graphique
- Étude statistique  statistiques annualisées, graphiques, tests de marche aléatoire, ACP,
                     cas aberrants, résumé annuel ; sur toute la base ou une période ; export Excel
- Prévision & valeur relative
                     courbes attendues (pentes inter-ténors, pente vs taux directeur, moyenne des
                     variations) sur 1A/3A/5A/10A/Tout/fenêtre perso, backtest depuis le début d'année ;
                     prévision Nelson-Siegel ; sensibilités, KRD, rendement attendu (carry, glissement,
                     effet courbe), points morts et classement des ténors ; export Excel

- Stress test        courbe BKAM d'une date, modifiable point par point (ou choc court/long) avec
                     impact immédiat sur les ténors ; modifier un ténor ajuste la courbe pour le
                     redonner (points encadrants, point le plus proche, translation, insertion),
                     selon les règles de taux_courbe ; export Excel
- Taux forward       courbe zéro-coupon (logique ZCP) et forward 1 an ; courbe des ténors attendue
                     dans 3 mois … 10 ans ; courbe implicite à l'horizon (1 mois, 3 mois, 6 mois, 1 an)
                     comparée aux prévisions pentes historiques et Nelson-Siegel ; backtest depuis le
                     début de l'année ; forward vs réalisé ; prime de terme ex post ; export Excel
- Pentification      question : « la courbe présente une pentification au-dessus de la moyenne,
                     comment l'exploiter ? ». Réglages communs : date, pente de référence (10a − 52s,
                     10a − 2a, 30a − 13s, 2a − 13s, 5a − 2a, 30a − 10a), seuil de courbe pentue
                     (moyenne, 60e ou 75e centile), historique (tout, 10 ans, 5 ans).
                     Onglet Pente actuelle : position de la pente dans l'historique (z-score, centile),
                     concentration par segment (13s → 52s … 20a → 30a : écart à la moyenne, pb par
                     année, part de l'excès), variation depuis le 1er janvier et son mécanisme, facteurs
                     Nelson-Siegel, 13s − taux directeur, analogues historiques (formes de courbe les
                     plus proches) et ce qui a suivi à 3, 6 et 12 mois.
                     Onglet Réaction après conseil : chaque conseil de BAM (table des taux directeurs)
                     classé hausse / statu quo / baisse et courbe pentue ou non la veille ; variations
                     du court, du long et de la pente à 1, 3, 6 mois et 1 an ; mécanisme (aplatissement
                     par le court ↑ ou le long ↓, pentification par le long ↑ ou le court ↓) ;
                     trajectoire moyenne ; nuage court / long ; liste des conseils.
                     Onglet Positionnement : lignes BDT au choix (201949, 201808, 201646, 201409 par
                     défaut) valorisées à J+1 puis à l'horizon sous 7 scénarios (stable, court +choc,
                     long +choc, historique post-conseil statu quo ou hausse, pentes historiques AR(1),
                     Nelson-Siegel, forward implicite) ; rendement décomposé en portage (TRI),
                     glissement et effet scénario ; seuils de rentabilité vs 0 et vs 13 semaines.
                     Onglet Synthèse : conclusions rédigées automatiquement. Export Excel.

### Analyse de titre
- Pricer             fiche d'un BDT (in fine, révisable, amortissable annuel/semestriel/trimestriel,
                     linéaire ou constant, prime de risque) valorisé sur la courbe d'une date ;
                     trois taux : courbe BKAM interpolée, ténors encadrants (modifiables dans le
                     tableau des ténors pour la négociation), taux négocié saisi ;
                     prix, coupon couru, PPC, TRI, sensibilité, convexité, duration, DV01,
                     rendement courant, point mort, prime/décote ; échéancier, chocs de taux ;
                     export Excel. Révisable = in fine échéant à la prochaine date de révision.
                     Code titre : un code de base_titre.xlsx remplit la fiche (et affiche ISIN, classe,
                     émetteur, détention…) ; un code inconnu = nouvelle ligne à saisir.
                     Dates au format JJ/MM/AAAA.
                     Délai de grâce (facultatif, années) pour un amortissable : aucun amortissement
                     jusqu'à jouissance + délai, puis linéaire ou constant sur les échéances suivantes.
- Swap               ligne 1 (code + quantité) à céder ; toutes les lignes vivantes de la base
                     valorisées sur la courbe (prix plein, sensibilité) :
                     · Montant  : code de la ligne 2 -> Q2 = Q1 × P1 / P2
                     · Sensi    : lignes de sensibilité proche (écart max), filtres émetteur, type de
                                  ligne, catégorie, spread -> Q2 = Q1 × P1 × S1 / (P2 × S2)
                     · Émetteur : lignes du même émetteur dans un intervalle d'échéance, quantités
                                  en montant et en sensi
                     quantités arrondies à l'unité, écarts résiduels de montant et de DV01 ; export Excel
- Contrôle de valo.  dépôt d'un export de valorisation (.xls/.xlsx : TITRE, VALORISATION,
                     SENSIBILITÉ, DURATION…) ; chaque titre est valorisé depuis base_titre.xlsx
                     (courbe = date de référence du fichier, valorisation = J+1 ouvré par défaut)
                     et comparé : résumé, statut par catégorie, écarts en pb, lignes mal pricées
                     avec cause probable, détail du calcul et échéancier, export Excel.
                     Lecture des .xls : pip install xlrd

### Portefeuille
- FMGC, FCM, FML, FCOB, FMS, FCO, FCOR, FMB : même fenêtre (vue_portefeuille.py), seul le code portefeuille de la
                     requête change.
                     Composition du portefeuille lue dans Manar (Oracle 192.168.7.80:1521/MANARPRO)
                     avec utilisateur, mot de passe et date du portefeuille (non enregistrés) ;
                     lignes sans dates d'émission, de jouissance et d'échéance = OPCVM externes ;
                     autres titres retrouvés par CODE_ISIN dans base_titre.xlsx, valorisés
                     (prix plein × QUANTITE = TOTAL_VALO) et comparés à VALORISATION ;
                     sensibilité et duration du portefeuille ; répartition par maturité résiduelle
                     (échéance retenue = révision, ou échéance finale) avec prime moyenne et part de
                     la prime par tranche ; prime moyenne et hors État ; répartition par émetteur ;
                     export Excel.
                     Règle : MA0000094351 (9435) toujours valorisée à 0 (portefeuilles, contrôle, swap).
                     Coupons imposés : 153159 = 2 780 MAD par coupon (demande de l'émetteur) ;
                     liste COUPONS_FIXES dans base_titre.py. Délais de grâce absents de la base :
                     9431 = 2 ans ; liste GRACES dans base_titre.py.
                     Connexion : pip install oracledb (mode thin, sans client Oracle)

- Calcul de la VL (à la date de valorisation, données Manar lues avec « Charger depuis Manar » ou
  « Actualiser la VL ») :
  - opérations en cours = Σ MONTANT_CV de tp.valo_engagement (date_valo, portefeuille) ;
  - position espèce (tp.histo_operation, entité = portefeuille, lignes FRGES- et PROCDVM- exclues) =
    Σ MONTANT_2 des opérations en valeur J, en négatif pour les postes PENSRO, PENSRT, ABDTS, ABDTP, ATCNP,
    ATCNS, AOBLS, AOBLP (positif pour les autres)
    + Σ MONTANT_4 des opérations à échéance J, en négatif pour les postes PENSDOB, PENSRTOB ;
    le poste est repéré dans une colonne texte de l'opération (valeur « PENSRO » ou « PENSRO-… ») ;
    listes POSTES_NEG_VALEUR et POSTES_NEG_ECHEANCE dans portefeuille.py ;
  - tp.histo_portef_fp_group : banque J-1 = BANQUE_DEB_JOUR, charges en cours = ESP_CHANGES_ENCOURS +
    CUMUL_PROVISIONS_CHARGES, agios = CUMUL_PROVISIONS_AGIOS, actif net Manar = ACTIF_NET_AV_VL,
    nombre de parts = NOMBRE_PARTS_AV_VL ;
  - banque actif = banque J-1 + position espèce, ou montant saisi dans « Banque actif (saisie manuelle) »
    (la valeur calculée et l'écart restent affichés ; champ vide = calcul) ; espèce actif = banque actif − charges − agios ;
    valo des actifs = total valo des titres valorisés + valorisation des OPCVM (titres non valorisés non repris) ;
    actif brut = espèce actif + valo des actifs + opérations en cours ;
    base FG = actif brut − OPCVM internes (FCM, FMGC, FML, FCOB, FMS, FCO, FCOR, FMO, FMB, FMSR, FCA, FSO,
    FMA, FIF, SMC, FMI, FMC, FTS : cochés d'office dans la table des OPCVM, modifiables) ;
    frais de gestion = base × taux annuel saisi × (J − dernier jour ouvré) / 360 ;
    actif net = actif brut − frais ; VL = actif net / nombre de parts ; écarts avec l'actif net et la VL Manar.
  - Le taux de frais de gestion est mémorisé par portefeuille dans le navigateur ; l'export ajoute les
    feuilles Calcul VL, Opérations du jour, Opérations en cours et HISTO_PORTEF_FP_GROUP.
### Trésorerie (retirée)
La fenêtre Trésorerie n'est plus affichée. Son code est conservé (archives/treso_global.py, tresorerie.py,
vue_tresorerie.py) ainsi que la base tresorerie.db ; pour la rétablir, recopier archives/treso_global.py
dans pages/ et rajouter ("tresorerie", "Trésorerie") à SECTIONS dans app.py. Les fichiers
pages/treso_*.py sont vides et peuvent être supprimés.

### Dette privée › Recherche (base Dette_Prive.xlsx, feuille Debts, dans le dossier de l'application)
Le fichier est relu dès qu'il est modifié. Deux onglets, alimentés par la même barre de recherche :
- Code ou ISIN : 202943 est cherché dans INSTRID (MA000 202943 9 : MA, zéros de tête et clé de
  contrôle retirés) ; l'ISIN complet est aussi accepté.
  Fiche : type d'obligation (OBL_ORDN ordinaire, OBL_CONV convertible, OBL_SUBD subordonnée, FPCT,
  BDT, TCN), description, paiement (ZCBD / AMBD = amortissable, sinon in fine), montant (nombre de
  titres × nominal), dates d'émission et d'échéance, nominal, type de taux, émetteur, dates de
  coupon ordonnées et fréquence déduite, présence dans la base titres et prime de risque.
  Maturité initiale et courbe : ténor plein (± 10 jours) ou les deux ténors encadrants (+ taux
  interpolé), 52 semaines en plus pour une révisable, sur la semaine précédant l'émission (J-6 à J) ;
  écart taux facial − courbe (révisable : − 52 semaines).
- Émetteur : saisie libre, la liste propose les noms les plus proches (orthographe approchée, nom
  courant des descriptions : « attijari » → ATW E), à faire défiler. La recherche par code y place
  l'émetteur du titre. Historique des émissions par année (nombre, montant), échéancier des lignes
  actives (coupons et capital ; amortissables linéaires approchés ; révisables au dernier taux),
  fréquence d'émission, ténors et formes les plus émis, types d'obligation, 5 dernières émissions.
- Émetteur = PREFERREDNAMEISSUER ; PREFERREDNAMEREGISTRAR (teneur de registre / arrangeur, 16 noms)
  est affiché à côté.
- Exporter : fiche, historique des ténors, émissions, échéancier et répartitions en Excel.

### Performance › ASFIM (tableaux des performances de l'ASFIM)
Les tableaux sont téléchargés depuis l'API publique du site (fundshare.asfim.ma : liste des tableaux et
export Excel par date) et gardés dans le dossier « asfim » de l'application. Si le site est injoignable,
la page reprend le dernier fichier du dossier ; un tableau peut aussi être déposé à la main (.xlsx du site).
- Dernier tableau : date et périodicité (quotidien / hebdomadaire). Filtres Souscripteurs = FGP,
  Affectation = CAPITALISANT, AN > 200 M MAD (pas de filtre d'actif pour ACTIONS). Classement de chaque
  catégorie sur la performance courte (1 jour en quotidien, 1 semaine en hebdomadaire, la colonne 1 jour
  étant vide dans les tableaux hebdomadaires) et le YTD, société de gestion de chaque OPCVM, fonds
  MAROGEST surlignés, synthèse de leurs rangs.
  Hebdomadaire : deux classements de plus, sans filtre : FONDS DÉDIÉS CAM (FCP MAROGEST BONDS, CAPITAL
  TRUST BONDS, FCP EMERGENCE OBLIHORIZON) et SECUR RENDEMENT (20 fonds) ; listes FONDS_DEDIES_CAM et
  SECUR_RENDEMENT dans asfim.py (noms rapprochés à l'orthographe près).
  Actif net : AN géré par société de gestion et par catégorie, rangs et parts de marché.
- Deux dates (hebdomadaires uniquement) : performance = VL de fin / VL de début − 1, mêmes filtres
  (date de fin) et mêmes classements ; variation de l'actif par société de gestion, par catégorie et
  société × catégorie.
- Exporter : synthèse MAROGEST, classements et actif net en Excel.

## Pages figées
Chaque fenêtre est construite à sa première ouverture puis conservée : en revenant, on retrouve
exactement son dernier état (courbe affichée, portefeuille chargé, saisies, filtres). Le mot de
passe Manar reste dans le champ tant que l'onglet est ouvert ; il n'est jamais enregistré.
Fermer ou recharger l'onglet repart de zéro (les données en base, elles, restent).

## Conventions de valorisation (constatées sur l'export VL du 16/11/2026)
- coupon en exact/360 si METHODE_COUPON = 364/360, 365/360, 366/360, ou coupon infra-annuel ;
- révisable à moins d'un an de sa révision : coupons + nominal jusqu'à la révision actualisés
  au taux monétaire (taux de la maturité de révision + spread), VA = flux / (1 + y·j/360) ;
- in fine dont la jouissance n'est pas une date anniversaire de l'échéance (ex. 03/04/2026 →
  05/04/2027) : premier coupon de la jouissance à la date de coupon la plus proche de
  jouissance + 1 an, = nominal × taux × jours réels / base (360 si METHODE_COUPON en x/360,
  365 si R/R) ; coupon couru depuis la jouissance ;
- duration monétaire = sensibilité × (1 + taux) ;
- délai de grâce DECALAG_AMORT, prochaine révision DATE_REVISION (base_titre.xlsx).

## Échéanciers imposés (ECHEANCIERS.xlsm)
Pour les titres dont l'émetteur impose un échéancier sans règle (FPCT, ordinaires, révisables
amortissables…), les flux ne sont pas reconstitués : ils sont lus dans ECHEANCIERS.xlsm, à placer
dans le dossier de l'application. Un bloc par titre : code (ou ISIN) en colonne A sur la ligne
d'en-tête « Date tombée | Amorti | Restant | Coupon », puis une ligne par tombée (B date, C amorti,
D capital restant dû, E coupon), pour le nominal de la base ; une ligne vide entre deux titres.
Le fichier est relu dès qu'il est modifié (relancer la page suffit).
Actualisation, mêmes règles que les autres lignes :
- amortissable : chaque flux au ZC de sa maturité, ROUND(ZC + prime ; 5) ;
- in fine : taux de la courbe à l'échéance + prime, actuariel ;
- révisable : flux jusqu'à la prochaine révision + capital restant dû à la révision, au taux de la
  maturité de révision + prime, monétaire (1 + y·j/360) si la révision est à moins d'un an ;
- maturité : 1er flux en jours / 365 (366), flux suivants + mois écoulés / 12 ;
- coupon couru : prochain coupon × jours depuis la tombée de coupon précédente / jours de la période.

## Base des titres (base_titre.xlsx)
Placée à côté de app.py ; relue automatiquement dès qu'elle est modifiée (même format de colonnes).
- TYPE_TAUX REV -> révisable (in fine jusqu'à la prochaine révision ; si PERIODICITE_REMBOU ≠ FIN,
  sur le capital restant dû) ; PERIODICITE_REMBOU ≠ FIN ou METHODE_VALO ZC -> amortissable ;
  sinon in fine. SPREAD_EMISSION vide -> prime 0.
- Pas de colonne de date de révision : les révisions sont prises aux dates de coupon postérieures
  à DATE_MAJ (modifiables dans la fiche). Une colonne dont le nom contient REVISION serait utilisée.
- Le type d'amortissement (linéaire / constant) et les dates de valorisation et de courbe restent
  à choisir dans la fiche.

En haut de chaque page : ☰ masque ou affiche le menu, ☾ / ☀ passe en mode nuit ou jour
(choix mémorisés par le navigateur).

## Fichiers
- app.py              point d'entrée : barre latérale (navigation, base, export)
- pages/courbe.py     fenêtre 1
- pages/statistiques.py  fenêtre 2
- pages/prevision.py  fenêtre 3
- stats_courbe.py     calculs de l'étude statistique (recalculés quand la base change)
- prevision.py        courbes attendues, Nelson-Siegel, backtests
- valeur_relative.py  sensibilités, KRD, rendement attendu, points morts
- pages/stress.py     fenêtre 4
- pages/pricer.py     Pricer (Analyse de titre)
- base_titre.py       lecture de base_titre.xlsx et correspondance avec le pricer
- pages/forward.py    Taux forward
- pages/swap.py       Swap (Analyse de titre)
- pages/controle.py   Contrôle de valorisation
- pages/fmgc.py, fcm.py, fml.py, fcob.py, fms.py, fco.py, fcor.py, fmb.py  pages des portefeuilles
- vue_portefeuille.py gabarit commun des pages de portefeuille
- asfim.py            tableaux ASFIM : API, cache (dossier asfim), filtres, classements, actif net
- dette_prive.py      lecture de Dette_Prive.xlsx : recherche, fiche, ténors, statistiques émetteur
- tresorerie.py       base tresorerie.db : positions, opérations, dénouements, journal, calculs
- vue_tresorerie.py   gabarit des fenêtres d'opérations de trésorerie
- pages/treso_global.py  page Trésorerie (tableau de bord à onglets)
- portefeuille.py     connexion Manar (connect_to_manar), requête, séparation OPCVM, revalorisation
- controle.py         lecture de l'export, comparaison, diagnostic des écarts
- swap.py             univers valorisé, quantités de swap en montant et en sensibilité
- forward.py          courbe zéro-coupon, forwards, courbe implicite, backtest, prime de terme
- pentification.py    pente vs historique, concentration, analogues, étude des conseils de BAM,
                      scénarios de courbe et rendement des lignes simulées, synthèse
- pages/pentification.py  fenêtre Pentification (Analyse de la courbe des taux)
- assets/00_datefns_fr.js  calendriers en français (locale date-fns)
- echeanciers.py      lecture de ECHEANCIERS.xlsm (échéanciers imposés par l'émetteur)
- pricer.py           moteur du Pricer : taux par source, coupon couru, indicateurs, chocs
- taux_courbe.py      fonctions de valorisation (valo_ligne, valo_ligne_zc, zcp, taux_courbe)
- stress.py           stress test : ténors recalculés par taux_courbe, résolution inverse
- base_courbe.py      accès à la base SQLite, dates de référence, exports Excel
- composants.py, theme.py, assets/style.css   charte visuelle
