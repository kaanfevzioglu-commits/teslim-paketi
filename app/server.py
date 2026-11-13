#!/usr/bin/env python3
"""Teslim Paketi — sürükle bırak teslim aracı (yerel, ffmpeg ile).

Sadece Python standart kütüphanesi kullanır. Dosyalar bilgisayardan çıkmaz.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import socket
import tempfile
import threading
import time
import unicodedata
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Paketlenmiş uygulamada (PyInstaller) dosyalar _MEIPASS altında durur
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(APP_DIR))
import takip  # noqa: E402

VERSION = "2.1.1"
PORT = int(os.environ.get("TESLIM_PORT", "8765"))
IS_MAC, IS_WIN = sys.platform == "darwin", os.name == "nt"
HOME = Path.home()
MOVIES = HOME / ("Videos" if IS_WIN else "Movies")
OUTPUT_ROOT = Path(os.environ.get("TESLIM_OUT", MOVIES / "Teslim Paketi"))
if IS_MAC:
    DATA_DIR = HOME / "Library" / "Application Support" / "Teslim Paketi"
elif IS_WIN:
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local")) / "Teslim Paketi"
else:
    DATA_DIR = HOME / ".local" / "share" / "teslim-paketi"
DATA_DIR = Path(os.environ.get("TESLIM_DATA", DATA_DIR))
WORK_ROOT = Path(tempfile.gettempdir()) / "teslim-paketi-gelen"
PREFS_FILE = DATA_DIR / "ayarlar.json"
TXT = {"text": True, "encoding": "utf-8", "errors": "replace"}  # Windows'ta Türkçe dosya adları için

_extra = ["/opt/homebrew/bin", "/usr/local/bin", str(APP_DIR / "bin"), str(DATA_DIR / "bin")]
if IS_WIN:  # winget ile kurulan ffmpeg: Links klasörü ya da paket klasörü
    _la = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
    _extra += [str(_la / "Microsoft" / "WinGet" / "Links")]
    _extra += [str(p) for p in sorted((_la / "Microsoft" / "WinGet" / "Packages").glob("Gyan.FFmpeg*/*/bin"))]
    _extra += [r"C:\ffmpeg\bin", str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin")]
os.environ["PATH"] = os.pathsep.join(_extra + [os.environ.get("PATH", "")])
if IS_WIN:
    _orig_popen_init = subprocess.Popen.__init__

    def _popen_no_window(self, *a, **k):  # ffmpeg için siyah pencere açılmasın
        k.setdefault("creationflags", 0x08000000)  # CREATE_NO_WINDOW
        _orig_popen_init(self, *a, **k)
    subprocess.Popen.__init__ = _popen_no_window

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

# anahtar: (etiket, açıklama, genişlik, yükseklik @1080 tabanı, grup, dosya etiketi)
FORMATS = {
    "9x16": ("9:16", "Reels / TikTok / Shorts", 1080, 1920, "sosyal", "9x16"),
    "4x5":  ("4:5", "Instagram akış", 1080, 1350, "sosyal", "4x5"),
    "1x1":  ("1:1", "Kare", 1080, 1080, "sosyal", "1x1"),
    "2x3":  ("2:3", "Pinterest / dikey poster", 1080, 1620, "sosyal", "2x3"),
    "3x4":  ("3:4", "Dikey klasik", 1080, 1440, "sosyal", "3x4"),
    "16x9": ("16:9", "YouTube / TV", 1920, 1080, "yatay", "16x9"),
    "4x3":  ("4:3", "Klasik TV", 1440, 1080, "yatay", "4x3"),
    "185":  ("1.85:1", "Sinema flat (DCI)", 1998, 1080, "sinema", "1.85-1"),
    "235":  ("2.35:1", "Sinema scope", 2048, 872, "sinema", "2.35-1"),
    "239":  ("2.39:1", "Sinema scope (DCI)", 2048, 858, "sinema", "2.39-1"),
}
RES = {"720": 2 / 3, "1080": 1.0, "2160": 2.0}

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
FONT = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)

JOBS = {}
JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------- yardımcılar
TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def slug(text, fallback="dosya"):
    text = (text or "").translate(TR_MAP)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9.]+", "-", text).strip("-.")
    return text or fallback


def ev(x):
    """En yakın çift sayı (video boyutları çift olmalı)."""
    return max(2, int(round(x / 2.0)) * 2)


def _has_filter(name):
    if not FFMPEG:
        return False
    out = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, **TXT).stdout
    return re.search(rf"\s{name}\s", out) is not None


HAS_DRAWTEXT = _has_filter("drawtext") and os.environ.get("TESLIM_NO_DRAWTEXT") != "1"


def probe(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, **TXT,
    )
    data = json.loads(out.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(data.get("format", {}).get("duration") or 0)
    if v and not dur:
        dur = float(v.get("duration") or 0)
    return {"video": v, "audio": a, "duration": dur}


def display_size(v):
    """Telefon videolarındaki döndürme bilgisini hesaba katarak ekrandaki boyut."""
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    try:
        rot = int(float((v.get("tags") or {}).get("rotate", 0)))
    except ValueError:
        pass
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                rot = int(float(sd["rotation"]))
            except ValueError:
                pass
    return (h, w) if abs(rot) % 180 == 90 else (w, h)


def frame_rate(v):
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            n, d = (v.get(key) or "0/1").split("/")
            fps = float(n) / float(d)
            if 1 <= fps <= 240:
                return fps
        except (ValueError, ZeroDivisionError):
            pass
    return 25.0


def dims(fmt, vw, vh, res="1080", letterbox=False):
    """Çıkış dosyası boyutu ve içindeki görüntü alanı: (out_w, out_h, img_w, img_h)."""
    if fmt == "orijinal":
        return ev(vw), ev(vh), ev(vw), ev(vh)
    k = RES.get(str(res), 1.0)
    _, _, w, h, group, _ = FORMATS[fmt]
    if group == "sinema" and letterbox:
        ratio = w / h
        ow, oh = ev(1920 * k), ev(1080 * k)
        return ow, oh, ow, ev(ow / ratio)
    return ev(w * k), ev(h * k), ev(w * k), ev(h * k)


def file_label(fmt, letterbox=False):
    if fmt == "orijinal":
        return "orijinal"
    lab = FORMATS[fmt][5]
    if letterbox and FORMATS[fmt][4] == "sinema":
        lab += "-bantli"
    return lab


def preview_size(w, h):
    return ev(w / 2), ev(h / 2)


def measure_loudness(path, target):
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn",
           "-af", f"loudnorm=I={target}:TP=-1.0:LRA=11:print_format=json", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, **TXT)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr, re.S)
    if not m:
        return None
    stats = json.loads(m.group(0))
    try:
        vals = {k: float(stats[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (KeyError, ValueError):
        return None
    if any(v != v or v in (float("inf"), float("-inf")) for v in vals.values()) or vals["input_i"] <= -70:
        return "sessiz"
    return stats


def audio_filter(stats, target):
    if not stats or stats == "sessiz":
        return f"loudnorm=I={target}:TP=-1.0:LRA=11"
    return (f"loudnorm=I={target}:TP=-1.0:LRA=11:"
            f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:linear=true")


def graph_blur(w, h):
    return (f"[0:v]split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"gblur=sigma=40,eq=brightness=-0.08[bgb];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"scale=trunc(iw/2)*2:trunc(ih/2)*2[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]")


def graph_crop(w, h):
    return f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[v0]"


def esc_drawtext(text):
    return (text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
            .replace("%", "\\%").replace(",", "\\,"))


def watermark_filters(text):
    """Yedek yol: ffmpeg'de drawtext varsa filigranı onunla çizer."""
    text = (text or "ONIZLEME").strip()[:24]
    t = esc_drawtext(text)
    # Windows yolundaki "C:" iki noktası ffmpeg'de ayırıcı sayılır: kaçış karakteriyle koru
    font = f"fontfile='{FONT.replace(chr(92), '/').replace(':', chr(92) + ':')}':" if FONT else ""
    small = f"min(w/3/({max(len(text), 6)}*0.95)\\,h/22)"
    parts = []
    for row in range(6):
        centers = [1 / 6, 3 / 6, 5 / 6] if row % 2 == 0 else [1 / 3, 2 / 3]
        for cx in centers:
            parts.append(f"drawtext={font}text='{t}':fontsize={small}:fontcolor=white@0.30:"
                         f"borderw=1:bordercolor=black@0.20:x=w*{cx:.3f}-tw/2:y=h*{(row + 0.5) / 6:.3f}-th/2")
    parts.append(f"drawtext={font}text='ÖNİZLEME':fontsize=min(w/7\\,h/6):fontcolor=white@0.35:"
                 f"borderw=2:bordercolor=black@0.22:x=(w-tw)/2:y=(h-th)/2")
    return ",".join(parts)


def run_ffmpeg(cmd, job, duration, step_label):
    full = [FFMPEG, "-hide_banner", "-y", "-progress", "pipe:1", "-nostats"] + cmd
    try:
        proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **TXT)
    except OSError as e:
        raise RuntimeError(f"{step_label}: ffmpeg başlatılamadı ({e}). Sürüm {VERSION}, komut uzunluğu "
                           f"{len(subprocess.list2cmdline(full))}, dosya seçeneği '{GRAPH_OPT['v'] or 'yok'}'") from e
    job["current"] = step_label
    job["step_pct"] = 0
    err_lines = []

    def read_err():
        for line in proc.stderr:
            err_lines.append(line)
            if len(err_lines) > 60:
                err_lines.pop(0)

    t = threading.Thread(target=read_err, daemon=True)
    t.start()
    for line in proc.stdout:
        if line.startswith("out_time_us=") and duration > 0:
            try:
                job["step_pct"] = max(0, min(100, int(line.split("=")[1]) / (duration * 1e6) * 100))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=2)
    if proc.returncode != 0:
        useful = [l for l in err_lines if re.search(r"error|invalid|no such|not found|failed|out of range", l, re.I)]
        raise RuntimeError(f"{step_label} başarısız:\n" + "".join((useful or err_lines)[-6:]))
    job["step_pct"] = 100


def safe_area_sheet(final_path, out_png, w, h, duration):
    """9:16 için yaklaşık Reels/TikTok arayüz alanlarını gösteren kontrol karesi."""
    top, bottom, right = int(h * 0.13), int(h * 0.20), int(w * 0.15)
    vf = (f"drawbox=x=0:y=0:w=iw:h={top}:color=red@0.35:t=fill,"
          f"drawbox=x=0:y=ih-{bottom}:w=iw:h={bottom}:color=red@0.35:t=fill,"
          f"drawbox=x=iw-{right}:y={top}:w={right}:h=ih-{top}-{bottom}:color=orange@0.30:t=fill")
    subprocess.run([FFMPEG, "-hide_banner", "-y", "-ss", f"{max(0.0, duration / 2):.2f}", "-i", str(final_path),
                    "-frames:v", "1", "-vf", vf, str(out_png)], capture_output=True)


def track_sheet(final_path, out_png, duration):
    """Takip sonucunu kontrol için 6 karelik şerit."""
    n = 6
    step = max(duration / n, 0.1)
    subprocess.run([FFMPEG, "-hide_banner", "-y", "-i", str(final_path), "-vf",
                    f"fps=1/{step:.3f},scale=-2:480,tile={n}x1:padding=6:color=black",
                    "-frames:v", "1", str(out_png)], capture_output=True)


def load_prefs():
    try:
        return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_prefs(p):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PREFS_FILE.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def folder_presets():
    cand = [("movies", "Videolar" if IS_WIN else "Filmler", OUTPUT_ROOT),
            ("desktop", "Masaüstü", HOME / "Desktop"),
            ("downloads", "İndirilenler", HOME / "Downloads"),
            ("documents", "Belgeler", HOME / "Documents")]
    return [{"key": k, "label": l, "path": str(p)} for k, l, p in cand
            if k == "movies" or p.exists()]


def pick_folder():
    """İşletim sisteminin kendi klasör seçme penceresi. İptal: None."""
    try:
        if IS_MAC:
            r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt '
                                '"Teslim dosyalarının kaydedileceği klasörü seç")'], capture_output=True, **TXT)
            return r.stdout.strip() or None
        if IS_WIN:
            ps = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
                  "$f=New-Object System.Windows.Forms.FolderBrowserDialog;"
                  "$f.Description='Teslim dosyalarinin kaydedilecegi klasoru sec';"
                  "if($f.ShowDialog($o) -eq 'OK'){[Console]::OutputEncoding=[Text.Encoding]::UTF8;$f.SelectedPath}")
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, **TXT)
            return r.stdout.strip() or None
        if shutil.which("zenity"):
            r = subprocess.run(["zenity", "--file-selection", "--directory"], capture_output=True, **TXT)
            return r.stdout.strip() or None
    except OSError:
        pass
    return None


GRAPH_OPT = {"v": None}


def graph_file_option():
    """Uzun filtre zincirini dosyadan okutmak için bu ffmpeg'in desteklediği seçenek.
    ffmpeg 7+: '-/filter_complex dosya'; eski sürümler: '-filter_complex_script dosya'."""
    if GRAPH_OPT["v"] is None:
        tmp = Path(tempfile.mkdtemp(prefix="teslim_g_"))
        gf = tmp / "g.txt"
        gf.write_text("[0:v]null[o]", encoding="utf-8")
        GRAPH_OPT["v"] = ""
        for opt in ("-/filter_complex", "-filter_complex_script"):
            r = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=s=16x16:d=0.04",
                                opt, str(gf), "-map", "[o]", "-f", "null", "-"], capture_output=True, **TXT)
            if r.returncode == 0:
                GRAPH_OPT["v"] = opt
                break
        shutil.rmtree(tmp, ignore_errors=True)
    return GRAPH_OPT["v"]


def graph_args(graph, tmpdir, name):
    """Kısa zincir komut satırına, uzun zincir dosyaya (Windows komut uzunluğu sınırı ~32 bin)."""
    if len(graph) < 2000:
        return ["-filter_complex", graph]
    opt = graph_file_option()
    if not opt:
        return ["-filter_complex", graph]
    gf = Path(tmpdir) / f"{name}.txt"
    gf.write_text(graph, encoding="utf-8")
    return [opt, str(gf)]


def normalize_filter(w, h):
    """Her kareyi w x h'ye getir; oran farklıysa esnetmeden siyahla doldur."""
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")


CMD_LIMIT = 28000  # Windows komut satırı sınırı ~32767; pay bırak


def fit_track_graph(track, cut_idx, axis, vw, vh, crop_w, crop_h, fps, iw, ih, use_sub):
    """Takip zinciri. Zincir dosyadan okutulamıyorsa komut satırı sınırına sığana kadar sadeleştirir;
    gerekirse tek formüllü tam piksel yönteme geçer. Böylece 'filename too long' hiç olmaz."""
    args = (track, cut_idx, axis, vw, vh, crop_w, crop_h, fps, iw, ih)
    g = takip.subpixel_graph(*args, subpixel=use_sub)
    if len(g) < 2000 or graph_file_option():
        return g, ""
    for eps in (0.4, 1.0, 2.0):
        g = takip.subpixel_graph(*args, subpixel=use_sub, eps=eps)
        if len(g) < CMD_LIMIT:
            return g, f", sadeleştirilmiş yol ±{eps:g} px"
    for eps in (1.0, 2.0, 4.0, 8.0, 16.0):
        g = takip.integer_graph(*args, eps=eps)
        if len(g) < CMD_LIMIT:
            return g, f", kısa yöntem ±{eps:g} px"
    return g, ", kısa yöntem"


SUBPIXEL = {"ok": None}


def subpixel_ok():
    if SUBPIXEL["ok"] is None:
        SUBPIXEL["ok"] = os.environ.get("TESLIM_NO_SUBPIXEL") != "1" and takip.subpixel_selftest(FFMPEG)
    return SUBPIXEL["ok"]


FIT_NAMES = {"blur": "bulanık arka plan", "crop": "ortadan kırp", "track": "akıllı takip"}


# ---------------------------------------------------------------- iş akışı
def process_job(job_id, settings):
    job = JOBS[job_id]
    tmpdir = Path(tempfile.mkdtemp(prefix="teslim_"))
    try:
        if not FFMPEG or not FFPROBE:
            raise RuntimeError("ffmpeg bulunamadı. Terminal'de: brew install ffmpeg")
        src_dir = WORK_ROOT / job_id
        sources = sorted(p for p in src_dir.iterdir() if p.is_file())
        if not sources:
            raise RuntimeError("Hiç dosya gelmedi.")

        client = slug(settings.get("client"), "Musteri")[:32].strip("-.") or "Musteri"
        project = slug(settings.get("project"), "Proje")[:32].strip("-.") or "Proje"
        version = int(settings.get("version") or 1)
        date = datetime.now().strftime("%Y%m%d")
        fmts = [f for f in settings.get("formats", []) if f in FORMATS or f == "orijinal"]
        if not fmts:
            raise RuntimeError("En az bir format seç.")
        fit = settings.get("fit", "blur")
        res = str(settings.get("res", "1080"))
        letterbox = bool(settings.get("letterbox", False))
        lufs = float(settings.get("lufs", -14))
        make_preview = bool(settings.get("preview", True))
        wm_text = settings.get("watermark") or "ONIZLEME"

        root = Path(settings.get("out_root") or OUTPUT_ROOT).expanduser()
        if not root.is_dir():
            if root == OUTPUT_ROOT:
                root.mkdir(parents=True, exist_ok=True)
            else:
                raise RuntimeError(f"Kayıt klasörü bulunamadı: {root}")
        out_dir = root / f"{client}_{project}_{date}"
        k = 2
        while (out_dir / "FINAL").exists() and any((out_dir / "FINAL").iterdir()) and settings.get("new_folder", True):
            out_dir = root / f"{client}_{project}_{date}_{k}"
            k += 1
        final_dir, prev_dir, check_dir = out_dir / "FINAL", out_dir / "ONIZLEME", out_dir / "_kontrol"
        for d in (final_dir, check_dir) + ((prev_dir,) if make_preview else ()):
            d.mkdir(parents=True, exist_ok=True)
        job["out_dir"] = str(out_dir)

        if fit == "track":
            print(f"ffmpeg: {FFMPEG} | zincir dosyası seçeneği: '{graph_file_option() or 'yok'}'")
        vision_bin, vision_why = None, None
        if fit == "track" and os.environ.get("TESLIM_NO_VISION") != "1":
            vision_bin, vision_why = takip.vision_binary(str(APP_DIR), str(DATA_DIR), log=lambda m: job.update(current=m))
        use_sub = True
        if fit == "track":
            job["current"] = "Görüntü motoru kontrol ediliyor"
            use_sub = subpixel_ok()

        per_src = len(fmts) * (2 if make_preview else 1) + 1
        job["total"] = len(sources) * per_src
        report = [f"TESLİM RAPORU — {settings.get('client','')} / {settings.get('project','')}",
                  f"Tarih: {datetime.now():%d.%m.%Y %H:%M}   Hedef ses: {lufs} LUFS   "
                  f"Yerleşim: {FIT_NAMES.get(fit, fit)}   Çözünürlük: {res}p"
                  + ("   Sinema: 16:9 içinde bantlı" if letterbox else ""), ""]

        for src in sources:
            info = probe(src)
            if not info["video"]:
                report.append(f"ATLANDI (video değil): {src.name}")
                job["done"] += per_src
                continue
            dur = info["duration"]
            fps = frame_rate(info["video"])
            stem = slug(Path(src.name).stem)[:32].strip("-.") or "video"
            vw, vh = display_size(info["video"])
            report.append(f"Kaynak: {src.name}  ({vw}x{vh}, {dur:.1f} sn, {fps:.2f} fps)")

            stats = None
            if info["audio"]:
                job["current"] = f"{src.name}: ses ölçülüyor"
                stats = measure_loudness(src, lufs)
                if stats == "sessiz":
                    report.append("  Ses: kanal var ama sessiz — ses ayarı yapılmadı")
                elif stats:
                    report.append(f"  Ses: {stats['input_i']} LUFS → {lufs} LUFS")
            else:
                report.append("  Ses: yok (sessiz video)")

            analysis, vision = None, None
            if fit == "track" and any(f != "orijinal" for f in fmts):
                job["current"] = f"{src.name}: sahne geçişleri ve hareket analizi"
                analysis = takip.analyze(FFMPEG, src)
                if vision_bin:
                    job["current"] = f"{src.name}: özne tespiti (Apple Vision)"
                    vision, verr = takip.run_vision(vision_bin, src)
                    if vision is None:
                        vision_why = f"Vision çalışmadı: {verr}"
                if not use_sub:
                    report.append("  Not: bu bilgisayardaki ffmpeg piksel altı kaydırmayı desteklemiyor; "
                                  "güvenli tam piksel yöntemi kullanıldı")
                if vision:
                    report.append("  Takip: Apple Vision özne tespiti + kompozisyon kuralları")
                else:
                    report.append(f"  Takip: hareket analizi (yedek yöntem — {vision_why or 'Vision yok'})")
            job["done"] += 1

            for f in fmts:
                ow, oh, iw, ih = dims(f, vw, vh, res, letterbox)
                label = file_label(f, letterbox)
                note = ""
                if f == "orijinal":
                    graph = f"[0:v]{normalize_filter(iw, ih)}[v0]"
                elif fit == "blur":
                    graph = graph_blur(iw, ih)
                elif fit == "crop":
                    graph = graph_crop(iw, ih)
                else:  # akıllı takip
                    src_ar, dst_ar = vw / vh, iw / ih
                    if abs(src_ar - dst_ar) / dst_ar < 0.01:
                        graph = f"[0:v]scale={iw}:{ih},setsar=1[v0]"
                    else:
                        if dst_ar < src_ar:   # kaynak daha geniş: yatay kaydır
                            axis = "x"
                            crop_w, crop_h = min(ev(vh * dst_ar), vw - vw % 2), vh - vh % 2
                            src_len, crop_len = vw, crop_w
                        else:                 # kaynak daha uzun: dikey kaydır
                            axis = "y"
                            crop_w, crop_h = vw - vw % 2, min(ev(vw / dst_ar), vh - vh % 2)
                            src_len, crop_len = vh, crop_h
                        frac = crop_len / src_len
                        res_path = takip.path_from_vision(vision, analysis, axis, frac) if vision else None
                        track, cut_idx = res_path if res_path else takip.path(analysis, axis, frac)
                        # Piksel altı hassasiyetli kamera (crop + perspective, formülle)
                        graph, how = fit_track_graph(track, cut_idx, axis, vw, vh, crop_w, crop_h, fps, iw, ih, use_sub)
                        # Video ortasında boyutu değişirse (birleştirilmiş kurgular) her kare önce orijinal
                        # boyuta getirilir; aksi halde ffmpeg filtreleri yeniden kuramaz.
                        graph = graph.replace("[0:v]", f"[0:v]{normalize_filter(vw, vh)},", 1)
                        n = sum(1 for a_, b_ in zip(track, track[1:]) if abs(a_[1] - b_[1]) > 1e-6)
                        note = (f"  (takip: {len(cut_idx)} sahne geçişi, "
                                f"{'sabit kamera' if n == 0 else 'yumuşak kaydırma'}{how})")
                if (ow, oh) != (iw, ih):
                    graph += f";[v0]pad={ow}:{oh}:(ow-iw)/2:(oh-ih)/2:black[v]"
                else:
                    graph = graph.replace("[v0]", "[v]")

                name = f"{project}_{stem}_{label}_v{version:02d}.mp4"
                if IS_WIN and len(str(final_dir / name)) > 230:   # Windows yol sınırı (260)
                    name = f"{project[:12]}_{stem[:12]}_{label}_v{version:02d}.mp4"
                final_path = final_dir / name
                cmd = ["-i", str(src)] + graph_args(graph, tmpdir, f"g_{stem}_{f}") + ["-map", "[v]"]
                if info["audio"]:
                    cmd += ["-map", "0:a:0"]
                    if stats != "sessiz":
                        cmd += ["-af", audio_filter(stats, lufs)]
                    cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
                cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(final_path)]
                run_ffmpeg(cmd, job, dur, f"{src.name} → {label} final")
                job["done"] += 1
                report.append(f"  FINAL   {name}  ({ow}x{oh}){note}")

                if f == "9x16":
                    safe_area_sheet(final_path, check_dir / f"{project}_{stem}_9x16_guvenli-alan.png", ow, oh, dur)
                if note:
                    track_sheet(final_path, check_dir / f"{project}_{stem}_{label}_takip.jpg", dur)

                if make_preview:
                    pname = f"{project}_{stem}_{label}_ONIZLEME.mp4"
                    pw, ph = preview_size(ow, oh)
                    wm_png = src_dir / "_wm" / f"wm_{pw}x{ph}.png"
                    pcmd = ["-i", str(final_path)]
                    if wm_png.exists():
                        pcmd += ["-i", str(wm_png), "-filter_complex",
                                 f"[0:v]scale={pw}:{ph}[b];[b][1:v]overlay=0:0,format=yuv420p[v]",
                                 "-map", "[v]", "-map", "0:a?"]
                        mark = "filigranlı"
                    elif HAS_DRAWTEXT:
                        pcmd += ["-vf", f"scale={pw}:{ph}," + watermark_filters(wm_text)]
                        mark = "filigranlı"
                    else:
                        pcmd += ["-vf", f"scale={pw}:{ph}"]
                        mark = "FİLİGRANSIZ — filigran oluşturulamadı"
                    pcmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
                             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(prev_dir / pname)]
                    run_ffmpeg(pcmd, job, dur, f"{src.name} → {label} önizleme")
                    job["done"] += 1
                    report.append(f"  ÖNİZLEME {pname}  ({pw}x{ph}, {mark})")
            report.append("")

        report += ["Notlar:",
                   "- _kontrol/…guvenli-alan.png: kırmızı alanlar Reels/TikTok arayüzünün yaklaşık kapladığı",
                   "  yerlerdir. Önemli yazı ve yüzler bu alanlara denk gelmemeli.",
                   "- _kontrol/…takip.jpg: akıllı takibin videonun farklı anlarında nereyi kadrajladığı."]
        (out_dir / "teslim_raporu.txt").write_text("\n".join(report), encoding="utf-8")
        shutil.rmtree(src_dir, ignore_errors=True)
        job["status"] = "bitti"
        job["current"] = "Tamamlandı"
    except Exception as e:  # noqa: BLE001
        job["status"] = "hata"
        job["error"] = f"{e}\n(Teslim Paketi {VERSION})"
        print(f"HATA: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------- HTTP
LAST_PING = {"t": time.time()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        LAST_PING["t"] = time.time()
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            body = (APP_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/info":
            self._json({"app": "teslim-paketi", "ffmpeg": bool(FFMPEG and FFPROBE), "version": VERSION,
                        "platform": "mac" if IS_MAC else "win" if IS_WIN else "linux",
                        "out": str(OUTPUT_ROOT), "folders": folder_presets(),
                        "formats": [{"key": k, "label": v[0], "desc": v[1], "group": v[4]}
                                    for k, v in FORMATS.items()]})
        elif u.path == "/prefs":
            self._json(load_prefs())
        elif u.path == "/ping":
            self._json({"ok": True})
        elif u.path == "/status":
            jid = parse_qs(u.query).get("job", [""])[0]
            job = JOBS.get(jid)
            self._json(job or {"status": "yok"}, 200 if job else 404)
        else:
            self.send_error(404)

    def do_POST(self):
        LAST_PING["t"] = time.time()
        u = urlparse(self.path)
        q = parse_qs(u.query)
        length = int(self.headers.get("Content-Length") or 0)
        if u.path == "/new":
            jid = uuid.uuid4().hex[:10]
            (WORK_ROOT / jid).mkdir(parents=True, exist_ok=True)
            with JOBS_LOCK:
                JOBS[jid] = {"status": "yükleniyor", "done": 0, "total": 0, "step_pct": 0, "current": ""}
            self._json({"job": jid})
        elif u.path == "/upload":
            jid = q.get("job", [""])[0]
            name = Path(q.get("name", ["dosya"])[0]).name
            if jid not in JOBS:
                return self._json({"error": "iş yok"}, 404)
            remaining = length
            with open(WORK_ROOT / jid / name, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(4 << 20, remaining))
                    if not chunk:
                        break
                    fh.write(chunk)
                    remaining -= len(chunk)
            self._json({"ok": True})
        elif u.path == "/plan":
            body = json.loads(self.rfile.read(length) or b"{}")
            jid = body.get("job")
            if jid not in JOBS or not FFPROBE:
                return self._json({"sizes": []})
            fmts = [f for f in body.get("formats", []) if f in FORMATS or f == "orijinal"]
            res, lb = str(body.get("res", "1080")), bool(body.get("letterbox"))
            sizes = set()
            for p in (WORK_ROOT / jid).iterdir():
                if not p.is_file():
                    continue
                v = probe(p)["video"]
                if not v:
                    continue
                dw, dh = display_size(v)
                for f in fmts:
                    ow, oh, _, _ = dims(f, dw, dh, res, lb)
                    sizes.add(preview_size(ow, oh))
            self._json({"sizes": sorted(sizes)})
        elif u.path == "/upload_wm":
            jid = q.get("job", [""])[0]
            try:
                w, h = int(q.get("w", ["0"])[0]), int(q.get("h", ["0"])[0])
            except ValueError:
                return self._json({"error": "boyut"}, 400)
            if jid not in JOBS or not (0 < w < 10000 and 0 < h < 10000):
                return self._json({"error": "geçersiz"}, 400)
            d = WORK_ROOT / jid / "_wm"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"wm_{w}x{h}.png").write_bytes(self.rfile.read(length))
            self._json({"ok": True})
        elif u.path == "/start":
            settings = json.loads(self.rfile.read(length) or b"{}")
            jid = settings.get("job")
            if jid not in JOBS:
                return self._json({"error": "iş yok"}, 404)
            JOBS[jid]["status"] = "işleniyor"
            threading.Thread(target=process_job, args=(jid, settings), daemon=True).start()
            self._json({"ok": True})
        elif u.path == "/prefs":
            try:
                save_prefs(json.loads(self.rfile.read(length) or b"{}"))
            except ValueError:
                return self._json({"error": "geçersiz"}, 400)
            self._json({"ok": True})
        elif u.path == "/pick_folder":
            path = pick_folder()
            self._json({"path": path.rstrip("/\\") if path and len(path) > 3 else path})
        elif u.path == "/open":
            jid = q.get("job", [""])[0]
            out = JOBS.get(jid, {}).get("out_dir") or q.get("path", [""])[0]
            if out and os.path.isdir(out):
                if IS_MAC:
                    subprocess.Popen(["open", out])
                elif IS_WIN:
                    os.startfile(out)  # type: ignore[attr-defined]
                elif shutil.which("xdg-open"):
                    subprocess.Popen(["xdg-open", out])
            self._json({"ok": bool(out)})
        elif u.path == "/quit":
            self._json({"ok": True})
            threading.Timer(0.3, lambda: os._exit(0)).start()
        else:
            self.send_error(404)


def busy():
    return any(j.get("status") in ("yükleniyor", "işleniyor") for j in JOBS.values())


def watchdog(idle_limit):
    """Tarayıcı sekmesi kapanınca (ping gelmeyince) ve iş yokken uygulamayı kapatır."""
    while True:
        time.sleep(15)
        if not busy() and time.time() - LAST_PING["t"] > idle_limit:
            os._exit(0)


def existing_instance(port):
    """Bu portta çalışan Teslim Paketi'nin sürümü (yoksa None)."""
    import urllib.request
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # yerel adres: vekil sunucu kullanma
        with opener.open(f"http://127.0.0.1:{port}/info", timeout=1.5) as r:
            info = json.loads(r.read().decode())
            return info.get("version", "?") if info.get("app") == "teslim-paketi" else None
    except Exception:  # noqa: BLE001
        return None


def stop_instance(port):
    """Eski sürümü kapat (yeni sürüm yerine geçsin)."""
    import urllib.request
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(urllib.request.Request(f"http://127.0.0.1:{port}/quit", data=b"", method="POST"), timeout=2)
    except Exception:  # noqa: BLE001
        pass
    for _ in range(20):
        time.sleep(0.25)
        if existing_instance(port) is None:
            return True
    return False


def _log_to_file():
    """Pencere olmadan (pythonw) çalışırken çıktıları ve hataları dosyaya yaz."""
    if sys.stdout is None or sys.stderr is None or os.environ.get("TESLIM_LOGFILE"):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            fh = open(os.environ.get("TESLIM_LOGFILE") or (DATA_DIR / "log.txt"), "a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = fh
            print(f"---- {datetime.now():%d.%m.%Y %H:%M:%S} ----")
        except OSError:
            pass


def main():
    _log_to_file()
    try:
        _main()
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        raise


def _main():
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    # Önceki oturumdan kalan geçici dosyaları temizle
    for old in WORK_ROOT.iterdir():
        try:
            if time.time() - old.stat().st_mtime > 6 * 3600:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    open_browser = os.environ.get("TESLIM_NO_BROWSER") != "1"
    srv, port = None, PORT
    for port in range(PORT, PORT + 20):
        running = existing_instance(port)
        if running == VERSION:
            print(f"Teslim Paketi zaten açık: http://127.0.0.1:{port}")
            if open_browser:
                webbrowser.open(f"http://127.0.0.1:{port}")
            return
        if running is not None:
            print(f"Eski sürüm ({running}) kapatılıyor, yerine {VERSION} açılıyor")
            stop_instance(port)
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        print("Boş port bulunamadı.")
        return
    url = f"http://127.0.0.1:{port}"
    print(f"Teslim Paketi {VERSION} çalışıyor: {url}")
    print(f"Varsayılan çıktı klasörü: {OUTPUT_ROOT}")
    if not FFMPEG:
        print("UYARI: ffmpeg bulunamadı.")
    if os.environ.get("TESLIM_NO_AUTOQUIT") != "1" and open_browser:
        threading.Thread(target=watchdog, args=(int(os.environ.get("TESLIM_IDLE", "120")),), daemon=True).start()
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
