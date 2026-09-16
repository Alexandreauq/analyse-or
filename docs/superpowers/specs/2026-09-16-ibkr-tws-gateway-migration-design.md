# Migration du bot actions IBKR vers IB Gateway / TWS API — design

## Statut

**[Proposé — non validé]**, sauf les points de la section « Décisions
validées avec l'utilisateur » ci-dessous, explicitement arbitrés en
session de brainstorming le 2026-09-16.

## 1. Contexte et objectif

Le bot actions IBKR (voir
`docs/superpowers/specs/2026-09-14-ibkr-equities-automation-design.md`,
Plan A + Plan B fusionnés le 2026-09-15) est déployé sur le VPS Hetzner
mais bloqué : le **Client Portal Gateway** (le proxy Java local
officiel d'IBKR, seul canal par lequel `ibkr_bot/gateway.py` parle au
broker) ne parvient jamais à établir de session authentifiée après un
login + 2FA pourtant réussis côté navigateur (`iserver/auth/status`
reste bloqué à `401`/`authenticated: false`).

Investigation menée avant ce document (voir historique de session,
non reproduite ici) :

- Le binaire du Client Portal Gateway distribué par IBKR est figé sur
  un build d'avril 2023 — confirmé indépendamment (deux téléchargements
  avec contournement de cache) et confirmé par le mainteneur du projet
  communautaire `Voyz/ibeam` sur son propre ticket (aucun build plus
  récent trouvable nulle part, officiel ou communautaire).
- Le contournement le plus corroboré par la communauté pour ce
  symptôme exact (`proxyRemoteHost` pointé vers un serveur régional
  IBKR plutôt que le load-balancer par défaut) a été testé
  (`1.api.ibkr.com`, `2.api.ibkr.com`) : les deux sont explicitement
  refusés par IBKR pour l'IP du VPS (« IP not authorized, use
  api.ibkr.com instead »).
- Le symptôme correspond à une classe de bugs documentée et non
  résolue en général sur le Client Portal Gateway (« Gateway session
  active but not authenticated », plusieurs tickets communautaires
  ouverts et fermés sans fix universel).

**Décision** : abandonner le Client Portal Gateway et migrer
`ibkr_bot/gateway.py` vers **IB Gateway** (l'application headless
d'IBKR, la même famille que TWS mais sans interface complète, conçue
pour tourner sur un serveur) piloté via la **TWS API** — l'interface
utilisée par la quasi-totalité des outils d'algo-trading tiers, bien
plus mature et éprouvée que le Client Portal Gateway.

## 2. Décisions validées avec l'utilisateur

1. **IB Gateway, pas TWS complet** — application headless dédiée à
   l'automatisation serveur, pas le poste de trading graphique complet.
2. **IBC (IBController)** — outil communautaire open-source standard
   pour automatiser le lancement d'IB Gateway et la saisie
   identifiant/mot de passe chaque matin. Les identifiants sont stockés
   chiffrés sur le VPS, au même niveau de sensibilité que le `.env`
   actuel.
3. **Le 2FA quotidien n'est pas éliminé, et ce n'est pas un défaut du
   design** — c'est une règle de sécurité IBKR indépendante du logiciel
   utilisé (sessions valables ~24h, aucune intégration OAuth
   disponible pour un compte particulier). Ce que corrige cette
   migration : la fiabilité de la connexion une fois le 2FA validé
   (bug actuel), pas la fréquence de l'intervention humaine. Avec
   IBC, le geste quotidien attendu de l'utilisateur se réduit à valider
   une notification push IBKR Mobile — IBC gère le reste
   (lancement, identifiant, mot de passe) automatiquement.
4. **`ib_async`** (fork maintenu de `ib_insync`, dont le développement
   original s'est arrêté) comme librairie Python cliente de la TWS API.
5. **Interface publique de `gateway.py` inchangée** — mêmes noms de
   fonctions, mêmes formes de retour. Tout le reste du bot
   (`contracts.py`, `portfolio.py`, `sizing.py`, `signals.py`,
   `daily.py`) appelle déjà `gateway.py` uniquement par ses fonctions
   publiques, jamais le protocole réseau directement — seul l'intérieur
   de `gateway.py` change.
6. **Modèle de connexion conservé** : une connexion ouverte au début du
   batch quotidien, fermée à la fin — pas de connexion permanente en
   arrière-plan. Change le moins possible le fonctionnement actuel de
   `daily.py`.
7. **Migration en parallèle, pas en remplacement à chaud** : IB
   Gateway + IBC installés à côté du Client Portal Gateway existant
   (services systemd séparés), validés en `dry_run` sur plusieurs
   jours avant de couper l'ancien.

## 3. Composants VPS

- **IB Gateway** : application Java headless d'IBKR, remplace
  `clientportal.gw`. Écoute la TWS API sur `127.0.0.1:4002` (paper) ou
  `127.0.0.1:4001` (réel) — jamais exposé au-delà de `localhost`,
  protégé par `ufw` exactement comme le port 5000 aujourd'hui (aucune
  règle d'ouverture, le default-deny suffit).
- **IBC** : automatise le cycle de vie d'IB Gateway (lancement,
  saisie identifiant/mot de passe, détection de l'écran de 2FA,
  redémarrage en cas de crash). Fichier de config séparé contenant les
  identifiants, permissions restreintes comme le `.env` actuel.
- **Xvfb** (serveur d'affichage virtuel X11) : IB Gateway est une
  application graphique Java (Swing) — même piloté par IBC en mode
  serveur, il lui faut un display virtuel pour s'exécuter. Nouveau
  composant système, standard pour ce type de déploiement, aucune
  exposition réseau.
- Nouveau service systemd (`ibkr-gateway-tws.service` ou nom
  équivalent, à trancher au moment du plan) qui lance Xvfb + IBC + IB
  Gateway comme une unité supervisée, sur le modèle de l'actuel
  `ibkr-gateway.service`.

## 4. `gateway.py` : portée du remplacement

Chaque fonction publique garde son nom et sa forme de retour actuels ;
seule l'implémentation change (appel `ib_async` au lieu d'une requête
REST). Deux fonctions n'ont pas d'équivalent direct côté TWS API :

- **`tickle()`** — devient un simple contrôle `ib.isConnected()` :
  `ib_async` maintient déjà la connexion vivante en interne (heartbeat
  intégré à la librairie), pas besoin d'un appel explicite de maintien
  de session.
- **`reauthenticate()`** — n'a pas d'équivalent côté TWS API : une
  session expirée exige une vraie reconnexion socket (relancée par
  IBC au niveau du système, pas un endpoint de rafraîchissement
  applicatif). Devient un no-op documenté ; la responsabilité de
  reconnexion passe à IBC/au service systemd.

Les correspondances exactes des autres fonctions (`positions()`,
`place_market_order()`, `ledger()`/`cash_by_currency()`,
`search_contract()`/`contract_info()`, `exchange_rate()`,
`order_status()`, `confirm_reply()`) vers les appels `ib_async`
réels devront être vérifiées contre la documentation officielle
`ib_async`/IBKR TWS API **au moment de l'écriture du plan**, comme ça
a été fait pour l'intégration MetaApi et le Client Portal Gateway —
ne jamais deviner un nom de méthode ou une forme de retour.

## 5. Cycle de connexion et gestion des erreurs

`daily.py::preflight()` garde sa forme (3 tentatives, 10 minutes
d'écart — spec 5.5 du document Plan A/B) mais son contenu change :
au lieu d'un `POST /iserver/auth/status`, il tente
`ib.connect(host, port, clientId)` sur le port TWS local. Connexion
socket réussie + comptes non vides en retour → authentifié. Si IBC n'a
pas fini sa séquence du matin (ou si le 2FA n'a pas encore été validé
par l'utilisateur), la connexion échoue proprement → comportement
inchangé pour l'appelant (nouvelle tentative, puis abandon + email si
toujours indisponible après 3 essais — aucun changement de
comportement observable pour l'orchestrateur).

## 6. Tests

`ib_async`'s `IB` est mocké dans les tests, jamais de connexion réseau
réelle — même principe que le monkeypatch actuel de `requests.post`/
`requests.get`. Toute la suite de tests existante
(`test_contracts.py`, `test_portfolio.py`, `test_sizing.py`,
`test_daily.py` côté logique métier) reste valide sans modification,
puisque l'interface avec `gateway` ne change pas. Seuls
`tests/ibkr_bot/test_gateway.py` et la partie de `test_daily.py` qui
simule l'authentification doivent être réécrits pour le nouveau
backend.

## 7. Déploiement / migration

1. Installer IB Gateway + IBC + Xvfb sur le VPS, en parallèle du
   Client Portal Gateway existant (celui-ci n'est pas touché tant que
   la bascule n'est pas validée).
2. Configurer IBC avec les identifiants IBKR (compte réel
   U28849893), valider manuellement le premier login (2FA compris).
3. Réécrire `gateway.py` (`ib_async`) et les tests associés.
4. Basculer `IBKR_GATEWAY_URL`/la configuration de connexion du
   `.env` vers les nouveaux host/port TWS (ex. `127.0.0.1:4001`).
5. Valider en `dry_run` sur plusieurs jours (batch quotidien réel,
   décisions journalisées, aucun ordre réel envoyé — comportement
   déjà garanti par le garde-fou structurel de Plan A/B).
6. Une fois la stabilité confirmée : désinstaller le Client Portal
   Gateway (service, dossier, entrée `.env` obsolète).

## 8. Hors périmètre de ce document

- Le choix entre compte réel et compte paper pour les tests initiaux
  de connexion (à trancher au moment du plan — un compte paper lié
  simplifierait les tests sans risque, mais ajoute un aller-retour de
  configuration supplémentaire).
- Toute optimisation de la fréquence de reconnexion ou passage à une
  connexion permanente — explicitement écarté par la décision 6
  ci-dessus, à reconsidérer seulement si le modèle actuel se montre
  insuffisant en pratique.
- La configuration exacte d'IBC (fichier `IBC/config.ini`, détection
  d'écran de 2FA, timeouts) — détail d'implémentation à régler lors de
  l'écriture du plan, pas un point structurant à valider
  individuellement.
