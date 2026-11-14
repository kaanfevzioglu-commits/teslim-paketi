# Teslim Paketi

Bitmiş videoyu sürükle; Reels, YouTube, sinema gibi tüm formatlar, -14 LUFS ses,
isteğe bağlı filigranlı önizleme tek seferde hazır olsun.
Akıllı takip (Mac'te Apple Vision ile özne tespiti), piksel altı yumuşak kamera.
Dosyalar bilgisayardan çıkmaz.

## İndir ve kur

Sağdaki **Releases** bölümünden en son sürümü aç, **Assets** listesinden bilgisayarına uygun dosyayı indir.

### 🪟 Windows

1. **`TeslimPaketi-Kurulum-….exe`** dosyasını indir ve çift tıkla.
2. "Windows bilgisayarınızı korudu" uyarısı çıkarsa **Ek bilgi → Yine de çalıştır**'a bas.
3. **İleri → Kur → Bitir**. Masaüstündeki **Teslim Paketi** simgesiyle aç.

### 🍎 Mac

1. Hangi dosyayı indireceğini öğrenmek için  menüsü → **Bu Mac Hakkında**'ya bak:
   - **Apple M1/M2/M3/M4** yazıyorsa → **`…-Mac-AppleSilicon.dmg`**
   - **Intel** yazıyorsa → **`…-Mac-Intel.dmg`**
2. .dmg'yi aç, **Teslim Paketi**'ni **Uygulamalar** klasörüne sürükle.
3. Uygulamayı aç. "Doğrulanamadı" uyarısı çıkarsa **Sistem Ayarları → Gizlilik ve Güvenlik**'e gir, en altta **Yine de Aç**'a bas. Bunu yalnızca ilk açılışta bir kez yapman yeterli.

### Kullanım

Uygulama tarayıcında açılır. Videoyu sürükle, formatları seç, **Başlat**'a bas.
Başka bir şey kurmana gerek yok, ffmpeg uygulamanın içinde geliyor.

**Güncelleme:** Yeni sürümü indirip aynı şekilde kur. Eskisinin üzerine yazılır, ayarların korunur.

## Geliştirici notları

Her `main` gönderiminde GitHub Actions (Derle) Windows ve Mac (Apple Silicon + Intel) için derler ve sürümü otomatik yayınlar.

Her derlemeden önce gerçek Windows ve macOS makinelerinde duman testi çalışır
(`tests/smoke.py`): piksel altı öz-test, ortasında çözünürlük değişen kurgu,
uzun hareketli video, sessiz ses kanalı, Mac'te Apple Vision.

## Klasörler

- `app/` uygulama (Python sunucu + tarayıcı arayüzü)
- `scripts/` ffmpeg indirme ve derleme
- `tests/` duman testi
- `packaging/windows/` Windows kurulum sihirbazı (Inno Setup)
- `assets/` ikonlar
