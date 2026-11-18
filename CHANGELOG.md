# Journal des modifications

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), versions selon [SemVer](https://semver.org/lang/fr/).
Chaque section `## [X.Y.Z]` sert de notes à la release correspondante.

## [6.0.1] - 2026-11-18

### Ajouté
- Licence **MIT** et mentions des composants tiers (`THIRD_PARTY_NOTICES.md`), incluses dans le `.exe`.
- Mises à jour entièrement automatiques : les mises à jour de dépendances testées par la CI sont fusionnées puis publiées en version correctrice, sans intervention.

### Modifié
- Lecture des photos HEIC via `pi-heif` (lecture seule). Le `.exe` n'embarque plus l'encodeur x265 (licence GPL, 22 Mo) : il est plus léger et compatible avec la licence MIT.
- Actions GitHub mises à jour (checkout v7, setup-python v7, cache v6, artefacts v7/v8, CodeQL v4, attestation v4).

## [6.0.0] - 2026-11-18

### Ajouté
- **La détection apprend de tes corrections** (Options avancées → 🧠) :
  - un visage ajouté à la main que le détecteur avait « presque » vu abaisse le seuil de détection ;
  - les zones supprimées entraînent un petit classifieur local qui marque les fausses détections « ? » ;
  - bouton « Enlever les « ? » ».
- Scores de confiance de 0 à 1, pour la détection IA comme pour la détection de secours.
- Numéro de version dans le titre, bouton « ⓘ À propos » et propriétés Windows du `.exe`.

### Sécurité
- Le seuil appris ne peut que baisser (on trouve plus de visages, jamais moins). Le classifieur ne cache jamais une zone tout seul.
- Seuls des nombres sont stockés (score, taille relative, proportions) : aucune image, position ni nom de fichier. Rien n'est écrit en mode terrain.

### Corrigé
- Le faux positif sur un casque, avec la détection de secours, n'apparaît plus au réglage par défaut.

## [5.0.0] - 2026-11-18

### Sécurité
- **Visage collé au bord de la photo** : l'ellipse, le rond flou et l'émoji laissaient jusqu'à 12 % du visage visible. Corrigé, avec des tests sur tous les bords.
- **PNG transparents** : les couleurs cachées sous les pixels 100 % transparents sont effacées.
- Alerte bloquante si le modèle IA a été modifié (empreinte incorrecte).
- Mode terrain mémorisé : il bloque aussi la marge et refuse d'enregistrer sans détection IA.

### Corrigé
- Suppr supprime la zone sélectionnée, plus la photo. Retirer une photo de la liste demande confirmation.
- Une zone supprimée ne revient pas après une nouvelle détection. Le ✔ est retiré si les zones changent.
- Les photos dont le nom contient « anonyme » ne sont plus ignorées. Le nombre de photos ignorées s'affiche.
- La fenêtre tient dans sa largeur minimale. Les flèches ne bougent plus les curseurs.
- L'export ne peut plus rester bloqué. Un ré-export remplace l'ancienne sortie. Les originaux effacés sont retirés de la liste.
- L'auto-test est plus complet et `build.bat` échoue si l'auto-test échoue.

### Ajouté
- Une croix ✕ sur chaque cadre, des zones douteuses marquées « ? », la consigne de dessin affichée sur la photo et un écran de fin de vérification.

## [4.0.0] - 2026-11-18

### Ajouté
- **Mode simple** (par défaut, toujours sûr) et **options avancées**.
- Styles : rond flou et émoji 🙂.
- Revue photo par photo (« Suivante ▶ » marque la photo ✔).
- Zones « objet » (badge, plaque, tatouage), mode terrain, effacement des originaux.
- Photos iPhone (HEIC).
- Tests automatiques (pytest), `.spec` PyInstaller unique, versions figées.

### Sécurité
- **Noms de sortie neutres** (`anonyme_0001.jpg`) : le nom d'origine contient souvent la date et l'heure.
- Modèle YuNet vérifié par son empreinte SHA-256. Modèles chargés en mémoire, ce qui marche avec un nom d'utilisateur accentué.
- Marge minimale sur les zones manuelles. L'ellipse couvre toujours tout le visage.

### Corrigé
- Fichiers de sortie écrasés et compteur faux, PNG 16 bits blancs, transparence perdue, double export, sélection de la liste perdue, images très allongées.

## [3.0.0] - 2026-11-18

### Ajouté
- Force de l'effet sur une vraie plage, de presque rien à totalement détruit (échelle logarithmique).
- Indicateur du niveau d'anonymat (vert / orange / rouge) et confirmation avant un export qui n'est pas sûr.
- Méthodes classiques, moins sûres et signalées : pixelisation sans bruit, flou gaussien, bandeau sur les yeux.

## [2.0.0] - 2026-11-17

### Ajouté
- Curseur de force pour la pixelisation et le flou destructifs.

## [1.0.0] - 2026-11-17

### Ajouté
- Première version : détection des visages (YuNet ou Haar), revue et correction manuelle.
- Caviardage, pixelisation et flou destructifs.
- Sauvegarde sans métadonnées, un seul `.exe` Windows.
