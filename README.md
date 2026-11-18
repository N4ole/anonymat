# Anonymat – anonymiseur de visages

[![CI](https://github.com/N4ole/anonymat/actions/workflows/ci.yml/badge.svg)](https://github.com/N4ole/anonymat/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/N4ole/anonymat?label=t%C3%A9l%C3%A9charger)](https://github.com/N4ole/anonymat/releases/latest)
[![CodeQL](https://github.com/N4ole/anonymat/actions/workflows/codeql.yml/badge.svg)](https://github.com/N4ole/anonymat/actions/workflows/codeql.yml)
![Windows](https://img.shields.io/badge/Windows-un%20seul%20.exe-0a84ff)
![Hors ligne](https://img.shields.io/badge/r%C3%A9seau-jamais-1b7a34)
[![Licence MIT](https://img.shields.io/github/license/N4ole/anonymat)](LICENSE)

Logiciel Windows (un seul `.exe`, rien à installer) qui cache les visages sur des photos, avec un **vrai anonymat par défaut** : le visage est détruit, pas seulement flouté, et toutes les informations cachées des photos (GPS, appareil…) sont supprimées. Il fonctionne **entièrement hors ligne**.

## Télécharger

👉 **[Dernière version](https://github.com/N4ole/anonymat/releases/latest)** : télécharge `AnonymiseurVisages-X.Y.Z-windows-x64.exe` et lance-le.

Pour vérifier que le fichier est authentique (voir aussi [SECURITY.md](SECURITY.md)) :
```powershell
Get-FileHash .\AnonymiseurVisages-X.Y.Z-windows-x64.exe -Algorithm SHA256   # = valeur de SHA256SUMS.txt
gh attestation verify AnonymiseurVisages-X.Y.Z-windows-x64.exe -R N4ole/anonymat  # compilé par la CI de ce dépôt
```
Le `.exe` n'est pas signé avec un certificat : SmartScreen peut afficher « Windows a protégé votre ordinateur ». Clique alors sur *Informations complémentaires → Exécuter quand même*.

## Utilisation

**Mode simple (par défaut)** – choisis un style, ils sont tous réglés pour un anonymat total :
Flou (recommandé) · Rond flou · Pixels · Émoji 🙂 · Carré noir.

1. `➕ Photos…` / `📁 Dossier…`, ou glisser les photos sur l'icône du .exe (JPEG, PNG, WebP, TIFF, BMP, GIF, **HEIC iPhone**).
2. Pour chaque photo, vérifie les cadres numérotés puis clique **Suivante ▶** (la photo est marquée ✔). À la dernière, le programme propose d'enregistrer, ou t'emmène aux photos pas encore vérifiées :
   - `➕ Visage oublié` puis dessine un rectangle (la consigne s'affiche sur la photo) ;
   - `🏷 Autre chose à cacher` pour un badge, un nom brodé, une plaque, un tatouage… ;
   - la petite **✕** en haut à droite d'un cadre le supprime (ou : clic sur le cadre puis Suppr / clic droit) ;
   - une zone marquée **« ? »** est probablement une fausse détection : vérifie-la.
   Une zone supprimée ne revient pas si la détection est relancée ; si la détection change les zones d'une photo déjà vérifiée, son ✔ est retiré.
3. `✔ Anonymiser et enregistrer`. Les copies vont dans un dossier `anonymise\` et s'appellent `anonyme_0001.jpg`, `anonyme_0002.jpg`… Rien n'est jamais écrasé (un nouvel enregistrement de la même photo remplace seulement sa version précédente) ; les originaux ne sont pas modifiés. Les photos déjà anonymisées sont ignorées à l'import, et leur nombre est affiché.

**⚙ Options avancées** : force de l'effet, marge, forme, sensibilité (re-détection automatique), méthodes classiques moins sûres, mode terrain, noms d'origine, effacement des originaux.

Un bandeau indique en permanence le niveau de protection : 🔒 vert = anonymat total, ⚠ orange = reconnaissable par un proche ou une IA, ⛔ rouge = réversible / insuffisant. Une confirmation est demandée avant d'enregistrer un réglage non vert, des photos non vérifiées, ou d'effacer des originaux.

| Méthode | Sûre ? |
|---|---|
| 🔒 Carré noir, Émoji | Toujours (visage entièrement recouvert) |
| 🔒 Flou, Rond flou, Pixels (destructifs) | À partir d'environ 54 % de force (≤ 12 blocs) — toujours en mode simple |
| ⚠ Pixelisation classique (sans bruit) | Au mieux « moyen » |
| ⚠ Flou gaussien classique | Jamais : peut être en partie annulé |
| ⛔ Bandeau sur les yeux | Jamais : visage reconnaissable |

**Mode terrain** (*Méthodes sûres uniquement*) : ne propose que les méthodes 🔒, bloque la force et la marge au-dessus du seuil, et **refuse d'enregistrer sans la détection IA**. Ce réglage (et l'affichage des options avancées) est mémorisé dans `%APPDATA%\AnonymiseurVisages\reglages.json` — aucun nom de photo ni de dossier n'y est écrit.

## 🧠 La détection s'améliore avec tes corrections

Dans **⚙ Options avancées → 🧠 Apprendre de mes corrections** (activé par défaut) :

| Ce que tu fais | Ce que le programme apprend |
|---|---|
| Tu ajoutes un visage oublié | Il regarde si le détecteur l'avait « presque » vu. Si oui, il **abaisse son seuil** la fois suivante pour trouver ce genre de visage. |
| Tu supprimes une zone détectée | Il apprend à quoi ressemblent **tes** fausses détections et les marque **« ? »** ; le bouton *Enlever les « ? »* les retire d'un coup. |
| Tu enregistres une photo vérifiée ✔ | Les zones gardées comptent comme de vrais visages. |

Garde-fous :
- le seuil appris ne peut que **descendre** (plus de visages trouvés, jamais moins), avec un plancher ;
- le classifieur ne fait que **signaler** : il ne cache jamais une zone tout seul ;
- seuls des **nombres** sont gardés (score de confiance, taille relative, proportions, détecteur) dans `%APPDATA%\AnonymiseurVisages\apprentissage.json` (3 000 corrections max) : jamais d'image, de position, de nom de fichier ni de date ;
- **en mode terrain, rien n'est enregistré** ; *Oublier l'apprentissage* efface tout.

Pourquoi pas un vrai ré-entraînement du réseau de neurones ? Il faudrait conserver des photos de visages (contraire au but du logiciel), embarquer PyTorch (plusieurs centaines de Mo) et des heures de calcul. La couche légère ci-dessus apporte l'essentiel du gain sur l'usage réel de chacun.

## Ce qui est garanti / ce qui ne l'est pas

- ✅ Le visage est détruit (réduction à quelques blocs + bruit aléatoire non reproductible), pas seulement flouté.
- ✅ Aucune métadonnée recopiée (EXIF, GPS, appareil, XMP, miniature EXIF), écriture atomique.
- ✅ Noms neutres par défaut (le nom d'origine contient souvent date et heure).
- ✅ L'ellipse, le rond flou et l'émoji sont agrandis pour recouvrir tout le cadre du visage, **y compris collé au bord de la photo** ; les zones manuelles ont une marge minimale.
- ✅ PNG/WebP transparents : les couleurs cachées sous les pixels transparents sont effacées.
- ✅ Si le modèle IA a été modifié (empreinte incorrecte), alerte bloquante : le programme a peut-être été altéré.
- ✅ Modèles chargés depuis la mémoire : fonctionne même si le nom d'utilisateur Windows contient des accents. Si aucun détecteur ne se charge, une alerte bloquante l'indique.
- ⚠ Le bandeau vert juge la méthode, pas la photo : un badge ou un nom visible reste à masquer à la main.
- ⚠ La détection automatique peut rater un visage (de dos, masqué, minuscule) : la revue photo par photo reste indispensable. Sans le modèle IA (détection basique), un bandeau rouge le signale.
- ⚠ L'effacement des originaux écrase puis supprime le fichier, mais sur SSD, clé USB, cloud (OneDrive…) ou sauvegardes, des copies peuvent subsister ; Windows garde aussi des miniatures en cache.
- ✖ Pas encore géré : vidéo, voix, détection automatique des plaques et tatouages (à masquer à la main).

## Compiler soi-même

Sur ton PC (Python 3.10+) : double-clic sur `build.bat`. Le script :
1. installe les dépendances (versions figées) ;
2. télécharge le modèle d'IA **YuNet** et **vérifie son empreinte SHA-256**. Un fichier modifié est refusé ; sans modèle, il demande confirmation ;
3. lance les tests ;
4. compile `dist\AnonymiseurVisages.exe` ;
5. lance l'auto-test du `.exe`, et s'arrête s'il échoue.

Si le téléchargement est bloqué, place `face_detection_yunet_2023mar.onnx` (OpenCV Zoo) dans `models\` à la main ; il sera vérifié de la même façon.

### Automatisation GitHub

Le projet se maintient tout seul :

| Workflow | Quand | Ce qu'il fait |
|---|---|---|
| **CI** | chaque push / pull request, et chaque mois | lint (ruff), tests Linux, compilation et auto-test du `.exe` sous Windows, répétition de la release |
| **Dependabot** | chaque semaine | propose les mises à jour des dépendances Python |
| **Fusion auto** | quand la CI d'une PR Dependabot est verte | fusionne la PR (si elle ne touche que `requirements.txt`) et lance une release correctrice |
| **Release** | changement de `version.py`, étiquette `vX.Y.Z`, mise à jour fusionnée, ou chaque semaine | publie la release : `.exe`, `SHA256SUMS.txt`, attestation de provenance, notes tirées du [CHANGELOG](CHANGELOG.md). Pour une mise à jour de dépendance, la version correctrice (6.0.1 → 6.0.2…) et son entrée de CHANGELOG sont générées automatiquement |
| **CodeQL** | chaque push + chaque semaine | analyse de sécurité du code |

En cas d'échec (casse due à GitHub ou à une dépendance), GitHub envoie un e-mail. Une mise à jour qui casse les tests n'est jamais fusionnée.

Publier une version : voir [CONTRIBUTING.md](CONTRIBUTING.md#publier-une-version).

## Fichiers

- `learning.py` – apprentissage local à partir des corrections
- `core.py` – détection (scores de confiance 0–1), anonymisation, noms de sortie, sauvegarde sans métadonnées, effacement
- `app.py` – interface Tkinter (+ `--selftest` pour la CI)
- `version.py` – numéro de version unique (appli, .exe, release)
- `tests/` – tests pytest (un par bug ou exigence relevés en revue)
- `tools_model.py` – téléchargement + vérification du modèle YuNet
- `AnonymiseurVisages.spec`, `build.bat` – compilation ; `.github/` – CI, release, CodeQL, Dependabot, modèles d'issues
- `haar/` – détecteur de secours (OpenCV 4.x)

## Licence

[MIT](LICENSE) : libre d'utilisation, de modification et de redistribution. Les composants intégrés au `.exe` (OpenCV, YuNet, libheif…) gardent leur propre licence : voir [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
