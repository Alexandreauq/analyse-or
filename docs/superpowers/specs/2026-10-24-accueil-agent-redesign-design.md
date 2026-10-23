# Refonte de l'accueil et de l'agent IA — conception

Date : 2026-10-24
Branche : `redesign/modernisation` (partie de `main`)
Périmètre : page d'accueil (`#home`), agent IA flottant (`docs/assistant_bubble.js`), navigation mobile. Les autres pages (Or, Indices, Portefeuille…) seront refaites plus tard, page par page, dans des specs séparées.

## 1. Intention

L'accueil est la première impression du site. Aujourd'hui (main) c'est une grande barre de recherche IA au centre d'un écran presque vide : un nouveau visiteur ne comprend pas ce que propose le produit sans naviguer. L'agent en bas à droite (sphère turquoise, panneau à boutons texte et emojis) a l'air daté, sort de la palette et ne suit pas le thème clair.

Objectif : moderniser la saisie centrale et l'agent, garder l'ADN visuel (bleu nuit, or, Newsreader + Inter), montrer le produit par ses vraies données du jour sans charger l'écran, et rendre le mobile confortable (navigation au pouce).

## 2. Objectifs / non-objectifs / anti-objectifs

**Objectifs**
- Barre centrale modernisée, cohérente desktop/mobile, réutilisée telle quelle dans l'agent.
- Accueil desktop rempli par des données réelles (tuiles live) + un guide « ce que je sais faire » qui n'apparaît qu'à l'usage.
- Accueil mobile dense : une carte « Aujourd'hui » compacte.
- Barre d'onglets en bas sur mobile (remplace le menu hamburger + tiroir).
- Agent refait : panneau flottant sur desktop, onglet central « IA » + feuille montante sur mobile.
- Toutes les couleurs des nouveaux composants passent par les variables CSS du thème (sombre et clair).

**Non-objectifs**
- Aucun changement backend (API `/ask`, format SSE, jetons, JSON publiés) : on consomme ce qui existe.
- Pas de refonte des pages Or / Indices / Portefeuille dans cette spec (seulement leur adaptation à la barre d'onglets et au retrait du tiroir).
- Pas de framework, pas d'étape de build : HTML/CSS/JS vanilla dans `docs/`, servi par GitHub Pages.
- Ne pas reprendre le reste de la branche `homepage` (lingot 3D, sommaire numéroté, colonnes latérales, refonte du suivi des signaux).

**Anti-objectifs (échec même si ça « marche »)**
- Une fonction existante de l'assistant perdue : voix (dictée), historique local, niveau Technique / Grand public, liens de navigation, navigation instantanée sans IA (`matchInstantNav`), bouton Copier, révélation progressive de la réponse (remplacée par la nouvelle animation, voir 4.4 bis), annulation au retour accueil, envoi des positions manuelles (`manual_positions`).
- Des icônes ou textes mal centrés dans leurs boutons/pilules (point explicitement soulevé).
- Un élément masqué par l'encoche / Dynamic Island / coins arrondis de l'iPhone.
- L'agent qui recouvre la barre d'onglets mobile.
- Des couleurs codées en dur qui cassent le thème clair.

## 3. Contraintes

- Fichiers concernés : `docs/index.html` (CSS + markup + JS inline), `docs/assistant_bubble.js`, `docs/service-worker.js` (incrément `CACHE_NAME` à chaque livraison, nouvelles ressources éventuelles dans la liste de précache).
- Tests JS existants (`docs/*.test.js`, workflow `js_tests.yml`) doivent rester verts.
- `prefers-reduced-motion` : toute animation nouvelle (lueur qui tourne, défilement du placeholder, transitions) se coupe ou se fige.
- Points de rupture existants conservés : mobile/tablette `< 1024px`, desktop `≥ 1024px`.
- Safe areas iOS : `env(safe-area-inset-*)` sur tout élément fixé en haut ou en bas.

## 4. Ce qui a été décidé (par l'utilisateur)

### 4.1 Reprise de la branche `homepage`
Uniquement :
1. **Barre d'onglets en bas sur mobile** (`< 1024px`), remplaçant `menu-button` + `drawer` + `drawer-scrim`. Minières retiré de la navigation (desktop et mobile).
2. **Transitions en fondu** entre écrans (opacité 0 → 1, `pointer-events` coupés hors écran actif ; `#home` traité comme les autres écrans via `SLIDING_PANES = ['home', 'or', 'indices', 'portefeuille']`), au lieu du glissement « push ». `hidePane` écoute `opacity` au lieu de `transform`.

Le reste de l'accueil part de `main`.

### 4.2 Barre centrale (« pilule lumineuse », fusion des pistes A + C)
- **Forme** : une pilule sur une seule ligne. À droite dans la pilule : bouton micro (rond, transparent) puis bouton envoyer rond doré (dégradé or, flèche vers le haut). Icônes **centrées au pixel** dans leurs cercles.
- **Placeholder animé** : fait défiler des exemples de questions (« Demande-moi *les meilleures opportunités du CAC 40* »…), avec la partie exemple en or clair.
- **Raccourci** `/` sur desktop : met le focus dans la barre (indice visuel `/` dans la pilule au repos).
- **Lueur** : contour or conique qui tourne lentement.
  - Desktop : sobre au repos (simple filet `--border-strong`), lueur allumée au focus, pendant la saisie et pendant la réflexion de l'IA.
  - Mobile : lueur **constante**.
- **Disposition verticale** (desktop et mobile) : **Historique** (pilule avec icône horloge) centré **au-dessus** de la barre ; sélecteur **Technique / Grand public** centré **en dessous**. Les textes de toutes ces pilules sont centrés verticalement et horizontalement (hauteur fixe).
- Sur mobile, la barre est placée vers le tiers bas de l'écran, au-dessus de la barre d'onglets avec de l'air, remontée pour occuper l'espace central.

### 4.3 Accueil desktop (≥ 1024px) — « tuiles live + guide au clic » (mix B + C)
- **En-tête** : wordmark « AI Investment » + phrase d'accroche qui dit ce que fait le produit (ex. « L'IA note chaque jour l'or et 1 299 entreprises mondiales, repère les points d'entrée et suit ton portefeuille. » — chiffre calculé depuis les données, pas en dur).
- **Au repos** : sous la barre, une rangée de **4 tuiles** discrètes (semi-transparentes) :
  1. **Or · score macro** — `composite_score` + interprétation courte, mini-courbe, lien « Voir l'analyse → » (`#or`).
  2. **Indices · top du jour** — meilleur score parmi les entreprises + son nom + nombre d'entreprises notées, mini-courbe, lien vers `#indices`.
  3. **Signaux · perf réelle** — taux de réussite et alpha moyen vs indice, nombre en cours, lien `#indices/suivi`.
  4. **Bots · statut** — état du bot Or (actif / simulation / coupé / pause) et du bot Actions, lien `#portefeuille`.
  Au survol une tuile s'éclaire et se soulève ; clic = ouvre sa section.
- **Au clic dans la barre** : lueur allumée, les tuiles passent en fond (atténuées + léger flou) et un **panneau « ce que je sais faire »** s'ouvre sous la barre, en 3 colonnes (Or / Indices / Portefeuille) : icône + titre, une ligne d'explication, 2 questions exemples cliquables chacune. Les exemples **se filtrent pendant la frappe**. Clavier : ↑↓ pour choisir, Entrée pour envoyer, Échap pour fermer. Pied du panneau avec ces raccourcis.

### 4.4 Accueil mobile (< 1024px) — carte « Aujourd'hui »
- Wordmark + accroche en haut.
- **Carte « Aujourd'hui »** (en-tête « Aujourd'hui » + date) : 4 lignes compactes (~40px) façon widget : icône dans un carré arrondi, libellé + précision grise, valeur (Newsreader, colorée vert/rust/or), chevron. Mêmes 4 infos que les tuiles desktop. Chaque ligne ouvre sa section.
- Puis Historique / barre (lueur constante) / Technique·Grand public, barre remontée pour combler l'espace central.

### 4.4 bis Accueil — réponse affichée
- **Desktop** : dès la première réponse, les tuiles disparaissent (fondu). La conversation s'affiche en **bulles**, même style que l'agent : question à droite, réponse à gauche avec avatar sphère dorée, liens en pilules dorées, actions copier / écouter sous chaque réponse. La barre (avec Historique au-dessus, niveau en dessous) se place en bas de l'écran. « Nouvelle conversation » ramène l'accueil à l'état repos (tuiles de retour).
- **Mobile** : même logique ; la carte « Aujourd'hui » cède sa place à la conversation.
- **Réflexion** : la sphère de particules existante est conservée mais passe du turquoise à l'**or**.
- **Rédaction de la réponse** (demande explicite : « fluide et moderne ») : remplacer l'effet machine à écrire caractère par caractère par une **apparition mot par mot en fondu** (chaque mot passe d'opaque 0 + léger flou à net, ~150–250 ms, en cascade), un **défilement automatique doux** qui suit le texte, et un curseur discret en fin de flux. Le rythme reste piloté par le flux SSE (pas d'attente artificielle une fois le flux terminé : le reste apparaît rapidement). Les liens et les actions apparaissent en fondu une fois le texte terminé. `prefers-reduced-motion` : texte affiché directement, sans fondu. Même animation dans l'agent (desktop et mobile).

### 4.5 Barre d'onglets mobile
- Fixe en bas, fond translucide flouté, `padding-bottom: env(safe-area-inset-bottom)`.
- **5 onglets** : Accueil · Or · **IA** (centre) · Indices · Portefeuille.
- Icônes grandes (~27–28px), libellé centré sous l'icône, onglet actif en or avec pastille de fond derrière l'icône.
- **Onglet IA** : sphère dorée surélevée qui dépasse au-dessus de la barre (bouton d'action central), libellé « IA ».
- **Thème** sort de la barre d'onglets : petite icône dans l'en-tête de chaque écran, en haut à droite, **intégrée au flux de l'en-tête** (défile avec la page, pas `position: fixed`), avec marges tenant compte de `env(safe-area-inset-top)` / `env(safe-area-inset-right)` pour ne jamais passer sous l'encoche, la Dynamic Island ou un coin arrondi (portrait et paysage).
- Les écrans réservent la hauteur de la barre (`padding-bottom`), la barre du comparateur Indices remonte au-dessus d'elle (comme sur la branche `homepage`).

### 4.6 Agent IA — desktop : panneau flottant
- **Lanceur** : petite sphère dorée 56px en bas à droite (remplace la sphère de particules turquoise 96px). Ouvert, elle affiche une croix.
- **Panneau** (~370px de large, coins 20px) au-dessus du lanceur :
  - **En-tête** : avatar sphère, « Assistant », **puce de contexte** (point vert + nom de la page courante, ex. « Fiche LVMH », dérivée de `contexteCourant()`), puis icônes : lecture vocale (bascule), nouvelle conversation, fermer.
  - **Messages en bulles** : question de l'utilisateur à droite (fond `--bg-4`), réponse de l'IA à gauche avec avatar sphère ; chiffres/termes clés mis en valeur en or clair quand la réponse les balise (sinon texte simple).
  - Sous chaque réponse : **liens** (événements `liens`) en pilules dorées « … → », actions **copier** et **écouter**.
  - **Pied** : suggestions liées à la page courante (pilules), puis la **même pilule de saisie** que l'accueil (micro + envoyer doré, lueur à l'usage).
  - Niveau Technique / Grand public réglable dans l'agent (aujourd'hui figé à `technique`) et partagé avec l'accueil (même clé `analyse-or-answer-level`).

### 4.7 Agent IA — mobile : feuille montante
- Ouvert par l'onglet **IA** de la barre d'onglets (plus de bulle flottante sur mobile).
- **Feuille** qui monte du bas presque plein écran (la page reste visible en haut, assombrie), poignée de glisser, même contenu que le panneau desktop (en-tête avec contexte et icônes, bulles, liens, suggestions, pilule de saisie avec lueur). Fermeture : glisser vers le bas, chevron, ou appui sur le fond.

### 4.8 Validations transverses
- Tous les boutons/icônes/pilules : contenu centré (icônes SVG avec `viewBox`, conteneurs `display:grid; place-items:center` ou flex centré, hauteurs fixes).
- Palette or ; fini le turquoise (`TEAL` dans `assistant_bubble.js`, `rgba(42,91,255,…)` dans le focus de la barre d'accueil).

### 4.9 Accueil et agent : deux conversations indépendantes
Décision utilisateur : les deux conversations restent **séparées, comme aujourd'hui** (l'historique envoyé à l'IA depuis l'accueil n'est pas partagé avec l'agent, et inversement). On ne fusionne pas les deux moteurs en un module commun : chacun garde son code d'envoi/flux. La refonte porte sur l'**habillage et l'affichage** (pilule de saisie, bulles, liens, animation de rédaction, couleurs), que les deux côtés reproduisent à l'identique. Les fonctions existantes de chaque côté sont conservées ; l'agent gagne seulement ce que la présente spec lui ajoute explicitement (niveau réglable, copier/écouter par réponse, animation de rédaction, suggestions par page).

## 5. Choix laissés au constructeur / pris par moi (à valider à la lecture)

- **Rendu partagé** : pour garantir l'affichage identique, le CSS des bulles/liens/pilule et la fonction d'animation de rédaction peuvent être écrits une fois et réutilisés des deux côtés (sans fusionner les moteurs ni les conversations).

- **Agent masqué sur l'accueil** desktop (la barre centrale fait le travail) ; sur mobile, l'onglet IA depuis l'accueil met le focus dans la barre centrale au lieu d'ouvrir la feuille.
- **Bouton « Nouvelle conversation »** de l'accueil : visible seulement une fois qu'une réponse est affichée.
- **Mobile, au clic dans la barre** : la carte « Aujourd'hui » cède sa place aux questions exemples (même emplacement, pas de panneau qui monte).
- **Minières** : n'existe que comme deux boutons désactivés et un symbole SVG (aucun écran, aucune route, aucune mention côté `assistant_ia`). On supprime ces boutons et le symbole `icon-minieres`, rien d'autre.
- **Mini-courbes des tuiles** : aucune série historique n'est servie depuis `docs/` pour l'or (`score_history.json` reste à la racine du dépôt, hors site) ni pour les autres tuiles. On ne touche ni aux workflows ni aux JSON publiés : **pas de mini-courbe dans cette itération**, la tuile garde chiffre + explication + lien. Le composant tuile prévoit un emplacement optionnel pour une courbe, à brancher plus tard si une série est publiée.
- **Questions exemples et suggestions par page** : liste statique dans le JS (par route), pas générée par l'IA.
- **CSS** : on reste dans `index.html` pour cette itération ; nouveaux composants nommés et regroupés dans une section commentée, couleurs uniquement via variables (ajout de variables manquantes dans `:root` et `html[data-theme="light"]`). L'agent (`assistant_bubble.js`) lit ces mêmes variables au lieu de couleurs en dur.
- **Service worker** : pas d'enregistrement sur `localhost`/`127.0.0.1` (repris de `homepage`, utile pour itérer), incrément du `CACHE_NAME`.

## 6. Vérification

- Captures à 1440×900 (desktop), 393×852 (iPhone avec Dynamic Island) et 390×844, thèmes sombre et clair, pour : accueil repos, accueil focus barre, accueil réponse affichée, agent ouvert sur une fiche (desktop), feuille IA (mobile).
- Navigation complète au clavier sur l'accueil desktop (`/`, ↑↓, Entrée, Échap).
- `npm`/`node` tests JS existants verts.
- Parcours de non-régression de l'assistant : question → réflexion → réponse tapée → liens → copier ; historique ; dictée ; changement de niveau ; nouvelle conversation ; retour accueil pendant une réponse.
