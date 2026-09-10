# Bot de trading automatique (or, MT5/Vantage) — design

## Contexte et objectif

Depuis le tout début du chantier Portefeuille, l'utilisateur a évoqué l'objectif
final : recevoir un signal, et que le site passe l'ordre chez un courtier —
explicitement repoussé à une "phase 3" séparée à chaque étape précédente
(suivi manuel, puis synchronisation lecture seule IBKR, puis MT5/Vantage),
en raison du risque financier direct que porte l'exécution automatique
d'ordres réels.

Cette phase est ce chantier. Contrairement aux phases précédentes (lecture
seule, dégradation propre suffisante), il s'agit ici de **passer de vrais
ordres avec de l'argent réel, de façon totalement autonome** — l'utilisateur
a explicitement choisi l'autonomie complète (pas de validation manuelle à
chaque trade) tout en demandant que ce soit "le plus safe possible". Le
design ci-dessous traduit cette demande en garde-fous concrets, pas en
promesse vague.

La synchronisation MT5/Vantage (lecture seule, `portfolio_sync_mt5.py`,
branche `portefeuille-mt5`) est déjà en place et fonctionnelle avec de
vraies positions synchronisées — ce chantier réutilise le même courtier
(Vantage) et le même service tiers (MetaApi.cloud), mais avec un mécanisme
d'accès fondamentalement différent (voir Sécurité).

## Portée

**Dans le périmètre :**
- Un nouveau service (`gold_bot`), qui tourne en continu sur un VPS dédié —
  premier composant non-statique, non-cron de ce projet (tout le reste du
  site est un site statique généré périodiquement par GitHub Actions).
- Le moteur de décision : la logique de confluence déjà construite dans
  `docs/scalping.js` (tendance/support-résistance/indicateurs/chandeliers,
  bougies 1min XAUUSD), portée fidèlement en Python — y compris la
  condition de tendance intentionnellement inversée par rapport au texte
  du spec d'origine (le comportement du JS fait foi, pas le texte du
  spec historique).
- L'exécution des ordres via l'API de trading MetaApi (distincte de l'API
  de lecture seule déjà utilisée par la synchro).
- Les garde-fous : taille de position par le risque, coupe-circuit
  journalier, interrupteur d'urgence manuel.
- Un petit point d'accès web (VPS, HTTPS) que le site statique interroge
  pour afficher l'état du bot et actionner l'interrupteur d'urgence.
- Un résumé quotidien par email des trades du bot (réutilise le SMTP déjà
  configuré pour les alertes indices).
- Un mode simulation ("dry-run") pour valider le comportement avant de
  laisser le bot toucher à de l'argent réel.

**Hors périmètre (cette phase) :**
- Authentification "de vrai compte" pour le bouton d'urgence — un jeton
  secret simple suffit pour l'instant, l'utilisateur a explicitement prévu
  d'associer ça à un vrai compte plus tard, séparément.
- Tout autre instrument que XAUUSD, ou toute autre stratégie que le moteur
  de confluence déjà existant.
- Déploiement continu (CI/CD) du service sur le VPS — déploiement manuel via
  script (voir Déploiement), automatisable plus tard si le besoin se
  confirme.
- Couche de coupe-circuit supplémentaire côté "Risk management API" de
  MetaApi — évoquée pendant le brainstorm comme piste complémentaire, non
  retenue pour cette phase (notre propre coupe-circuit suffit, pas de
  complexité ajoutée sans besoin démontré).
- Toute autre paire/instrument MT5 que XAUUSD.

## Architecture globale

```
[TwelveData: bougies 1min XAUUSD]
        │
        ▼
[gold_bot: moteur de décision (confluence)]
        │  signal achat/vente/neutre
        ▼
[gold_bot: garde-fous]  ← état persistant (solde début de journée,
        │                  interrupteur d'urgence, positions suivies)
        │  décision finale
        ▼
[MetaApi: API de trading] → ordre réel chez Vantage
        │
        ▼
[gold_bot: journalisation locale] → email quotidien (SMTP existant)

[gold_bot: petit point d'accès HTTPS] ←→ [site statique : bloc "Bot Or"]
   GET /status · POST /kill · POST /resume        (bouton d'urgence)
```

Le reste du site (indices, or, scalping affiché aux visiteurs, portefeuille,
synchro MT5 lecture seule) continue de fonctionner exactement comme
aujourd'hui, sans dépendance à ce nouveau service — c'est un ajout isolé,
pas une modification de l'existant.

## Sécurité — identifiants de trading

La synchronisation lecture seule utilise le **mot de passe investisseur**
MT5, qui interdit structurellement le passage d'ordres au niveau du
protocole MT5 lui-même. Pour que le bot puisse trader réellement, il faut
un **deuxième compte MetaApi**, connecté cette fois avec le **mot de passe
de trading** réel du compte Vantage — un identifiant qui, lui, peut agir
sur l'argent réel du compte. La garantie de sécurité ne vient donc plus du
protocole (comme pour la synchro), mais de tout ce que ce design construit
autour : garde-fous, isolation, accès restreint.

**Coût** : un deuxième compte connecté chez MetaApi facture séparément
(même ordre de grandeur que le premier, ~9$/mois), en plus du coût du VPS.

**Emplacement des identifiants** : uniquement sur le VPS, dans un fichier
`.env` avec permissions restreintes (lisible seulement par l'utilisateur
système dédié qui fait tourner le bot) — jamais commité dans git, jamais
loggé. Le VPS lui-même : connexion SSH par clé uniquement (pas de mot de
passe), pare-feu limitant les ports ouverts au strict nécessaire (SSH +
443 pour le point d'accès HTTPS), mises à jour de sécurité automatiques,
service tournant sous un utilisateur non-root dédié. Pas de gestionnaire
de secrets externe (Vault, etc.) — complexité disproportionnée pour un VPS
personnel (YAGNI).

## Moteur de décision et exécution

- **Confluence** : portage fidèle de `docs/scalping.js` en Python,
  réévalué à chaque clôture de bougie 1min XAUUSD (même source de données
  que le site aujourd'hui, TwelveData). Le calcul entrée/stop-loss/
  take-profit déjà présent dans le signal actuel est réutilisé tel quel.
- **Sur signal achat/vente :**
  - Aucune position ouverte par le bot → en ouvre une, taille calculée par
    le risque (voir Garde-fous), avec le stop-loss/take-profit du signal.
  - Position déjà ouverte dans le même sens → aucune action (pas
    d'empilement ; jamais plus de 5% de risque engagé à la fois).
  - Position ouverte dans le sens opposé → la clôture, puis ouvre la
    nouvelle position dans le nouveau sens (décision explicite de
    l'utilisateur : le bot suit le signal en direct plutôt que de laisser
    l'ancienne position courir jusqu'à son propre stop-loss/take-profit).
- **Sur signal neutre** : aucune action ; une position déjà ouverte
  continue de suivre son propre stop-loss/take-profit.
- **Fiabilité au redémarrage** : le bot ne se fie jamais uniquement à sa
  mémoire locale pour savoir s'il a une position ouverte — à chaque
  démarrage (y compris après un crash), il interroge MetaApi pour l'état
  réel des positions du compte avant de prendre la moindre décision. Un
  redémarrage du VPS ne peut donc jamais faire ouvrir une position en
  double.

## Garde-fous

- **Taille de position — dimensionnement par le risque (5%)** : le lot est
  calculé de sorte que la perte, si le stop-loss est touché, corresponde à
  5% du solde du compte — pas 5% de la valeur notionnelle engagée. Calcul :
  `taille = (5% du solde) / (distance entrée→stop-loss en points × valeur
  du point)`. C'est la méthode standard de dimensionnement par le risque en
  trading.
- **Coupe-circuit — perte journalière (-10%)** : calculé sur le **solde au
  début de la journée UTC** (figé), jamais sur le solde courant — sinon le
  seuil se déplacerait de façon incohérente au fil des pertes. Une fois
  déclenché : plus aucune nouvelle position n'est ouverte jusqu'au
  lendemain (réinitialisation automatique à minuit UTC) ; une position déjà
  ouverte au moment du déclenchement n'est **pas** fermée de force — elle
  continue de suivre son propre stop-loss/take-profit, cohérent avec le
  principe "on n'intervient jamais sur une position ouverte en dehors des
  cas explicitement définis" (renversement de signal, ou clôture normale
  par SL/TP).

## Interrupteur d'urgence et intégration site

Le VPS expose un petit point d'accès HTTPS (certificat Let's Encrypt), trois
routes :
- `GET /status` : état courant — bot actif ou coupé, P&L du jour, coupe-
  circuit déclenché ou non.
- `POST /kill` : coupe le bot — plus aucune nouvelle position n'est ouverte
  ; comme pour le coupe-circuit, une position déjà ouverte n'est pas fermée
  de force.
- `POST /resume` : relance le bot après un arrêt manuel via `/kill`. (Le
  coupe-circuit journalier se réinitialise tout seul le lendemain — pas
  besoin de `/resume` pour ce cas-là.)

**Authentification** : un jeton secret unique (chaîne aléatoire, comme un
mot de passe), saisi une fois dans le navigateur et conservé en
`localStorage` — pas de vrai système de comptes pour cette phase (prévu
comme amélioration future, hors périmètre ici, voir Portée). Le risque
d'exposition est asymétrique : quelqu'un qui découvrirait ce jeton pourrait
au pire couper le bot (aucun risque financier direct) — jamais l'activer,
augmenter une position, ou déclencher un ordre ; ces actions n'existent
simplement pas sur ce point d'accès.

Sur le site, un nouveau bloc "Bot Or" dans l'onglet Portefeuille affiche le
statut en direct et propose le bouton d'arrêt/relance.

## Journalisation et suivi

- Chaque décision du bot (signal évalué, action prise ou non, ordre passé,
  erreur rencontrée) est journalisée localement sur le VPS avec horodatage.
- `docs/real_portfolio_mt5.json` (synchro lecture seule quotidienne)
  continue de fonctionner sans aucun changement — les positions du bot y
  apparaissent naturellement au run suivant, pas de duplication d'affichage
  à construire.
- **Résumé quotidien par email** : réutilise le SMTP déjà configuré pour
  les alertes indices existantes. Récapitule les trades du jour et le P&L,
  envoyé une fois par jour — pas d'alerte à chaque trade individuel
  (explicitement non demandé), pour garder un signal utile sans
  submerger.

## Cas limites

- **VPS redémarre ou plante en pleine position ouverte** : couvert par la
  réconciliation au démarrage (voir Moteur de décision).
- **API de trading MetaApi indisponible** : le bot ne prend aucune
  décision tant que l'appel échoue — jamais d'action basée sur une donnée
  incomplète ou périmée.
- **Connexion MT5 perdue côté MetaApi** (compte déconnecté de leur
  infrastructure) : le bot se met en pause et journalise l'incident
  (remonté dans le résumé quotidien) ; aucune tentative d'ouvrir une
  position tant que la connexion n'est pas rétablie.
- **Interrupteur d'urgence activé pendant qu'un ordre est en cours
  d'envoi** : l'ordre déjà envoyé au courtier va à son terme (on n'annule
  jamais un ordre en transit) ; seules les décisions **futures** sont
  bloquées à partir de ce moment.

## Déploiement

Déploiement manuel via un script (connexion SSH, `git pull` du dépôt sur le
VPS, redémarrage du service `systemd`) — pas de pipeline CI/CD pour cette
phase, complexité disproportionnée pour ce stade (YAGNI, automatisable plus
tard si le besoin se confirme).

## Tests et mise en production progressive

- **Tests unitaires** (pytest, même densité que le reste du projet) :
  moteur de confluence porté (comparé aux mêmes cas de test que
  `scalping.test.js` pour garantir un comportement identique au JS
  existant) ; calcul de taille de position par le risque ; logique du
  coupe-circuit (déclenchement, non-déclenchement, réinitialisation
  journalière) ; réconciliation au démarrage (positions existantes
  détectées correctement).
- **Mode simulation ("dry-run") avant mise en réel** : le bot tourne
  d'abord dans un mode où il évalue les signaux et journalise ce qu'il
  aurait fait (ordre, taille, stop-loss/take-profit) **sans jamais appeler
  l'API de trading réelle**. Permet de valider plusieurs jours de
  comportement réel avant de laisser le bot agir sur de l'argent réel.
  Bascule vers le mode réel = un changement de configuration sur le VPS,
  pas un redéploiement de code.
