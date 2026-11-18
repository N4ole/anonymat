# Anonymiseur de visages

Logiciel Windows (un seul `.exe`) qui cache les visages sur des photos, avec un vrai anonymat par défaut.

## Compiler le .exe

**Option A – sur ton PC (Python 3.10+)** : double-clic sur `build.bat`. Le script :
1. installe les dépendances (versions figées dans `requirements.txt`) ;
2. télécharge le modèle d'IA **YuNet** et **vérifie son empreinte SHA-256** (un fichier corrompu ou modifié est refusé). Sans modèle, il demande confirmation avant de compiler une version « détection basique » ;
3. lance les tests automatiques (`tests/`) ;
4. compile avec `AnonymiseurVisages.spec` → `dist\AnonymiseurVisages.exe` ;
5. lance l'auto-test du .exe.

Si le téléchargement est bloqué : place `face_detection_yunet_2023mar.onnx` (OpenCV Zoo) dans `models\` à la main ; il sera vérifié de la même façon.

**Option B – sans Python** : pousse le dossier sur GitHub → onglet *Actions* → *Build exe*. La CI échoue si le modèle IA est absent, si un test échoue, ou si le .exe ne démarre pas depuis un dossier au nom accentué.

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

## Fichiers

- `core.py` – détection, anonymisation, noms de sortie, sauvegarde sans métadonnées, effacement
- `app.py` – interface Tkinter (+ `--selftest` pour la CI)
- `tests/` – tests pytest (un par bug ou exigence relevés en revue)
- `tools_model.py` – téléchargement + vérification du modèle YuNet
- `AnonymiseurVisages.spec`, `build.bat`, `.github/workflows/build.yml` – compilation
- `haar/` – détecteur de secours (OpenCV 4.x)
