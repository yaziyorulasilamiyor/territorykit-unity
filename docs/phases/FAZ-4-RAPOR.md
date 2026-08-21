# Faz 4 — Unity paketi, temel render

Tarih: 2026-08-18 · Durum: Tamamlandı (bir inceleme turu dahil)
Dal: feat/phase-4-unity-render · Commit sayısı: 20

## Ne yapıldı
- `TkmsHeader` + `MeshDecoder`: worker thread'de tam doğrulama (sonlu koordinat, index aralığı, bbox'ın
  **gerçek** vertex sınırlarına eşitliği); ana thread'de yalnız 4 Mesh çağrısı + 2 kopya
- `TerritoryClient`: metadata, sayfalı liste, tekil mesh, TKMB batch; `nativeData` ile handler
  buffer'ına tahsissiz erişim ve istek kapanmadan önce sahipli `NativeArray`'e tek açık kopya,
  `Abort()` ile gerçek iptal, cache **yok** (Karar 3). `LodPolicy` bayrakları yorumlar ama **seçim
  yapmaz**; `TerritoryMapPlacement` yerleştirme ve kamera yönünü tek yerde tutar
- `TerritoryMapRenderer` + `Samples~/BasicMap` (81 il, tek batch); `capture_sample.ps1` ile uvicorn →
  Unity batchmode → PNG; `check_lod_report.py`'a CI'da bağlı dataset sentinel'i

## Nasıl doğrulandı
| Kontrol | Komut | Sonuç |
|---|---|---|
| EditMode | `Unity -runTests -testPlatform EditMode` | **32 geçti**, 0 atlandı |
| PlayMode (sahte sunucu, render, iptal) | `-testPlatform PlayMode` | **12 geçti**, 0 atlandı |
| Python (sentinel dahil) | `pytest -q --cov` | **384 geçti**, kapsam **%95** |
| Lint/tip | `ruff check` · `ruff format --check` · `mypy src/` | Temiz · 277 dosya · temiz |
| LOD zinciri sıfırdan | `build_lod.py` → `publish_dataset.py` | high **240.379 vertex / 238.969 üçgen / 81 il** — Faz 2 ile birebir |
| Örnek sahne | `capture_sample.ps1` | **81 çizildi, 0 eksik**, dolu piksel **%34,60**, tek batch isteği |

**El yönü (Faz 1'in açık notu) iki bağımsız kanıtla kapandı.** EditMode: CW üçgenin yerel normali `−Z`,
X'te +90° dönüş sonrası dünya `+Y`. PlayMode: culling açık, RenderTexture'a çizilip piksel sayılıyor —
CW %40–50 bandında görünür, ters çevrilmiş kontrol **%0**. [Ekran görüntüsü](faz-4-ornek-sahne.png).

## İnceleme turu — iki hata
1. **İptal yolunda mesh sızıntısı (engelleyici).** `Task.Run` başladıktan sonra gelen iptal batch
   mesh'leri üretildikten *sonra* çarpıyordu; hiçbir şey onlara sahip olmadığı için hepsi sızıyordu —
   `NativeArray` tarafı doğruydu, mesh devri değildi. Sahiplik artık `finally` altında geçiyor.
2. **`CancellationToken.Register` geri çağrısı iptal eden thread'de koşuyordu**, `isDone` ve `Abort()`
   ise Unity API'si. Latent değil, ölçüldü: worker thread'den iptal eski kodda `Cancel()` içinde
   **"get_result can only be called from the main thread"** fırlatıyor — abort hiç olmuyor, istek sonuna
   kadar koşuyor. Token artık isteği başlatan thread'de yoklanıyor.

Her iki test düzeltmesinden **önceki** koda karşı koşuldu ve orada kırmızı (2 sızan mesh; yukarıdaki
exception). İkisi de zamanlama yerine `internal` seam kullanıyor: zamanlamaya dayalı bir sürüm bozuk
kodda da geçerdi — iki hatanın ilk turda hayatta kalma sebebi bu.

## Kararlar ve gerekçeleri
1. **Vertex'ler 3 float'a genişletiliyor, 2'ye değil** — ölçüldü: `Float32 × 2` pozisyon D3D12'de kabul
   edildi ve aynı kaplamayla render etti (%39,06). Reddedildi: cihaz bağımlı, Faz 5'in
   `MeshCollider`'ını test edilmemiş zemine koyar, kazancı da worker thread'deydi.
2. **`package.json` fiilen test edilen sürümü yazıyor (`6000.1`)** — makinede 2022.3 yok; manifest
   iddia dosyasıdır. 2023+ API kullanılmadı; 2022.3 hedefi README'de **ayrı ve doğrulanmamış**.
3. **Faz 4'te cache yok; Faz 5 revizyon-anahtarlı disk cache'i kullanacak** — `UnityWebRequest`'in C#
   kaynağında cache yok, `Caching` yalnız AssetBundle için, ETag/304 işlenmiyor; URL'ler zaten değişmez.

## Bilinen eksikler ve riskler
- **2022.3 hiç çalıştırılmadı**; kod 2023+ API içermiyor ama bu doğrulanmamış bir uyumluluktur.
- **`build_lod.py` göreli `--output` ile kırılıyor** (Faz 2 kodu): alt süreç `cwd`'yi değiştiriyor; CI
  mutlak yol geçtiği için yeşil kalıyor — sessiz bir tuzak. Düzeltmesi `args.output.resolve()` kadar.
- **Unity CI job'ı yok** (Faz 6). **TKMB `entryEncoding: gzip` okunmuyor** — gzip'li konteyner net
  hatayla reddediliyor (Faz 5). Faz 3'ün lease-budama yarışı ve yanlış-sınıflandırma açığı hâlâ açık.

## Tıkanmalar
Yok. Yakalama zincirinde üç şey ilk denemede tutmadı, üçü de çözüldü: `localhost` → `::1`, `Unity.exe`
GUI-subsystem olduğu için PowerShell'in beklememesi, kalıntı Unity örneğinin proje kilidi.

## Sonraki faza hazırlık
Faz 5 için önkoşul **hazır**. Devreden üç karar: disk cache (Karar 3), `LodPolicy`'nin tüketileceği
yer (`ViewportStreamer`), `AllocateWritableMeshData` ölçümü.

## Değişen dosyalar
- `packages/.../` — `Runtime/` (12 dosya), `Tests/` (`Common/` fixture + sahte sunucu, `Editor/` 3,
  `Runtime/` 3, 3 asmdef), `Samples~/BasicMap/`, `package.json`, paket `README.md`
- `unity/TerritoryKitDev/` (paket kökünün **dışında**), `capture_sample.ps1`, `check_lod_report.py`,
  `ci.yml`, `.gitignore`, ekran görüntüsü PNG'si
