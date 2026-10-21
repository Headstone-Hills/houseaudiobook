#!/usr/bin/env bash
# Build static ffmpeg + ffprobe for bundling (D-056): LGPL only (no --enable-gpl,
# no nonfree), with libmp3lame for MP3 export, nothing else external.
#
#   scripts/build_ffmpeg.sh OUT_DIR
#
# Sources are verified before use: LAME by a pinned SHA-256, FFmpeg by its
# release signature from the key whose fingerprint is pinned below (the one
# ffmpeg.org/download.html lists). FFMPEG_TARBALL, FFMPEG_SIG and FFMPEG_KEY
# can point at local copies (same checks). Works on macOS and Linux.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(mkdir -p "$1" && cd "$1" && pwd)"
FFMPEG_VERSION="${FFMPEG_VERSION:-8.0}"
FFMPEG_FPR="FCF986EA15E6E293A5644F10B4322F04D67658D8"  # FFmpeg release signing key
LAME_URL="https://downloads.sourceforge.net/project/lame/lame/3.100/lame-3.100.tar.gz"
LAME_SHA256="ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e"
WORK="$ROOT/build/ffmpeg-work"
PREFIX="$WORK/prefix"
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
rm -rf "$WORK" && mkdir -p "$WORK" "$PREFIX"
cd "$WORK"

sha256() { python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }
MIN=""
[[ "$(uname)" == "Darwin" ]] && MIN="-mmacosx-version-min=12.0"

# 1. LAME (LGPL-2.0+), static library only.
curl -fsSL --retry 3 "$LAME_URL" -o lame.tar.gz
[[ "$(sha256 lame.tar.gz)" == "$LAME_SHA256" ]] || { echo "error: LAME checksum mismatch"; exit 1; }
tar xzf lame.tar.gz
(cd lame-3.100 \
  && CFLAGS="-O2 $MIN" LDFLAGS="$MIN" ./configure --prefix="$PREFIX" --disable-shared --enable-static \
       --disable-frontend --disable-dependency-tracking >/dev/null \
  && make -j"$JOBS" >/dev/null 2>&1 && make install >/dev/null 2>&1)

# 2. FFmpeg source, signature-checked against the pinned key.
TARBALL="${FFMPEG_TARBALL:-}"
if [[ -z "$TARBALL" ]]; then
  TARBALL="$WORK/ffmpeg-$FFMPEG_VERSION.tar.xz"
  curl -fsSL --retry 3 "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" -o "$TARBALL"
  curl -fsSL --retry 3 "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz.asc" -o "$TARBALL.asc"
fi
SIG="${FFMPEG_SIG:-$TARBALL.asc}"
KEY="${FFMPEG_KEY:-$WORK/ffmpeg-devel.asc}"
[[ -f "$KEY" ]] || curl -fsSL --retry 3 "https://ffmpeg.org/ffmpeg-devel.asc" -o "$KEY"
export GNUPGHOME="$WORK/gnupg"
mkdir -m 700 "$GNUPGHOME"
gpg --batch --quiet --import "$KEY"
gpg --batch --with-colons --fingerprint | grep -q "^fpr:::::::::$FFMPEG_FPR:" \
  || { echo "error: the FFmpeg key isn't $FFMPEG_FPR"; exit 1; }
gpg --batch --status-fd 1 --verify "$SIG" "$TARBALL" 2>/dev/null | grep -q "VALIDSIG $FFMPEG_FPR" \
  || { echo "error: bad FFmpeg signature"; exit 1; }
echo "verified $(basename "$TARBALL") (signed by $FFMPEG_FPR)"
mkdir src && tar xJf "$TARBALL" -C src --strip-components 1

# 3. Configure: audio tools only need the defaults minus anything external.
ASM=""
command -v nasm >/dev/null || [[ "$(uname -m)" == "arm64" ]] || ASM="--disable-x86asm"
(cd src && ./configure --prefix="$PREFIX" \
    --disable-autodetect --disable-doc --disable-debug --disable-ffplay --disable-network \
    --enable-static --disable-shared --enable-libmp3lame $ASM \
    --extra-cflags="-I$PREFIX/include $MIN" --extra-ldflags="-L$PREFIX/lib $MIN" \
    >"$WORK/configure.log" || { tail -30 "$WORK/configure.log"; exit 1; })
grep -q "^License: LGPL" "$WORK/configure.log" \
  || { echo "error: not an LGPL configuration"; grep "^License" "$WORK/configure.log"; exit 1; }
(cd src && make -j"$JOBS" >"$WORK/make.log" 2>&1 && make install >>"$WORK/make.log" 2>&1) \
  || { tail -30 "$WORK/make.log"; exit 1; }

# 4. The two programs, plus their license texts and the exact configuration.
cp "$PREFIX/bin/ffmpeg" "$PREFIX/bin/ffprobe" "$OUT/"
cp src/COPYING.LGPLv2.1 "$OUT/LICENSE-ffmpeg.txt"
cp lame-3.100/COPYING "$OUT/LICENSE-lame.txt"
"$OUT/ffmpeg" -hide_banner -buildconf >"$OUT/BUILDCONF-ffmpeg.txt"

# 5. The corresponding source, published with each release (the LGPL's source
#    offer, open question 11): the verified tarballs, the signature and this script.
#    Not bundled in the app (the engine spec takes only the programs and *.txt).
mkdir -p "$OUT/source"
cp "$TARBALL" "$SIG" "$WORK/lame.tar.gz" "$ROOT/scripts/build_ffmpeg.sh" "$OUT/source/"
mv "$OUT/source/lame.tar.gz" "$OUT/source/lame-3.100.tar.gz"
echo "$("$OUT/ffmpeg" -hide_banner -version | head -1) (LGPL, libmp3lame) -> $OUT"
