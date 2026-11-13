# Teslim Paketi

Bitmiş videoyu sürükle; Reels, YouTube, sinema gibi tüm formatlar, -14 LUFS ses,
müşteriye filigranlı önizleme ve teslim raporu tek seferde hazır olsun.
Akıllı takip (Mac'te Apple Vision ile özne tespiti), piksel altı yumuşak kamera.
Dosyalar bilgisayardan çıkmaz.

## Kurulum dosyaları

Her `v*` etiketi (ör. `v2.1.0`) GitHub Actions'ta otomatik derlenir:

- **Windows:** `TeslimPaketi-Kurulum-<sürüm>.exe` (ffmpeg gömülü, yönetici izni gerekmez)
- **Mac (Apple Silicon):** `TeslimPaketi-<sürüm>-Mac.dmg` (ffmpeg ve Apple Vision bileşeni gömülü)

Dosyalar depo sayfasında **Releases** bölümündedir. Elle derlemek için
**Actions → Derle → Run workflow**; dosyalar o çalışmanın **Artifacts** kısmına düşer.

Her derlemeden önce gerçek Windows ve macOS makinelerinde duman testi çalışır
(`tests/smoke.py`): piksel altı öz-test, ortasında çözünürlük değişen kurgu,
uzun hareketli video, sessiz ses kanalı, Mac'te Apple Vision.

## Klasörler

- `app/` uygulama (Python sunucu + tarayıcı arayüzü)
- `scripts/` ffmpeg indirme ve derleme
- `tests/` duman testi
- `packaging/windows/` Windows kurulum sihirbazı (Inno Setup)
- `assets/` ikonlar
