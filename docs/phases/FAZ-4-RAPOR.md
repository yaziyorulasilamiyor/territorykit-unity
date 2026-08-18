# Faz 4 — Unity paketi, temel render

Tarih: 2026-08-18 · Durum: Tamamlandı
Dal: feat/phase-4-unity-render · Commit sayısı: 15

## Ne yapıldı
- `TkmsHeader` + `MeshDecoder`: worker thread'de tam doğrulama (sonlu koordinat, index aralığı,
  bbox'ın **gerçek** vertex sınırlarına eşitliği); ana thread'de yalnız 4 Mesh çağrısı + 2 kopya
- `TerritoryClient`: metadata, sayfalı liste, tekil mesh, TKMB batch; `nativeData` ile kopyasız
  okuma, `Abort()` ile gerçek iptal, cache **yok** (Karar 3). `LodPolicy` bayrakları yorumlar ama
  **seçim yapmaz**; `TerritoryMapPlacement` yerleştirmeyi ve kamera yönünü tek yerde tutar
- `TerritoryMapRenderer` + `Samples~/BasicMap`: 81 il tek batch isteğiyle iniyor ve çiziliyor
- `capture_sample.ps1` + `CaptureSample.cs`: uvicorn → Unity batchmode → PNG;
  `check_lod_report.py`'a dataset sentinel'i (`--expect-high-vertices`), CI'da bağlı

## Nasıl doğrulandı
| Kontrol | Komut | Sonuç |
|---|---|---|
| EditMode | `Unity -runTests -testPlatform EditMode` | **32 geçti**, 0 atlandı |
| PlayMode (sahte sunucu + render) | `-testPlatform PlayMode` | **9 geçti**, 0 atlandı |
| Python (sentinel dahil) | `pytest -q --cov` | **384 geçti**, kapsam **%95** |
| Lint/tip | `ruff check` · `ruff format --check` · `mypy src/` | Temiz · 277 dosya · temiz |
| LOD zinciri sıfırdan | `build_lod.py` → `publish_dataset.py` | high **240.379 vertex / 238.969 üçgen / 81 il** — Faz 2 ile birebir |
| Örnek sahne | `capture_sample.ps1` | **81 çizildi, 0 eksik**, dolu piksel **%34,60**, tek batch isteği |

**El yönü (Faz 1'in açık notu) iki bağımsız kanıtla kapandı.** EditMode: CW üçgenin yerel normali
`−Z`, X'te +90° dönüş sonrası dünya `+Y`. PlayMode: culling **açık**, sahne RenderTexture'a çizilip
piksel sayılıyor — TKMS'in CW'si %40–50 bandında görünür, ters çevrilmiş kontrol **%0**. İkincisi
olmadan birincisi tutarlı biçimde yanlış olabilirdi. [Ekran görüntüsü](faz-4-ornek-sahne.png).

## Kararlar ve gerekçeleri
1. **Vertex'ler 3 float'a genişletiliyor, 2'ye değil** — ölçüldü: `Position` için `Float32 × 2`
   D3D12'de hem kabul edildi hem 3 boyutluyla birebir aynı kaplamayla render etti (%39,06). Yine de
   reddedildi: `SupportsVertexAttributeFormat` cihaz bağımlı, Faz 5'in `MeshCollider`'ını test
   edilmemiş zemine koyar, kazancı da ana thread'de değil worker thread'deydi — asıl kısıtı
   iyileştirmiyordu. Alternatif: cihaz kontrollü çift yol, tek bayrak için iki kod yolu.
2. **`package.json` fiilen test edilen sürümü yazıyor (`6000.1`)** — makinede 2022.3 yok; manifest
   iddia dosyasıdır. 2023+ API kullanılmadı; 2022.3 hedefi README'de **ayrı ve doğrulanmamış**.
3. **Faz 4'te cache yok; Faz 5 revizyon-anahtarlı disk cache'i kullanacak** — `UnityWebRequest`'in
   C# referans kaynağında cache yok, `Caching` yalnız AssetBundle için, ETag/304 işlenmiyor; mesh
   URL'leri değişmez olduğundan koşullu istek saf gecikme olurdu. `Accept-Encoding` elle set
   edilmiyor: Unity ayarlıyor, `.tkms.gz` bedavaya geliyor.

## Bilinen eksikler ve riskler
- **2022.3 hiç çalıştırılmadı**; kod 2023+ API içermiyor ama bu doğrulanmamış bir uyumluluktur.
- **`build_lod.py` göreli `--output` ile kırılıyor** (Faz 2 kodu): alt süreç `cwd`'yi
  `services/geometry-api`'ye çekiyor, göreli yol yanlış köke çözülüyor — ilk çalıştırmam bu yüzden
  düştü. CI mutlak yol geçtiği için yeşil kalıyor: sessiz bir tuzak. Faz 4 kapsamı değil, düzeltmesi
  `args.output.resolve()` kadar küçük.
- **Unity CI job'ı yok** (Faz 6). **TKMB `entryEncoding: gzip` okunmuyor** — istemci `identity`
  istiyor, gzip'li konteyner net hatayla reddediliyor; bant genişliği Faz 5'in konusu.
- **İptal `Abort()`'u ana thread'den bekliyor**; başka thread'den çağrılması belgelenmemiş. Faz
  3'ten devralınan lease yarışı ve yanlış-sınıflandırma açığı da açık kalmaya devam ediyor.

## Tıkanmalar
Yok. Yakalama zincirinde üç şey ilk denemede tutmadı, üçü de çözüldü: `localhost` → `::1`
çözülmesi, `Unity.exe` GUI-subsystem olduğu için PowerShell'in beklememesi, önceki koşumdan kalan
Unity örneğinin proje kilidini tutması.

## Sonraki faza hazırlık
Faz 5 için önkoşul **hazır**. Devreden üç karar: disk cache tasarımı (Karar 3), `LodPolicy`'nin
tüketileceği yer (`ViewportStreamer`), `AllocateWritableMeshData` ölçümü.

## Değişen dosyalar
- `packages/.../Runtime/` (11 dosya): decoder, client, TKMB, modeller, `LodPolicy`,
  `TerritoryMapPlacement`, `TerritoryMapRenderer`, 2 exception tipi; `Tests/`: `Common/`
  (fixture + sahte sunucu), `Editor/` (3), `Runtime/` (2), 3 asmdef
- `packages/.../Samples~/BasicMap/` (sahne + README), `package.json`, `README.md`;
  `unity/TerritoryKitDev/` geliştirme koşumu (paket kökünün **dışında**); `capture_sample.ps1`,
  `check_lod_report.py` (sentinel), `ci.yml`, `.gitignore`, ekran görüntüsü PNG'si
