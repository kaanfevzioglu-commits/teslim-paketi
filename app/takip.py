"""Akıllı takip: videodaki ana hareketi/ilgi alanını bulup kırpma penceresini onunla kaydırır.

Yöntem (ek kütüphane gerektirmez):
  1. ffmpeg videoyu küçük gri karelere çevirir (saniyede 6 kare, 96x54).
  2. Her karede sütun/satır başına "ilgi" puanı: hareket (önceki kareden fark) ağırlıklı,
     biraz da detay (kenar yoğunluğu). Hafif merkez eğilimi gürültüyü bastırır.
  3. Kırpma genişliğinde kayan pencere ile en çok ilgiyi içeren konum seçilir.
  4. Sahne geçişleri (cut) bulunur; her sahne kendi içinde yumuşatılır, böylece
     kamera kesmelerde kayarak değil, kesmeyle birlikte atlar.
  5. İleri-geri yumuşatma ile operatör gibi akıcı, gecikmesiz bir kamera yolu çıkar.
"""
import math
import subprocess

AW, AH = 96, 54      # analiz çözünürlüğü
AFPS = 6             # analiz kare hızı
CUT_THRESHOLD = 38   # ortalama piksel farkı bunun üstündeyse sahne geçişi say
MOTION_FLOOR = 10    # bu farkın altı gürültü sayılır


def analyze(ffmpeg, src):
    """Videoyu tarar; kare başına sütun ve satır ilgi profillerini ve kesme noktalarını döndürür."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(src), "-an",
           "-vf", f"fps={AFPS},scale={AW}:{AH}:flags=area,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = AW * AH
    cols_all, rows_all, cuts = [], [], []
    prev = None
    cx_bias = [0.65 + 0.35 * math.exp(-((x / (AW - 1) - 0.5) / 0.38) ** 2) for x in range(AW)]
    cy_bias = [0.65 + 0.35 * math.exp(-((y / (AH - 1) - 0.5) / 0.38) ** 2) for y in range(AH)]
    while True:
        buf = proc.stdout.read(size)
        if not buf or len(buf) < size:
            break
        colw = [0.0] * AW
        roww = [0.0] * AH
        diff_sum = 0
        mean = sum(buf) / size  # kare ortalaması: ondan belirgin ayrışan alanlar "özne" adayıdır
        for y in range(AH):
            row = buf[y * AW:(y + 1) * AW]
            # detay: yatay komşu farkı
            det = [abs(a - b) for a, b in zip(row[1:], row[:-1])]
            det.append(0)
            if prev is not None:
                prow = prev[y * AW:(y + 1) * AW]
                mot = [abs(a - b) for a, b in zip(row, prow)]
                diff_sum += sum(mot)
                vals = [(m if m > MOTION_FLOOR else 0) * 2.0 + d * 0.35 + _con(p, mean)
                        for m, d, p in zip(mot, det, row)]
            else:
                vals = [d * 0.35 + _con(p, mean) for d, p in zip(det, row)]
            s = 0.0
            for x, v in enumerate(vals):
                colw[x] += v
                s += v
            roww[y] += s
        colw = [v * b for v, b in zip(colw, cx_bias)]
        roww = [v * b for v, b in zip(roww, cy_bias)]
        idx = len(cols_all)
        if prev is not None and diff_sum / size > CUT_THRESHOLD:
            cuts.append(idx)
        cols_all.append(colw)
        rows_all.append(roww)
        prev = buf
    proc.wait()
    return {"cols": cols_all, "rows": rows_all, "cuts": cuts}


def _con(p, mean):
    c = abs(p - mean)
    return (c - 25) * 0.6 if c > 25 else 0.0


def _operator(raw, deadzone, gain=0.85):
    """Kameraman davranışı: özne kadrajın rahat bölgesindeyse kıpırdama, çıkınca yakala."""
    if not raw:
        return raw
    p, out = raw[0], []
    for r in raw:
        d = r - p
        if abs(d) > deadzone:
            p += (abs(d) - deadzone) * gain * (1 if d > 0 else -1)
        out.append(p)
    return out


def _best_window(profile, frac):
    """Profil üzerinde frac genişliğindeki pencerenin en yüksek toplamlı merkezini (0-1) bulur."""
    n = len(profile)
    # Arka plan seviyesini çıkar: sadece ortalamanın belirgin üstündeki alanlar sayılır
    base = sorted(profile)[n // 2]
    profile = [max(0.0, v - base * 1.15) for v in profile]
    win = max(1, min(n, round(frac * n)))
    if win >= n:
        return 0.5
    total = sum(profile)
    if total <= 1e-6:
        return None
    s = sum(profile[:win])
    best, best_i = s, 0
    for i in range(1, n - win + 1):
        s += profile[i + win - 1] - profile[i - 1]
        if s > best:
            best, best_i = s, i
    # Pencere içindeki ilginin ağırlık merkezine ortala: özne kenarda değil ortada dursun
    seg = profile[best_i:best_i + win]
    tot = sum(seg)
    if tot <= 1e-6:
        return (best_i + win / 2) / n
    cen = sum((best_i + i + 0.5) * v for i, v in enumerate(seg)) / tot
    return cen / n


def _median(vals, k=5):
    out = []
    h = k // 2
    for i in range(len(vals)):
        w = sorted(vals[max(0, i - h):i + h + 1])
        out.append(w[len(w) // 2])
    return out


def _smooth(vals, alpha):
    """İleri + geri üstel yumuşatma: gecikmesiz, akıcı."""
    if not vals:
        return vals
    f = [vals[0]]
    for v in vals[1:]:
        f.append(f[-1] + alpha * (v - f[-1]))
    b = [f[-1]]
    for v in reversed(f[:-1]):
        b.append(b[-1] + alpha * (v - b[-1]))
    return list(reversed(b))


def path(analysis, axis, frac, smoothness=0.55):
    """Kırpma merkezinin zaman içindeki yolu: [(zaman_sn, merkez_0_1), ...]"""
    profiles = analysis["cols"] if axis == "x" else analysis["rows"]
    if not profiles:
        return [(0.0, 0.5)], set()
    raw, last = [], 0.5
    for p in profiles:
        c = _best_window(p, frac)
        if c is None:
            c = last
        raw.append(c)
        last = c
    bounds = [0] + [c for c in analysis["cuts"] if 0 < c < len(raw)] + [len(raw)]
    centers = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = raw[a:b]
        if not seg:
            continue
        seg = _smooth(_median(seg, 5), 0.5)
        tol = frac * 0.12
        centers.extend(calm_path([c - tol for c in seg], [c + tol for c in seg], seg, frac))
    half = frac / 2
    centers = [min(1 - half, max(half, c)) for c in centers]
    return [(i / AFPS, c) for i, c in enumerate(centers)], set(bounds[1:-1])


def sendcmd_file(track, cut_idx, out_path, fps, duration, src_len, crop_len, axis, start=0.0):
    """Her çıkış karesi için kırpma konumunu sendcmd dosyasına yazar."""
    lines, last = [], None
    n_frames = max(1, int(duration * fps) + 1)
    pts = track
    for i in range(n_frames):
        t = i / fps
        s = t * AFPS
        k = min(len(pts) - 1, int(s))
        frac_part = s - k
        c = pts[k][1]
        if k + 1 < len(pts) and (k + 1) not in cut_idx:
            c = c + (pts[k + 1][1] - c) * frac_part
        pos = int(round(c * src_len - crop_len / 2))
        pos = max(0, min(src_len - crop_len, pos))
        if pos != last:
            # Komut iki karenin tam ortasına: her kare kendi konumunu alır, takılma olmaz
            tc = start + (t - 0.5 / fps if i > 0 else 0.0)
            lines.append(f"{max(0.0, tc):.5f} crop {axis} {pos};")
            last = pos
    with open(out_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    first = int(lines[0].split()[-1].rstrip(";")) if lines else 0
    return first, len(lines)


# =====================================================================
# Apple Vision ile özne tespiti (Mac) + kompozisyon kurallı kadrajlama
# =====================================================================
import json
import os
import shutil
import sys

PRIORITY = {"face": 3.0, "human": 2.2, "animal": 2.2, "obj": 1.0, "att": 0.45}


def vision_binary(app_dir, data_dir=None, log=None):
    """Mac'te kadraj.swift'i bir kere derler, ikili dosyanın yolunu döndürür. Olmazsa None.
    Derlenen dosya kullanıcı veri klasörüne yazılır (uygulama paketi salt okunur olabilir)."""
    if sys.platform != "darwin":
        return None, "Apple Vision yalnızca Mac'te var (Windows'ta hareket analizi kullanılır)"
    bundled = os.path.join(app_dir, "bin", "kadraj")   # paketlenmiş uygulamada önceden derlenmiş gelir
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return bundled, None
    src = os.path.join(app_dir, "kadraj.swift")
    if not os.path.isfile(src):
        return None, "Vision bileşeni bulunamadı"
    out_dir = os.path.join(data_dir or app_dir, "bin")
    binary = os.path.join(out_dir, "kadraj")
    if os.path.exists(binary) and os.path.getmtime(binary) >= os.path.getmtime(src):
        return binary, None
    if subprocess.run(["xcode-select", "-p"], capture_output=True).returncode != 0:
        return None, "Apple geliştirici araçları yok (Terminal: xcode-select --install)"
    swiftc = shutil.which("swiftc") or "/usr/bin/swiftc"
    os.makedirs(out_dir, exist_ok=True)
    if log:
        log("Apple görüntü tanıma hazırlanıyor (yalnızca ilk sefer, ~1 dk)")
    res = subprocess.run([swiftc, "-O", src, "-o", binary], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0 or not os.path.exists(binary):
        err = (res.stderr or res.stdout or "").strip().splitlines()
        return None, "derleme başarısız: " + (" | ".join(err[-3:]) if err else "bilinmeyen hata")
    return binary, None


def run_vision(binary, src):
    res = subprocess.run([binary, str(src), str(AFPS)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        return None, (res.stderr or "").strip()[-200:]
    try:
        return json.loads(res.stdout), None
    except ValueError:
        return None, "Vision çıktısı okunamadı"


def _iou(a, b):
    ax2, ay2, bx2, by2 = a["x"] + a["w"], a["y"] + a["h"], b["x"] + b["w"], b["y"] + b["h"]
    iw = max(0.0, min(ax2, bx2) - max(a["x"], b["x"]))
    ih = max(0.0, min(ay2, by2) - max(a["y"], b["y"]))
    inter = iw * ih
    u = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / u if u > 0 else 0.0


def _contains(outer, inner, tol=0.05):
    return (inner["x"] >= outer["x"] - tol and inner["y"] >= outer["y"] - tol and
            inner["x"] + inner["w"] <= outer["x"] + outer["w"] + tol and
            inner["y"] + inner["h"] <= outer["y"] + outer["h"] + tol)


def _select_subjects(frames, cuts=()):
    """Her kare için özne kutusu ve (varsa) yüz kutusu. Önceki özneye yakın olanı tercih eder;
    sahne geçişinde bu tercih sıfırlanır (yeni planda eski özneye yapışmasın)."""
    chosen, prev = [], None
    cuts = set(cuts)
    for idx, fr in enumerate(frames):
        if idx in cuts:
            prev = None
        boxes = [b for b in fr.get("boxes", []) if b["w"] > 0.01 and b["h"] > 0.01]
        best, best_s = None, 0.0
        for b in boxes:
            s = PRIORITY.get(b["k"], 0.5) * max(b["c"], 0.3) * (b["w"] * b["h"]) ** 0.35
            if prev is not None and _iou(b, prev) > 0.15:
                s *= 1.7
            if s > best_s:
                best, best_s = b, s
        face = None
        if best is not None:
            faces = [b for b in boxes if b["k"] == "face" and (best["k"] == "face" or _contains(best, b))]
            if faces:
                face = max(faces, key=lambda b: b["w"] * b["h"])
            # yüz seçildiyse, onu içeren insan kutusu özne genişliği olarak kullanılır
            if best["k"] == "face":
                face = best
                humans = [b for b in boxes if b["k"] == "human" and _contains(b, best)]
                if humans:
                    best = max(humans, key=lambda b: b["w"] * b["h"])
            prev = best
        chosen.append((best, face))
    return chosen


def _fill(vals):
    """None değerleri komşularla doldurur."""
    out = list(vals)
    last = None
    for i, v in enumerate(out):
        if v is None:
            out[i] = last
        else:
            last = v
    nxt = None
    for i in range(len(out) - 1, -1, -1):
        if out[i] is None:
            out[i] = nxt
        else:
            nxt = out[i]
    return out


def _compose(lo, hi, focus, v, axis, L):
    """Kompozisyon kuralları: hareket yönüne boşluk, yüz üst üçte birde, özneyi kesme."""
    m = 0.04 * L
    size = hi - lo
    direction = max(-1.0, min(1.0, v / 0.03))   # 0.03 = saniyede kadrajın %3'ü
    if focus is not None:
        f_lo, f_hi = focus
        fc = (f_lo + f_hi) / 2
        if axis == "y":
            c = fc + 0.12 * L                     # yüz/gözler kadrajın üst ~%38'inde
        else:
            c = fc + direction * 0.15 * L         # baktığı/gittiği yöne boşluk
        if size <= L - 2 * m:
            c = min(max(c, hi - L / 2 + m), lo + L / 2 - m)
        else:
            c = min(max(c, f_hi - L / 2 + m), f_lo + L / 2 - m)
    else:
        if size <= L - 2 * m:                     # özne sığıyor: tamamen kadraja al
            if axis == "x":
                c = (lo + hi) / 2 + direction * (L - size) / 2 * 0.6
            else:
                c = (lo + hi) / 2 - 0.05 * L      # zemine oturan, hafif üst boşluklu duruş
            c = min(max(c, hi - L / 2 + m), lo + L / 2 - m)
        else:                                     # özne kadrajdan büyük
            if axis == "x":
                center = (lo + hi) / 2
                front = (hi - L / 2 + m) if direction > 0 else (lo + L / 2 - m)
                c = center + abs(direction) * (front - center)   # gidiş yönündeki ön/baş kısmı göster
            else:
                c = lo + L / 2 - m                # dikeyde öznenin üst kısmı (baş) korunur
    return min(1 - L / 2, max(L / 2, c))


def path_from_vision(vision, analysis, axis, frac, smoothness=0.35):
    """Vision tespitlerinden kamera yolu. Tespit olmayan sahnelerde hareket analizine döner."""
    frames = vision.get("frames", [])
    n = len(frames)
    if n == 0:
        return None
    chosen = _select_subjects(frames, (analysis or {}).get("cuts", []))
    if axis == "x":
        lo_raw = [b["x"] if b else None for b, _ in chosen]
        hi_raw = [b["x"] + b["w"] if b else None for b, _ in chosen]
        fo_raw = [(f["x"], f["x"] + f["w"]) if f else None for _, f in chosen]
    else:
        lo_raw = [b["y"] if b else None for b, _ in chosen]
        hi_raw = [b["y"] + b["h"] if b else None for b, _ in chosen]
        fo_raw = [(f["y"], f["y"] + f["h"]) if f else None for _, f in chosen]

    cuts = [c for c in (analysis or {}).get("cuts", []) if 0 < c < n]
    bounds = [0] + cuts + [n]
    heur = None
    centers = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        lo_s, hi_s, fo_s = lo_raw[a:b], hi_raw[a:b], fo_raw[a:b]
        if all(v is None for v in lo_s):
            # Bu sahnede özne bulunamadı: hareket analizine dön
            if heur is None:
                heur = path(analysis, axis, frac)[0] if analysis and analysis.get("cols") else []
            seg = [heur[i][1] if i < len(heur) else 0.5 for i in range(a, b)]
            centers.extend(seg)
            continue
        lo_s = _smooth(_median(_fill(lo_s), 5), 0.4)
        hi_s = _smooth(_median(_fill(hi_s), 5), 0.4)
        mids = [(l + h) / 2 for l, h in zip(lo_s, hi_s)]
        A, B, P = [], [], []
        k = int(AFPS * 1.5)  # yön için ±1.5 sn
        for i in range(len(mids)):
            i0, i1 = max(0, i - k), min(len(mids) - 1, i + k)
            v = (mids[i1] - mids[i0]) / max(1e-6, (i1 - i0) / AFPS)
            direction = max(-1.0, min(1.0, v / 0.03))
            a_, b_, p_ = corridor(lo_s[i], hi_s[i], fo_s[i], direction, axis, frac)
            A.append(a_); B.append(b_); P.append(p_)
        centers.extend(calm_path(A, B, P, frac))
    half = frac / 2
    centers = [min(1 - half, max(half, c)) for c in centers]
    return [(i / AFPS, c) for i, c in enumerate(centers)], set(cuts)


# =====================================================================
# Sakin kamera: "dur — süzül — dur"
# Özne kadrajda kalabildiği sürece kamera sabit; gerekirse en kısa yoldan, yumuşak gider.
# =====================================================================
def _gauss(vals, sigma):
    if sigma <= 0 or len(vals) < 3:
        return list(vals)
    r = max(1, int(sigma * 3))
    ker = [math.exp(-0.5 * (k / sigma) ** 2) for k in range(-r, r + 1)]
    n, out = len(vals), []
    for i in range(n):
        s = w = 0.0
        for j, kv in enumerate(ker):
            idx = min(n - 1, max(0, i + j - r))
            s += vals[idx] * kv
            w += kv
        out.append(s / w)
    return out


def calm_path(a, b, pref, L, step=3):
    """a[i] <= merkez <= b[i] koridorunda en az hareketli yumuşak yol.
    pref: koridor içinde tercih edilen (kompozisyon) merkezleri."""
    n = len(a)
    if n == 0:
        return []
    lo_lim, hi_lim = L / 2, 1 - L / 2
    a = [min(hi_lim, max(lo_lim, v)) for v in a]
    b = [min(hi_lim, max(lo_lim, v)) for v in b]
    for i in range(n):
        if a[i] > b[i]:
            a[i] = b[i] = (a[i] + b[i]) / 2
    # 1) Tek sabit konum yetiyor mu? (kilitli kamera)
    A, B = max(a), min(b)
    if A <= B:
        want = sorted(pref)[n // 2]
        return [min(B, max(A, want))] * n
    # 2) Kaba ızgarada (yarım saniyelik dilimler) gergin ip yolu
    idx = list(range(0, n, step))
    ca = [max(a[i:i + step]) for i in idx]
    cb = [min(b[i:i + step]) for i in idx]
    for i in range(len(ca)):
        if ca[i] > cb[i]:
            ca[i] = cb[i] = (ca[i] + cb[i]) / 2
    m = len(ca)
    c = [min(cb[i], max(ca[i], pref[idx[i]])) for i in range(m)]
    for _ in range(max(400, 30 * m)):
        moved = 0.0
        for i in range(m):
            if m == 1:
                break
            if i == 0:
                t = c[1]
            elif i == m - 1:
                t = c[m - 2]
            else:
                t = (c[i - 1] + c[i + 1]) / 2
            t = min(cb[i], max(ca[i], t))
            moved = max(moved, abs(t - c[i]))
            c[i] = t
        if moved < 1e-6:
            break
    # 3) İnce ızgaraya doğrusal aç, sonra yumuşak hızlanıp yavaşlama için Gauss
    full = []
    for i in range(n):
        p = i / step
        k = min(m - 1, int(p))
        f = p - k
        full.append(c[k] + ((c[k + 1] - c[k]) * f if k + 1 < m else 0.0))
    full = _gauss(full, sigma=AFPS * 0.6)
    return [min(hi_lim, max(lo_lim, v)) for v in full]


def corridor(lo, hi, focus, direction, axis, L):
    """Bu karede kadraj merkezinin nerede olabileceği [a, b] ve tercih edilen merkez."""
    m = 0.03 * L
    size = hi - lo
    pref = _compose(lo, hi, focus, direction * 0.03, axis, L)
    if focus is not None:
        f_lo, f_hi = focus
        a, b = f_hi - L / 2 + m, f_lo + L / 2 - m
        if size <= L - 2 * m:
            a, b = max(a, hi - L / 2 + m), min(b, lo + L / 2 - m)
        if axis == "y":                       # yüz kadrajın üst yarısında kalsın
            b = min(b, (f_lo + f_hi) / 2 + L * 0.25)
    elif size <= L - 2 * m:                   # özne tamamen sığıyor
        a, b = hi - L / 2 + m, lo + L / 2 - m
    else:                                     # özne kadrajdan büyük: ön kısmı (gidiş yönü) kadrajda kalsın
        e = 0.6 * L
        if axis == "x":
            if direction > 0.3:
                need = (hi - e, hi)
            elif direction < -0.3:
                need = (lo, lo + e)
            else:
                mid = (lo + hi) / 2
                need = (mid - e / 2, mid + e / 2)
        else:
            need = (lo, lo + e)               # dikeyde üst kısım (baş)
        a, b = need[1] - L / 2 + m, need[0] + L / 2 - m
    if a > b:
        a = b = (a + b) / 2
    return a, b, pref


# =====================================================================
# Piksel altı (sub-pixel) kamera: yol, kare numarasına bağlı bir formüle çevrilir.
# crop tam pikseli, perspective kesirli kısmı kaydırır -> 0/2/0/2 takılması olmaz.
# =====================================================================
def _simplify(pts, eps):
    """Ramer–Douglas–Peucker: yolu, en fazla eps sapmayla en az noktaya indirir."""
    if len(pts) < 3:
        return list(range(len(pts)))
    keep = {0, len(pts) - 1}
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ta, pa), (tb, pb) = pts[a], pts[b]
        worst, wi = -1.0, -1
        for i in range(a + 1, b):
            t, p = pts[i]
            lin = pa + (pb - pa) * (t - ta) / (tb - ta) if tb > ta else pa
            d = abs(p - lin)
            if d > worst:
                worst, wi = d, i
        if worst > eps and wi > 0:
            keep.add(wi)
            stack += [(a, wi), (wi, b)]
    return sorted(keep)


def track_expr(track, cut_idx, src_len, crop_len, fps, var="n", eps=0.15):
    """Kırpma penceresinin sol/üst kenarı (kaynak pikseli, ondalıklı) için ffmpeg ifadesi.
    Yol, en fazla eps piksel sapmayla sadeleştirilir; formül kısa kalır."""
    lim = max(0.0, src_len - crop_len)
    pos = [min(lim, max(0.0, c * src_len - crop_len / 2)) for _, c in track]
    if not pos:
        return "0"
    dt = 1.0 / AFPS
    T = f"({var}/{fps:.6f})"
    # Sahne geçişlerinde yolu böl; her parçayı ayrı sadeleştir
    cuts = sorted(c for c in cut_idx if 0 < c < len(pos))
    bounds = [0] + cuts + [len(pos)]
    knots = []  # (zaman, konum, bu noktada atlama mı)
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = [(i * dt, pos[i]) for i in range(a, b)]
        for j, idx in enumerate(_simplify(seg, eps)):
            knots.append((seg[idx][0], seg[idx][1], j == 0 and a > 0))
    terms = [f"{knots[0][1]:.3f}"]
    for (t0, p0, _), (t1, p1, jump) in zip(knots, knots[1:]):
        if jump:                       # sahne geçişi: anında atla
            if abs(p1 - p0) >= 1e-3:
                terms.append(f"({p1 - p0:.3f})*gte({T},{t1:.4f})")
            continue
        if abs(p1 - p0) < 1e-3 or t1 <= t0:
            continue
        terms.append(f"({(p1 - p0) / (t1 - t0):.4f})*clip({T}-{t0:.4f},0,{t1 - t0:.4f})")
    return f"min({lim:.3f},max(0,{_grouped_sum(terms)}))"


def _grouped_sum(terms, k=16):
    """Uzun toplamı iç içe parantezli küçük gruplara böler.
    ffmpeg 7+ tek zincirde ~100'den fazla terimi reddediyor ("Missing ')'")."""
    while len(terms) > k:
        terms = ["(" + "+".join(terms[i:i + k]) + ")" for i in range(0, len(terms), k)]
    return "+".join(terms)


def subpixel_graph(track, cut_idx, axis, src_w, src_h, crop_w, crop_h, fps, out_w, out_h, subpixel=True, eps=0.15):
    """Takip için filtre zinciri (giriş [0:v], çıkış [v0]).
    Görüntü önce 8-bit 4:4:4'e çevrilir: renk kanalları parlaklıkla aynı yoldan geçer, bazı ffmpeg
    sürümlerindeki yarım çözünürlüklü renk / 10-bit hataları devre dışı kalır."""
    if not subpixel:
        return integer_graph(track, cut_idx, axis, src_w, src_h, crop_w, crop_h, fps, out_w, out_h, eps)
    if axis == "x":
        en = track_expr(track, cut_idx, src_w, crop_w + 2, fps, "n", eps)
        ei = track_expr(track, cut_idx, src_w, crop_w + 2, fps, "(in-1)", eps)
        f = f"(({ei})-floor({ei}))"   # kesirli kısım; perspective'in 'in' sayacı 1'den başladığı için (in-1)
        return (f"[0:v]format=yuv444p,crop=w={crop_w + 2}:h={crop_h}:x='floor({en})':y=0:exact=1,"
                f"perspective=x0='{f}':y0=0:x1='W+{f}':y1=0:x2='{f}':y2=H:x3='W+{f}':y3=H:"
                f"interpolation=cubic:eval=frame,"
                f"crop=w={crop_w}:h={crop_h}:x=0:y=0:exact=1,scale={out_w}:{out_h},setsar=1[v0]")
    en = track_expr(track, cut_idx, src_h, crop_h + 2, fps, "n", eps)
    ei = track_expr(track, cut_idx, src_h, crop_h + 2, fps, "(in-1)", eps)
    f = f"(({ei})-floor({ei}))"
    return (f"[0:v]format=yuv444p,crop=w={crop_w}:h={crop_h + 2}:x=0:y='floor({en})':exact=1,"
            f"perspective=x0=0:y0='{f}':x1=W:y1='{f}':x2=0:y2='H+{f}':x3=W:y3='H+{f}':"
            f"interpolation=cubic:eval=frame,"
            f"crop=w={crop_w}:h={crop_h}:x=0:y=0:exact=1,scale={out_w}:{out_h},setsar=1[v0]")


def integer_graph(track, cut_idx, axis, src_w, src_h, crop_w, crop_h, fps, out_w, out_h, eps=0.15):
    """Güvenli yedek: tam piksel kırpma (4:4:4 üzerinde, tek piksel adımlarla)."""
    if axis == "x":
        en = track_expr(track, cut_idx, src_w, crop_w, fps, "n", eps)
        return (f"[0:v]format=yuv444p,crop=w={crop_w}:h={crop_h}:x='round({en})':y=0:exact=1,"
                f"scale={out_w}:{out_h},setsar=1[v0]")
    en = track_expr(track, cut_idx, src_h, crop_h, fps, "n", eps)
    return (f"[0:v]format=yuv444p,crop=w={crop_w}:h={crop_h}:x=0:y='round({en})':exact=1,"
            f"scale={out_w}:{out_h},setsar=1[v0]")


def subpixel_selftest(ffmpeg):
    """Piksel altı kaydırma bu bilgisayardaki ffmpeg'de renkleri bozuyor mu? (True = güvenli)
    Hareketli renkli test görüntüsünü hem piksel altı hem tam piksel yöntemle işler, renkleri karşılaştırır."""
    W, H, cw, fps, n = 320, 180, 96, 24, 24
    track = [(k / AFPS, (20 + k * 9.7 + (cw + 2) / 2) / W) for k in range(int(n / fps * AFPS) + 2)]
    outs = []
    for sub in (True, False):
        g = subpixel_graph(track, set(), "x", W, H, cw, H, fps, cw, H, subpixel=sub)
        for src_fmt in ("yuv420p10le", "yuv420p"):
            cmd = [ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={fps}:d=1",
                   "-filter_complex", f"[0:v]format={src_fmt}[s];" + g.replace("[0:v]", "[s]", 1),
                   "-map", "[v0]", "-frames:v", str(n), "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0 or len(r.stdout) != cw * H * 3 * n:
                return False if sub else True
            outs.append(r.stdout)
    # outs: [sub10, sub8, int10, int8] -> piksel altı ile tam piksel arasındaki fark küçük olmalı
    def diff(a, b):
        step = 7
        return sum(abs(x - y) for x, y in zip(a[::step], b[::step])) / (len(a) // step)
    return diff(outs[0], outs[2]) < 12 and diff(outs[1], outs[3]) < 12
