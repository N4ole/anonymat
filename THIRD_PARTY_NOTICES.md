# Composants tiers

Le code de ce dépôt est sous licence [MIT](LICENSE). Le `.exe` distribué contient aussi les composants suivants, chacun sous sa propre licence :

| Composant | Rôle | Licence |
|---|---|---|
| [OpenCV](https://github.com/opencv/opencv) (`opencv-python-headless`) | Détection et traitement d'image | Apache 2.0 |
| [FFmpeg](https://ffmpeg.org) (`opencv_videoio_ffmpeg*.dll`, fournie par OpenCV) | Lecture et écriture des vidéos | **LGPL-2.1** |
| [FFmpeg](https://ffmpeg.org) 9.0.2 (`ffmpeg/ffmpeg.exe`, build minimale, voir ci-dessous) | Son des vidéos (voix déformée) | **LGPL-2.1** |
| [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) (`face_detection_yunet_2023mar.onnx`) | Modèle IA de détection des visages | MIT, © Shiqi Yu |
| Cascades Haar d'OpenCV (`haar/*.xml`) | Détection de secours | Intel License Agreement (type BSD), © Intel Corporation, licence incluse dans chaque fichier |
| [NumPy](https://numpy.org) | Calcul | BSD-3-Clause |
| [Pillow](https://python-pillow.org) | Lecture et écriture des images | MIT-CMU (HPND) |
| [pi-heif](https://github.com/bigcat88/pillow_heif) | Lecture des photos HEIC (iPhone) | BSD-3-Clause |
| [libheif](https://github.com/strukturag/libheif), [libde265](https://github.com/strukturag/libde265) (DLL fournies par pi-heif) | Décodage HEIC | **LGPL-3.0** |
| [Python](https://www.python.org) et Tcl/Tk | Interpréteur et interface | PSF License, licence Tcl/Tk (type BSD) |
| [PyInstaller](https://pyinstaller.org) (chargeur du `.exe`) | Création du `.exe` | GPL-2.0 avec exception pour le chargeur, qui permet toute licence au programme |

## LGPL (libheif, libde265, FFmpeg)

Ces bibliothèques sont utilisées sans modification, par liaison dynamique (DLL chargées à l'exécution) ou, pour `ffmpeg.exe`, comme programme séparé appelé par l'application. Leur code source est disponible aux liens ci-dessus. Pour utiliser une autre version de ces bibliothèques, recompile le programme à partir de ce dépôt avec `build.bat` (voir le [README](README.md)).

`ffmpeg.exe` est compilé par la CI de ce dépôt à partir des [sources officielles de FFmpeg 9.0.2](https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz) (empreinte SHA-256 vérifiée), sans aucune modification, avec le script [`.github/scripts/build_ffmpeg.sh`](.github/scripts/build_ffmpeg.sh) (workflow `ffmpeg`, attestation de provenance, binaire publié dans la pré-release `ffmpeg-9.0.2-1` du dépôt) : il ne contient que la lecture du son, l'encodeur AAC et le format MP4, sans aucune bibliothèque externe. Le script échoue si la configuration devient GPL, « nonfree » ou LGPL-3. Pour utiliser une autre version, modifie ce script et recompile.

Le programme n'embarque volontairement **aucun encodeur x265 ni x264 (GPL)** : il ne fait que lire les HEIC, et les photos HEIC sont enregistrées en JPEG. Les vidéos sont écrites avec l'encodeur MPEG-4 (`mp4v`) de la build LGPL de FFmpeg fournie par OpenCV.
