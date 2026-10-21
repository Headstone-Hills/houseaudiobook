# PyInstaller spec for the bundled engine (M9, D-054).
#
#   FFMPEG_DIR=/path/with/ffmpeg+ffprobe  pyinstaller engine/packaging/hillaudio-engine.spec \
#       --distpath build/engine --workpath build/pyinstaller --noconfirm
#
# A onedir build (a folder, not one self-extracting file): it starts in a second
# or two instead of unpacking hundreds of MB on every launch, and codesign can
# sign each library in place. The built UI goes in `web/`, ffmpeg/ffprobe in
# `bin/` (hillaudio.tools finds them there).
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]  # noqa: F821 (SPECPATH is set by PyInstaller)
WEB = ROOT / "web" / "dist"
if not (WEB / "index.html").is_file():
    raise SystemExit(f"build the UI first: (cd web && npm run build); no {WEB}/index.html")

datas = [(str(WEB), "web")]
datas += collect_data_files("faster_whisper")  # the VAD model asset
datas += collect_data_files("reportlab")  # fonts for the corrections-guide PDF
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
ffmpeg_dir = os.environ.get("FFMPEG_DIR")
if ffmpeg_dir:
    for tool in ("ffmpeg", "ffprobe"):
        p = Path(ffmpeg_dir) / tool
        if not p.is_file():
            raise SystemExit(f"FFMPEG_DIR has no {tool}: {p}")
        binaries.append((str(p), "bin"))
    # License texts and build configuration that come with our own build
    # (scripts/build_ffmpeg.sh): the LGPL asks for them to travel with the program.
    for extra in sorted(Path(ffmpeg_dir).glob("*.txt")):
        datas.append((str(extra), "bin"))
else:
    print("note: FFMPEG_DIR not set: the engine will use ffmpeg from PATH")

hidden = (
    collect_submodules("hillaudio")
    + collect_submodules("uvicorn")
    + ["faster_whisper", "ctranslate2", "tokenizers", "av", "soundfile", "reportlab.graphics.barcode"]
)

a = Analysis(  # noqa: F821
    [str(Path(SPECPATH) / "entry.py")],  # noqa: F821
    pathex=[str(ROOT / "engine")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "mypy", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="hillaudio-engine",
    console=True,  # stdout carries the READY line to the app
    codesign_identity=os.environ.get("APPLE_SIGNING_IDENTITY") or None,
    entitlements_file=str(ROOT / "shell" / "src-tauri" / "entitlements.plist"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="hillaudio-engine")  # noqa: F821
