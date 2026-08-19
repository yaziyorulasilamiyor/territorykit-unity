# Faz 5 — Havuzlama, viewport streaming, seçim

Tarih: 2026-08-19 · Durum: Tamamlandı (bir geniş inceleme turu dahil)
Dal: feat/phase-5-streaming-pooling · Commit sayısı: 17

## Ne yapıldı
- `TerritoryPool`: GameObject ve Mesh için iki bağımsız yığın; checkout/release'te transform kimliğe sıfırlanıyor, çift release reddediliyor (aynı Mesh iki bölgeye verilemez)
- `ViewportStreamer`: kamera kutusu → `/viewport` → pool diff'i. Her tik bir **generation** taşıyor; her await sonrası ve commit öncesi doğrulanıyor. Commit **transactional**: yeni slotların hepsi hazır olmadan görünür kümeye hiçbiri geçmiyor. İstek 200'lük parçalara bölünüyor, başarısız istek geri alınıyor
- `LodHysteresis`: saf, iki eşikli (coarsen/refine) durum makinesi
- `TerritoryPicker`: bbox ön-eleme + CPU nokta-üçgen; `MeshCollider` yok
- `MeshDiskCache`: revizyon+bölge+lod anahtarlı, **her bileşen SHA-256 ile kodlanıyor**, atomik yazma, bozuk kayıt kendini onarıyor
- `TerritoryClient`: `GetViewportAsync`/`GetMeshDataAsync`/`GetMeshDataBatchAsync`; eski metodlar değişmedi
- `Samples~/BasicMap`: `ViewportStreamer` + `BasicMapCameraController` (pan/zoom/tıkla-vurgula)
- `scripts/build_lod.py` göreli `--output` düzeltmesi; `scripts/measure_render.ps1` (render ölçüm hattı)

## Nasıl doğrulandı
| Kontrol | Sonuç |
|---|---|
| EditMode | **77 geçti**, 0 atlandı |
| PlayMode | **36 geçti**, 0 atlandı (`-nographics` render testlerini kırıyor — kullanılmıyor) |
| Python | `pytest tests/test_lod_scripts.py` → **44 geçti** |
| **A1 — GC kapısı** | Havuz **0 byte/döngü**; boştaki streamer tiki **0 byte/tik**; istek atan tik **4,0–7,7 KB/istek** (aşağıda) |
| **A3 — CPU picking belleği** | **52,1 KB/bölge** (gerçek `high` build: 240.379 vertex / 238.969 üçgen / 81 il); 973 ilçe ≈ **49,5 MB**, 42.210 mahalle ≈ **2,10 GB** |
| **A4 — draw call** | **83 draw call / 83 batch / 83 SetPass**, 81 bölge görünür (gerçek dataset, D3D12, 1600×1200) |
| **A5 — LOD salınım** | Yavaş süpürme + iki sınırda jitter → tam **4 geçiş**, sıçrama yok |
| FPS | **60,0 FPS**, en kötü kare **17,4 ms** (vsync'e takılı — bu bir *taban*, tavan değil) |

**A1 — asıl bulgu ölçüm aracıydı.** İlk kapı `GC.GetAllocatedBytesForCurrentThread` kullanıyordu; bu API Unity'nin Mono'sunda derleniyor, çalışıyor ve **her zaman 0 döndürüyor** — 100 KB'lık dizi, 1000 kutulama, `CancellationTokenSource`, hepsi 0. Yani rapor edilen "0 byte" hiçbir şey ölçmüyordu. `ProfilerRecorder(Memory, "GC Allocated In Frame")` de batchmode'da geçerli ama boş. Çalışan araç managed heap göstergesi (`GC.GetTotalMemory`); `AllocationMeasurement` önce 1 MiB'lık bilinen tahsisle **sayacın çalıştığını kanıtlıyor**, sonra `GC.CollectionCount` ile pencerede toplama olmadığını doğruluyor. İstek başına 4–7,7 KB'ın kaynağı: `UnityWebRequest` + `DownloadHandlerBuffer`, beş iç içe async metodun state machine'leri + `TaskCompletionSource`, linked `CancellationTokenSource`, ve `FormatBbox`'ın URL'i. Bu **kare başına değil istek başına**; 0,2 s tik aralığında sürekli pan ~5 istek/s ≈ 25 KB/s, duran kamerada sıfır. (Süreç geneli sayaç olduğu için içerideki sahte sunucunun payı da dahil — üst sınır.)

**A4 — SetPass = draw call, yani hiç batch'leme yok.** Bölge başına benzersiz `Mesh` bunun doğrudan sonucu: dynamic batching bu boyutta farklı mesh'leri birleştiremez, GPU instancing aynı mesh ister, SRP Batcher built-in pipeline'da devrede değil. `MaterialPropertyBlock` burada suçlu **değil** — benzersiz mesh'ler zaten batch'lemeyi engelliyor. **973 bölgede ~975 draw call olur**; çözmüyoruz, biliyoruz.

## Kararlar ve gerekçeleri
1. **CPU nokta-üçgen picking, `MeshCollider` değil** — havuzlanan mesh içeriği sürekli değiştiği için `MeshCollider` her checkout'ta yeniden pişerdi. Bedeli A3'te ölçülü: CPU buffer'ları bellekte tutmak ~40 binlik ölçekte kırılıyor; collider'ın ikinci kopyası daha erken kırardı.
2. **Cache anahtarları sanitize değil hash'leniyor** — `:` → `_` dönüşümü dört ayrı yoldan çakışıyordu (`a:b`/`a_b`, büyük/küçük harf, `CON`/`NUL`, `.`/`..`). Çakışma çökme değil, **yanlış bölgenin mesh'ini doğru sanıp sunmak**: TKMS bölge kimliğini içinde taşımadığı için decoder yakalayamaz. 96 bit'e kısaltmak zorunluydu — tam digest ile yol 360 karakteri aşıp Windows `MAX_PATH`'e takılıyordu.
3. **Tik hem generation-guarded hem transactional** — iptal tek başına yetmiyor: `Task.Run` başladıktan sonra geri sarılmıyor, yani eski tik geçerli bir sonuçla commit bloğuna varıyordu. Zamanlamaya dayalı test bunu **yakalayamadı** (bozuk kodda da geçti); ancak `AfterFetchObserver` seam'iyle deterministik hale gelince kırmızıya döndü — Faz 4'ün öğrendiği dersin tekrarı.

## Bilinen eksikler ve riskler
- CPU picking belleği il/ilçe ölçeğinde önemsiz, **mahalle ölçeğinde (42.210) tasarım kırılıyor** — aynı anda tutulan bölge sayısı sınırlanmadıkça GB'lara çıkar. Çözülmedi, bilinerek bırakıldı.
- LOD histerezis eşikleri (60k/45k/180k/140k) bu sahnenin ölçeğine göre; doğrulanmış evrensel sabit değil.
- FPS vsync'e takılı: 60,0 bir taban. `vSyncCount=0` + `targetFrameRate` ikisi birden verilmesine rağmen D3D12 swap chain sınırladı; gerçek başlık boşluğu ölçülmedi.
- LOD değişiminde tik kısa süre iki kat slot tutuyor (hazırla-sonra-bırak sırasının bedeli); havuz bir kez ~2× büyüyebilir.
- Disk cache eviction/limit yok; TKMB `entryEncoding: gzip` okunmuyor; 2022.3 LTS hiç çalıştırılmadı (son ikisi Faz 4'ten devrediyor).

## Tıkanmalar
Yok. İnceleme "NaN hatası" engelleyicisi tam stack trace ile `UnityEditor.TransformManipulator.SetPositionDelta`'ya — Editor'ün kendi Move gizmo'suna — bağlandı; izde runtime kodumuz yok, **ürün hatası değil**. Yine de bağımsız gerekçeyle iki şey yapıldı: havuz transform'u sıfırlıyor ve kamera/ray matematiği sonlu olmayan değerleri reddediyor. Örnek sahne README'sinde bölge nesnelerinin kodla konumlandığı, Scene view'da Move aracıyla taşınmaması gerektiği yazılı.

## Merge sonrasına bırakılanlar
Gerçek ADM2/ADM3 bellek ölçümü (52,1 KB ADM1'den kaba projeksiyon; Unity/GPU toplam belleği değil) · cache eviction/disk limiti · tekrarlanan cursor koruması.

## Sonraki faza hazırlık
Faz 6 için önkoşul hazır. Devreden: yukarıdaki üç madde, 2022.3 doğrulaması, TKMB gzip.

## Değişen dosyalar
- `Runtime/`: `TerritoryPool`, `ViewportStreamer`, `TerritoryPicker`, `LodHysteresis`, `MeshDiskCache` (yeni); `TerritoryClient`, `TerritoryMapPlacement`, `TerritoryMapRenderer` (güncellendi)
- `Tests/Editor/`: `TerritoryPickerTests`, `LodHysteresisTests`, `MeshDiskCacheTests`, `GroundPlaneTests`
- `Tests/Runtime/`: `TerritoryPoolTests`, `ViewportStreamerTests`, `TerritoryClientCacheTests`, `AllocationMeasurement`
- `Tests/Common/MockGeometryServer.cs`: `/viewport` + 200'lük batch sınırı
- `Samples~/BasicMap/`, paket `README.md`, `package.json`
- `unity/TerritoryKitDev/Assets/Benchmark/` + `Assets/Editor/BuildBenchmark.cs`, `scripts/measure_render.ps1`, `scripts/build_lod.py`
