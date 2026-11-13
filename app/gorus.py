"""OpenCV ile özne tespiti (Windows'ta ve Apple Vision olmayan her yerde).

Her karede (saniyede 6):
  - yüzler: YuNet (MIT lisans; uzaktaki küçük yüzleri de bulur), yoksa Haar
  - insan / hayvan / nesne: YOLOX-tiny (Apache 2.0 lisans, COCO 80 sınıf)
  - hiçbiri yoksa: dikkat çeken alan (saliency + hareket), düşük öncelikle
Çıktı, Apple Vision bileşeniyle (kadraj.swift) aynı biçimdedir:
  {"fps": 6, "frames": [{"t": 0.0, "boxes": [{"k": "face"|"human"|"obj", "x","y","w","h" (0-1), "c"}]}]}
Böylece aynı kompozisyon ve sakin kamera kuralları kullanılır.
"""
import os
import subprocess

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    AVAILABLE = True
except Exception:  # noqa: BLE001
    AVAILABLE = False

FPS = 6
AW = 960  # analiz genişliği (küçük yüzler için yüksek tutuldu)
ANIMALS = set(range(14, 24))  # COCO: kuş, kedi, köpek, at, koyun, inek, fil, ayı, zebra, zürafa


def _model_dirs(app_dir):
    dirs = [os.path.join(app_dir, "models")]
    if AVAILABLE:
        try:
            dirs.append(cv2.data.haarcascades)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return dirs


def _find(app_dir, name):
    for d in _model_dirs(app_dir):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


class _Detectors:
    def __init__(self, app_dir, w, h):
        self.w, self.h = w, h
        self.yunet = None
        yp = _find(app_dir, "face_detection_yunet.onnx")
        if yp and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.yunet = cv2.FaceDetectorYN.create(yp, "", (w, h), 0.7, 0.3, 5000)
            except Exception:  # noqa: BLE001
                self.yunet = None
        self.haar = []
        if self.yunet is None:
            for n in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
                p = _find(app_dir, n)
                if p:
                    c = cv2.CascadeClassifier(p)
                    if not c.empty():
                        self.haar.append(c)
        self.yolox = None
        xp = _find(app_dir, "yolox_tiny.onnx")
        if xp:
            try:
                self.yolox = cv2.dnn.readNetFromONNX(xp)
            except Exception:  # noqa: BLE001
                self.yolox = None
        self.sal = cv2.saliency.StaticSaliencySpectralResidual_create() if hasattr(cv2, "saliency") else None
        self.face_kind = "yunet" if self.yunet is not None else ("haar" if self.haar else "yok")
        self.obj_kind = "yolox" if self.yolox is not None else "yok"

    def faces(self, img, gray):
        out = []
        if self.yunet is not None:
            _, f = self.yunet.detect(img)
            if f is not None:
                for r in f:
                    x, y, w, h, score = float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[14])
                    out.append((x, y, w, h, score))
            return out
        ms = max(18, int(self.h * 0.06))
        for c in self.haar:
            for (x, y, w, h) in c.detectMultiScale(gray, 1.1, 7, minSize=(ms, ms)):
                out.append((float(x), float(y), float(w), float(h), 0.7))
        return out

    def objects(self, img, size=416):
        """YOLOX-tiny: (sınıf türü, x, y, w, h, güven) listesi, analiz karesi pikselinde."""
        if self.yolox is None:
            return []
        h, w = img.shape[:2]
        r = min(size / h, size / w)
        pad = np.full((size, size, 3), 114, np.uint8)
        pad[:int(h * r), :int(w * r)] = cv2.resize(img, (int(w * r), int(h * r)))
        self.yolox.setInput(pad.transpose(2, 0, 1)[None].astype(np.float32))
        out = self.yolox.forward()[0]
        if not hasattr(self, "_grid"):
            grids, strides = [], []
            for st in (8, 16, 32):
                g = size // st
                yv, xv = np.meshgrid(np.arange(g), np.arange(g), indexing="ij")
                grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
                strides.append(np.full((g * g, 1), st))
            self._grid, self._stride = np.concatenate(grids), np.concatenate(strides)
        out[:, :2] = (out[:, :2] + self._grid) * self._stride
        out[:, 2:4] = np.exp(out[:, 2:4]) * self._stride
        scores = out[:, 4:5] * out[:, 5:]
        cls, conf = scores.argmax(1), scores.max(1)
        keep = conf > 0.3
        bx, cls, conf = out[keep, :4], cls[keep], conf[keep]
        if not len(conf):
            return []
        boxes = np.stack([bx[:, 0] - bx[:, 2] / 2, bx[:, 1] - bx[:, 3] / 2, bx[:, 2], bx[:, 3]], 1) / r
        idx = np.array(cv2.dnn.NMSBoxes(boxes.tolist(), conf.tolist(), 0.3, 0.45)).flatten()
        res = []
        for i in idx:
            c, cf = int(cls[i]), float(conf[i])
            kind = "human" if c == 0 else "animal" if c in ANIMALS else "obj"
            if kind == "obj" and cf < 0.45:
                continue
            x, y, bw, bh = boxes[i]
            res.append((kind, float(x), float(y), float(bw), float(bh), cf))
        return res

    def salient(self, img, gray, prev_gray):
        """Saliency (+ hareket) haritasında en yoğun bölgenin kutusu."""
        sw = 160
        sh = max(16, int(self.h * sw / self.w))
        g = cv2.resize(gray, (sw, sh))
        m = np.zeros((sh, sw), np.float32)
        if self.sal is not None:
            ok, smap = self.sal.computeSaliency(cv2.resize(img, (sw, sh)))
            if ok:
                m += cv2.GaussianBlur(smap.astype(np.float32), (0, 0), 3)
        if prev_gray is not None:
            pg = cv2.resize(prev_gray, (sw, sh))
            mot = cv2.GaussianBlur(cv2.absdiff(g, pg).astype(np.float32), (0, 0), 3) / 255.0
            mot[mot < 0.04] = 0
            m += mot * 1.5
        if m.max() <= 1e-6:
            return None
        m /= m.max()
        thr = max(0.35, float(np.percentile(m, 92)))
        mask = (m >= thr).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        if n <= 1:
            return None
        best, best_mass = None, 0.0
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area < 6:
                continue
            mass = float(m[lab == i].sum())
            if mass > best_mass:
                best, best_mass = (x, y, w, h), mass
        if best is None:
            return None
        x, y, w, h = best
        k = self.w / sw
        conf = min(1.0, best_mass / (m.sum() + 1e-6) * 3)
        return (x * k, y * k, w * k, h * k, conf)


def _stable_faces(frames):
    """Tek karede görünüp kaybolan (doku kaynaklı sahte) yüzleri ele: komşu karede benzeri olmalı."""
    def near(a, b):
        ax, ay = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
        bx, by = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
        return abs(ax - bx) < max(a["w"], b["w"]) and abs(ay - by) < max(a["h"], b["h"])
    faces = [[b for b in f["boxes"] if b["k"] == "face"] for f in frames]
    for i, f in enumerate(frames):
        keep = []
        for b in f["boxes"]:
            if b["k"] != "face":
                keep.append(b)
                continue
            nb = (faces[i - 1] if i > 0 else []) + (faces[i + 1] if i + 1 < len(faces) else [])
            if any(near(b, o) for o in nb):
                keep.append(b)
        f["boxes"] = keep
    return frames


def analyze(ffmpeg, src, app_dir, display_w, display_h, log=None):
    """Videoyu tarar; Vision biçiminde özne kutuları döndürür. OpenCV yoksa None."""
    if not AVAILABLE:
        return None, "OpenCV yok"
    w = AW
    h = max(2, int(round(display_h * AW / display_w / 2)) * 2)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(src), "-an",
           "-vf", f"fps={FPS},scale={w}:{h}:flags=area,format=bgr24", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kw)
    det = _Detectors(app_dir, w, h)
    size = w * h * 3
    frames, prev_gray, i = [], None, 0
    while True:
        buf = proc.stdout.read(size)
        if not buf or len(buf) < size:
            break
        img = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        boxes = []
        for (x, y, bw, bh, c) in det.faces(img, gray):
            boxes.append({"k": "face", "x": x / w, "y": y / h, "w": bw / w, "h": bh / h, "c": c})
        for (kind, x, y, bw, bh, c) in det.objects(img):
            boxes.append({"k": kind, "x": x / w, "y": y / h, "w": bw / w, "h": bh / h, "c": c})
        if not boxes:  # ne yüz ne nesne: dikkat çeken alana bak (düşük öncelik)
            s = det.salient(img, gray, prev_gray)
            if s:
                x, y, bw, bh, c = s
                boxes.append({"k": "att", "x": x / w, "y": y / h, "w": bw / w, "h": bh / h, "c": c})
        frames.append({"t": i / FPS, "boxes": boxes})
        prev_gray = gray
        i += 1
    proc.wait()
    frames = _stable_faces(frames)
    return {"fps": FPS, "frames": frames, "faces": det.face_kind, "objects": det.obj_kind}, None
