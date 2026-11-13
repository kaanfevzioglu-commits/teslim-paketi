"""CI duman testi: gömülü ffmpeg ile gerçek işletim sisteminde uçtan uca işlem.
Öz-test, ortasında çözünürlük değişen video, uzun hareketli video (komut uzunluğu),
sessiz ses kanalı ve (Mac'te) Apple Vision."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "build" / "bin"
os.environ["PATH"] = str(BIN) + os.pathsep + os.environ.get("PATH", "")
TMP = Path(tempfile.mkdtemp(prefix="teslim_smoke_"))
os.environ["TESLIM_OUT"] = str(TMP / "out")
os.environ["TESLIM_DATA"] = str(TMP / "data")
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import takip  # noqa: E402

FF = server.FFMPEG
print("Teslim Paketi", server.VERSION, "| ffmpeg:", FF)
print("zincir dosyası seçeneği:", repr(server.graph_file_option()))
print("piksel altı öz-test:", server.subpixel_ok())


def ff(*args):
    r = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if r.returncode:
        sys.exit("test videosu üretilemedi: " + r.stderr)


src = TMP / "src"
src.mkdir()
# 1) ortasında çözünürlük + renk aralığı değişen kurgu (teaser gibi)
ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=24:d=3", "-f", "lavfi", "-i", "sine=d=3",
   "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(TMP / "p1.mp4"))
ff("-f", "lavfi", "-i", "testsrc=s=1280x720:r=24:d=3", "-f", "lavfi", "-i", "sine=f=600:d=3",
   "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", "-color_range", "pc", str(TMP / "p2.mp4"))
(TMP / "l.txt").write_text(f"file '{(TMP / 'p1.mp4').as_posix()}'\nfile '{(TMP / 'p2.mp4').as_posix()}'\n")
ff("-f", "concat", "-safe", "0", "-i", str(TMP / "l.txt"), "-c", "copy", str(src / "Karışık Teaser.mp4"))
# 2) uzun ve sürekli hareket eden video (Windows komut uzunluğu sınırı testi)
ff("-f", "lavfi", "-i", "gradients=s=1920x1080:d=30:speed=0.0001", "-f", "lavfi",
   "-i", "color=c=black@0:s=220x220:d=30,format=rgba,geq=r='255':g='150':b='30':a='if(lt(hypot(X-110,Y-110),105),255,0)'",
   "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
   "-filter_complex", "[0][1]overlay=x='80+(1920-380)*(0.5-0.5*cos(t*2*PI/9))':y=430:shortest=1,format=yuv420p[v]",
   "-map", "[v]", "-map", "2:a", "-t", "30", "-r", "24", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
   str(src / "uzun hareketli (sessiz ses).mp4"))

failed = False
for fit in ("track", "blur"):
    jid = "smoke_" + fit
    d = server.WORK_ROOT / jid
    d.mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        (d / p.name).write_bytes(p.read_bytes())
    server.JOBS[jid] = {"status": "x", "done": 0, "total": 0, "step_pct": 0, "current": ""}
    t = time.time()
    server.process_job(jid, {"client": "Test Müşteri", "project": f"duman {fit}", "fit": fit, "preview": True,
                             "formats": ["9x16", "235"] if fit == "track" else ["9x16", "orijinal"],
                             "letterbox": fit == "blur"})
    job = server.JOBS[jid]
    print(f"\n== {fit}: {job['status']} ({time.time() - t:.0f} sn)")
    if job["status"] != "bitti":
        print(job.get("error"))
        failed = True
        continue
    print((Path(job["out_dir"]) / "teslim_raporu.txt").read_text(encoding="utf-8"))

if sys.platform == "darwin":
    binary, why = takip.vision_binary(str(ROOT / "app"), str(ROOT / "build"))
    if (ROOT / "build" / "bin" / "kadraj").exists():
        binary = str(ROOT / "build" / "bin" / "kadraj")
    print("\nApple Vision ikili dosyası:", binary, why or "")
    if binary:
        vis, err = takip.run_vision(binary, src / "Karışık Teaser.mp4")
        if vis is None:
            print("Vision HATA:", err)
            failed = True
        else:
            print(f"Vision: {len(vis['frames'])} kare, {sum(len(f['boxes']) for f in vis['frames'])} kutu")

sys.exit(1 if failed else 0)
