# Anonymiseur de visages

Petit logiciel Windows (un seul `.exe`) qui détecte les visages sur des photos et les rend **irrécupérables**.

## Compiler le .exe

**Option A – sur ton PC (Python 3.10+ installé)** : double-clic sur `build.bat`.
→ résultat : `dist\AnonymiseurVisages.exe` (~60-90 Mo, rien à installer pour l'utilisateur final).

**Option B – sans Python** : pousse le dossier sur un dépôt GitHub → onglet *Actions* → *Build exe* → télécharge l'artefact.

Le script télécharge le modèle d'IA **YuNet** (OpenCV Zoo, ~230 Ko) et l'intègre dans le .exe.
Sans lui, le programme se rabat sur la détection Haar (plus ancienne, rate davantage les profils / petits visages).

## Utilisation

1. `➕ Images…` / `📁 Dossier…`, ou glisser des fichiers/dossiers **sur l'icône du .exe**.
2. Les visages détectés sont encadrés en rouge. **Vérifie chaque image** :
   - glisser à la souris → ajouter une zone (bleu) ;
   - clic droit → supprimer une zone (faux positif) ;
   - ◀ ▶ → image suivante / précédente ; `Suppr` → retirer l'image de la liste.
3. `Aperçu du résultat` pour voir le rendu, puis `✔ Anonymiser et enregistrer`.

Les copies vont dans un sous-dossier `anonymise\` (`photo_anonyme.jpg`). **Les originaux ne sont jamais modifiés** — pense à les supprimer toi-même si besoin.

## Pourquoi c'est irréversible

| Risque | Parade |
|---|---|
| Un flou gaussien classique peut être partiellement inversé (déconvolution, IA) | La zone est d'abord **écrasée à ~6×6 pixels + bruit aléatoire** : l'information d'origine est détruite, le flou n'est qu'esthétique. Le mode *Caviardage* (noir plein) ne garde rien du tout. |
| La **miniature EXIF** d'une photo contient souvent l'image d'origine non floutée | L'image est reconstruite à partir des pixels : **aucune métadonnée** (EXIF, GPS, appareil, XMP, miniature) n'est recopiée. |
| Cadre trop serré (cheveux, oreilles, menton visibles) | Marge réglable, +30 % par défaut (plus en haut pour les cheveux). |
| Visage raté par la détection | Revue manuelle image par image + ajout de zones à la main. |

Limites : la détection automatique n'est jamais parfaite (visages de dos, masqués, très petits ou flous). Un tatouage, une plaque, un badge ou une silhouette reconnaissable ne sont pas traités → ajoute des zones à la main.

## Fichiers

- `core.py` – détection (YuNet multi-échelle / Haar) + anonymisation + sauvegarde sans métadonnées
- `app.py` – interface Tkinter
- `haar/` – cascades Haar de secours (OpenCV 4.x)
- `build.bat`, `.github/workflows/build.yml` – compilation
