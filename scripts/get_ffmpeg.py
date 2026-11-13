"""CI: bu işletim sistemi için ffmpeg + ffprobe indirir, build/bin altına koyar ve doğrular."""
import io
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "build" / "bin"
OUT.mkdir(parents=True, exist_ok=True)
EXE = ".exe" if os.name == "nt" else ""


def fetch(url):
    print("indiriliyor:", url, flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "teslim-paketi-ci"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def extract(data, names):
    """Zip içinden adı eşleşen dosyaları (klasörden bağımsız) OUT'a çıkarır."""
    got = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            base = info.filename.rsplit("/", 1)[-1]
            if base in names:
                dst = OUT / base
                dst.write_bytes(z.read(info))
                dst.chmod(dst.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                got.append(base)
    return got


def windows():
    names = {"ffmpeg.exe", "ffprobe.exe"}
    for url in ("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
                "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"):
        try:
            if names <= set(extract(fetch(url), names)):
                return True
        except Exception as e:  # noqa: BLE001
            print("  olmadı:", e)
    return False


def mac():
    arch = "arm64" if platform.machine() == "arm64" else "amd64"
    try:
        for tool in ("ffmpeg", "ffprobe"):
            extract(fetch(f"https://ffmpeg.martin-riedl.de/redirect/latest/macos/{arch}/release/{tool}.zip"), {tool})
        if (OUT / "ffmpeg").exists() and (OUT / "ffprobe").exists():
            return True
    except Exception as e:  # noqa: BLE001
        print("  olmadı:", e)
    # yedek: static-ffmpeg paketi
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "static-ffmpeg"], check=True)
        from static_ffmpeg import run  # type: ignore
        ff, fp = run.get_or_fetch_platform_executables_else_raise()
        shutil.copy(ff, OUT / "ffmpeg")
        shutil.copy(fp, OUT / "ffprobe")
        for t in ("ffmpeg", "ffprobe"):
            (OUT / t).chmod(0o755)
        return True
    except Exception as e:  # noqa: BLE001
        print("  olmadı:", e)
    return False


ok = windows() if os.name == "nt" else mac()
if not ok:
    sys.exit("ffmpeg indirilemedi")
for t in ("ffmpeg", "ffprobe"):
    out = subprocess.run([str(OUT / (t + EXE)), "-hide_banner", "-version"], capture_output=True, text=True)
    print(out.stdout.splitlines()[0] if out.stdout else out.stderr)
filters = subprocess.run([str(OUT / ("ffmpeg" + EXE)), "-hide_banner", "-filters"], capture_output=True, text=True).stdout
for f in ("perspective", "loudnorm", "gblur", "crop", "scale", "pad", "overlay", "tile"):
    print(f"filtre {f}:", "var" if f" {f} " in filters else "YOK")
    if f" {f} " not in filters:
        sys.exit(f"ffmpeg'de gerekli filtre yok: {f}")
