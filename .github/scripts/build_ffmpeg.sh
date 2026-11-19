#!/usr/bin/env bash
# ffmpeg.exe MINIMAL pour Anonymat (voix déformée des vidéos), compilé depuis les sources
# officielles (empreinte figée) avec mingw-w64, sous Linux (CI).
#
# Seulement ce qui sert : lire la piste son des vidéos courantes, écrire du PCM brut,
# remonter vidéo (copiée) + son AAC en MP4. Aucune bibliothèque externe, aucun encodeur
# vidéo : licence LGPL-2.1 (le script échoue si la configuration devient GPL ou nonfree).
#
# Usage : build_ffmpeg.sh <dossier de sortie>   -> <dossier>/ffmpeg.exe, ffmpeg.exe.sha256
set -euo pipefail

VERSION=9.0.2
SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e
OUT=$(realpath -m "${1:?dossier de sortie}")
WORK=$(mktemp -d)
mkdir -p "$OUT"

cd "$WORK"
curl -sSfL -o src.tar.xz "https://ffmpeg.org/releases/ffmpeg-$VERSION.tar.xz"
echo "$SHA256  src.tar.xz" | sha256sum -c -
tar -xJf src.tar.xz
cd "ffmpeg-$VERSION"

./configure \
  --target-os=mingw32 --arch=x86_64 --cross-prefix=x86_64-w64-mingw32- --enable-cross-compile \
  --enable-static --disable-shared --disable-debug --enable-small \
  --disable-autodetect --disable-everything --disable-network --disable-doc --disable-x86asm \
  --disable-ffplay --disable-ffprobe --disable-avdevice --disable-swscale \
  --enable-ffmpeg \
  --enable-protocol=file,pipe \
  --enable-demuxer=mov,matroska,avi,mpegts,mpegps,asf,ogg,wav,mp3,aac,flac,amr,pcm_f32le \
  --enable-decoder=aac,aac_fixed,aac_latm,mp3,mp3float,mp2,mp2float,opus,vorbis,flac,alac,ac3,eac3 \
  --enable-decoder=amrnb,amrwb,wmav1,wmav2,adpcm_ima_qt \
  --enable-decoder=pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be,pcm_s32le,pcm_f32le,pcm_u8,pcm_alaw,pcm_mulaw \
  --enable-parser=aac,aac_latm,mpegaudio,opus,vorbis,flac,ac3 \
  --enable-encoder=aac,pcm_f32le \
  --enable-muxer=mp4,pcm_f32le,null \
  --enable-filter=aresample,aformat,anull,atrim \
  --extra-ldflags="-static -static-libgcc"

# Garde-fou licence : jamais de GPL ni de nonfree dans le .exe (licence MIT du projet).
grep -q '#define CONFIG_GPL 0' config.h
grep -q '#define CONFIG_NONFREE 0' config.h
grep -q '#define CONFIG_VERSION3 0' config.h

make -j"$(nproc)" ffmpeg.exe
x86_64-w64-mingw32-strip ffmpeg.exe
cp ffmpeg.exe "$OUT/ffmpeg.exe"
cp COPYING.LGPLv2.1 "$OUT/LICENSE-ffmpeg.txt"
(cd "$OUT" && sha256sum ffmpeg.exe | cut -d' ' -f1 > ffmpeg.exe.sha256)
ls -la "$OUT"
cat "$OUT/ffmpeg.exe.sha256"
