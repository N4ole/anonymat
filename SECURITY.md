# Sécurité

Ce logiciel sert à **protéger l'identité de personnes photographiées**. Une faille peut donc exposer quelqu'un.

## Signaler une faille

Une faille de sécurité, c'est par exemple :
- un visage (ou une partie) qui reste reconnaissable alors que le bandeau affiche 🔒 vert ;
- une métadonnée qui survit ;
- une fuite dans un fichier écrit par le programme ;
- un contournement de la vérification du modèle IA.

**N'ouvre pas d'issue publique.** Utilise plutôt
[« Report a vulnerability »](https://github.com/N4ole/anonymat/security/advisories/new) (onglet *Security* du dépôt).

**Ne joins jamais de vraie photo de personne.** Reproduis le problème avec une image de synthèse, un dessin ou une photo libre de droits, ou décris simplement les dimensions et la position de la zone.

## Vérifier un `.exe` téléchargé

Chaque release fournit :
- `SHA256SUMS.txt` : `Get-FileHash .\AnonymiseurVisages-X.Y.Z-windows-x64.exe -Algorithm SHA256` doit donner la même valeur ;
- une **attestation de provenance** : `gh attestation verify AnonymiseurVisages-X.Y.Z-windows-x64.exe -R N4ole/anonymat` prouve que le fichier a été compilé par la CI de ce dépôt, à partir de son code.

Le `.exe` n'est pas signé avec un certificat de code : Windows SmartScreen peut afficher un avertissement au premier lancement.

## Ce que le programme garantit, et ce qu'il ne garantit pas

Voir la section « Ce qui est garanti / ce qui ne l'est pas » du [README](README.md). En résumé :
- le programme ne se connecte jamais à Internet ;
- la détection automatique peut rater un visage : la vérification humaine reste indispensable.
