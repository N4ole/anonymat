# Contribuer

## Installer l'environnement de développement

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt ruff==0.17.5
python tools_model.py        # modèle IA YuNet (empreinte vérifiée)
python app.py                # lancer l'appli sans compiler
```

## Avant chaque commit

```powershell
ruff check .                 # style et erreurs courantes
python -m pytest             # tests
```

La CI (onglet *Actions*) refait la même chose, puis compile et teste le `.exe` sous Windows.

Règles du projet :
- **Ne jamais affaiblir la sécurité en silence.** Une méthode « 🔒 » doit recouvrir 100 % du cadre du visage, y compris au bord de l'image. Chaque bug d'anonymat corrigé reçoit un test dans `tests/`.
- Aucune connexion réseau dans l'appli.
- **Aucune vraie photo de personne** dans le dépôt, les tests ou les issues : uniquement des images générées.

## Publier une version

**Rien à faire pour les mises à jour de sécurité** : Dependabot propose la mise à jour, la CI la teste, elle est fusionnée automatiquement, puis une version correctrice est publiée avec son entrée de CHANGELOG.

Pour une nouvelle fonctionnalité :
1. Augmenter `__version__` dans `version.py` (ex. `6.1.0`).
2. Ajouter la section `## [6.1.0] - AAAA-MM-JJ` dans `CHANGELOG.md`.
3. Committer et pousser sur `main`.

Le workflow **Release** compile le `.exe`, publie la release `v6.1.0` avec `SHA256SUMS.txt`, une attestation de provenance et les notes du CHANGELOG. Il s'arrête si la section du CHANGELOG manque.

Les actions GitHub sont épinglées sur leur version majeure (`@v7`…), qui reçoit les correctifs automatiquement. Un passage de version majeure se fait à la main, car un bot ne peut pas fusionner une PR qui modifie des workflows.
