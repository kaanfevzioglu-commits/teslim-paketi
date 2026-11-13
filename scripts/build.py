"""PyInstaller ile uygulamayı derler (Windows: dist/TeslimPaketi, Mac: dist/Teslim Paketi.app)."""
import os
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "build" / "bin"
SEP = os.pathsep
IS_MAC = sys.platform == "darwin"
name = "Teslim Paketi" if IS_MAC else "TeslimPaketi"

args = [
    str(ROOT / "app" / "server.py"),
    "--noconfirm", "--clean", "--windowed",
    "--name", name,
    "--paths", str(ROOT / "app"),
    "--hidden-import", "takip",
    "--hidden-import", "gorus",
    "--add-data", f"{ROOT / 'app' / 'models'}{SEP}models",
    "--add-data", f"{ROOT / 'app' / 'index.html'}{SEP}.",
    "--add-data", f"{ROOT / 'app' / 'kadraj.swift'}{SEP}.",
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build" / "pyi"),
    "--specpath", str(ROOT / "build"),
]
exe = ".exe" if os.name == "nt" else ""
for tool in ("ffmpeg", "ffprobe"):
    args += ["--add-binary", f"{BIN / (tool + exe)}{SEP}bin"]
if IS_MAC:
    args += ["--icon", str(ROOT / "assets" / "AppIcon.icns"), "--osx-bundle-identifier", "studio.teslimpaketi.app"]
    if (BIN / "kadraj").exists():
        args += ["--add-binary", f"{BIN / 'kadraj'}{SEP}bin"]
else:
    args += ["--icon", str(ROOT / "assets" / "TeslimPaketi.ico")]
print(" ".join(args))
PyInstaller.__main__.run(args)
