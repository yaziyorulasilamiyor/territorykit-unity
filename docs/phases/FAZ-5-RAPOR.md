# Faz 5 — Havuzlama, viewport streaming, seçim

Tarih: 2026-08-19 · Durum: Tamamlandı (iki inceleme turu dahil)
Dal: feat/phase-5-streaming-pooling · Commit sayısı: 22

## Ne yapıldı
- `TerritoryPool`: GameObject ve Mesh için iki bağımsız yığın; checkout/release'te transform kimliğe sıfırlanıyor, çift release reddediliyor (aynı Mesh iki bölgeye verilemez)
- `ViewportStreamer`: kamera kutusu → `/viewport` → pool diff'i. Her tik bir **generation** taşıyor; her await sonrası ve commit öncesi doğrulanıyor. Commit **transactional**: yeni slotların hepsi hazır olmadan görünür kümeye hiçbiri geçmiyor. İstek 200'lük parçalara bölünüyor, başarısız istek geri alınıyor. `Start` artık iptal dışındaki hataları da yakalayıp bileşeni sessizce ölü bırakmak yerine çözümü söyleyen bir hata basıyor
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
| PlayMode | **41 geçti**, 0 atlandı (`-nographics` render testlerini kırıyor — kullanılmıyor) |
| Python | `pytest tests/test_lod_scripts.py` → **44 geçti** |
| **A1 — GC kapısı** | Havuz **0 byte/döngü**; boştaki tik **0 byte/tik**; istek başlatma **4,0–7,7 KB**; **tam tik** (async devam dahil) 20 minik mesh için **25,6 KB**, 20 gerçek boyutlu mesh için **tik başına 1 gen-0 toplama ≈ 2,2 MB** |
| **A3 — CPU picking belleği** | **52,1 KB/bölge** (gerçek `high` build: 240.379 vertex / 238.969 üçgen / 81 il); 973 ilçe ≈ **49,5 MB**, 42.210 mahalle ≈ **2,10 GB** |
| **A4 — draw call** | **83 draw call / 83 batch / 83 SetPass**, 81 bölge görünür (gerçek dataset, D3D12, 1600×1200) |
| **A5 — LOD salınım** | Yavaş süpürme + iki sınırda jitter → tam **4 geçiş**, sıçrama yok |
| FPS | **60,0 FPS**, en kötü kare **17,4 ms** (vsync'e takılı — bu bir *taban*, tavan değil) |

**A1 — kriter streaming için KARŞILANMIYOR; ölçüm üç turda üç kez eksik çıktı.** Sırasıyla: (1) havuz ölçülüyordu, streamer ölçülmüyordu; (2) sayaç (`GC.GetAllocatedBytesForCurrentThread`) Unity Mono'sunda **her zaman 0 döndürüyordu** — 100 KB'lık dizi dahil, yani "0 byte" hiçbir şey ölçmüyordu; (3) ölçüm tikin yalnız senkron başlangıcını kapsıyordu, asıl iş (HTTP, decode, mesh apply) sonraki karelerdeki fire-and-forget devamda. Şimdi tik `TickObserver` ile **gerçekten tamamlanana kadar** ölçülüyor, kare gürültüsü boştaki taban çıkarılarak ayıklanıyor (taban: 0 B/kare).

Sonuç dürüstçe: **duran kamera ve havuz sıfır tahsis; streaming değil.** 20 minik mesh takasında 25,6 KB/tik (sabit maliyet). 20 **gerçek boyutlu** mesh'te (2.968 vertex, ~41 KB kodlanmış — gerçek ortalama 2.967) byte cinsinden ölçmek mümkün değil: tek bir tik gen-0 toplaması tetikliyor, heap göstergesi de toplama boyunca ölçemiyor. Bu yüzden o vaka **toplama sayısıyla** kapıya bağlandı: tik başına 1 toplama, gözlenen heap deltası ~2,2 MB (taban). Kabaca **yüklenen bölge başına ~110 KB geçici çöp**, pan başına megabaytlar. Kapı gevşetilmedi; hassasiyeti yükü 3'e katlayarak doğrulandı (4 toplamaya çıkıyor ve test düşüyor). Bu geçici çöptür, sızıntı değil — zamanla değil, viewport kenarını geçen bölge sayısıyla orantılı.

Çalışan tek araç managed heap göstergesi (`GC.GetTotalMemory`). `AllocationMeasurement` önce 1 MiB'lık bilinen tahsisle **sayacın çalıştığını kanıtlıyor**, sonra `GC.CollectionCount` ile pencerede toplama olmadığını doğruluyor. `GC.GetTotalAllocatedBytes` bu runtime'da **yok**; `ProfilerRecorder(Memory, "GC Allocated In Frame")` hem batchmode'da hem Development Player'da 300 karede 1–2 örnek toplayıp 0 döndürüyor — benchmark artık bunu `unavailable(samples=N)` diye yazıyor, sahte bir sıfır olarak değil.

**A4 — SetPass = draw call, yani hiç batch'leme yok.** Bölge başına benzersiz `Mesh` bunun doğrudan sonucu: dynamic batching bu boyutta farklı mesh'leri birleştiremez, GPU instancing aynı mesh ister, SRP Batcher built-in pipeline'da devrede değil. `MaterialPropertyBlock` burada suçlu **değil** — benzersiz mesh'ler zaten batch'lemeyi engelliyor. **973 bölgede ~975 draw call olur**; çözmüyoruz, biliyoruz.

## Kararlar ve gerekçeleri
1. **CPU nokta-üçgen picking, `MeshCollider` değil** — havuzlanan mesh içeriği sürekli değiştiği için `MeshCollider` her checkout'ta yeniden pişerdi. Bedeli A3'te ölçülü: CPU buffer'ları bellekte tutmak ~40 binlik ölçekte kırılıyor; collider'ın ikinci kopyası daha erken kırardı.
2. **Cache anahtarları sanitize değil hash'leniyor** — `:` → `_` dönüşümü dört ayrı yoldan çakışıyordu (`a:b`/`a_b`, büyük/küçük harf, `CON`/`NUL`, `.`/`..`). Çakışma çökme değil, **yanlış bölgenin mesh'ini doğru sanıp sunmak**: TKMS bölge kimliğini içinde taşımadığı için decoder yakalayamaz. 96 bit'e kısaltmak zorunluydu — tam digest ile yol 360 karakteri aşıp Windows `MAX_PATH`'e takılıyordu.
3. **Havuz slotları versiyonlu** — "çıkışta mı?" bayrağı ABA'yı kapatmıyordu: bırak, aynı nesneyi tekrar al, sonra bayat kopyayı tekrar bırak — nesne gerçekten çıkışta olduğu için bayrak evet diyor ve aynı Mesh iki bölgeye gidiyor. Sayaç checkout **ve** release'te artıyor, böylece bir bayrak değil tek bir checkout'un kimliği oluyor. Alternatif (bayrağı korumak) reddedildi: sessiz görsel bozulmayı kapatmıyor.

## Bilinen eksikler ve riskler
- **"Steady state'te sıfır tahsis" hedefi streaming için karşılanmıyor.** Duran kamera ve havuz sıfır; bölge yükleyen tik gerçek mesh boyutunda tik başına megabaytlar üretiyor (yukarıda). Kapı gevşetilmedi, gerçek sayı yazıldı.
- CPU picking belleği il/ilçe ölçeğinde önemsiz, **mahalle ölçeğinde (42.210) tasarım kırılıyor** — aynı anda tutulan bölge sayısı sınırlanmadıkça GB'lara çıkar. Çözülmedi, bilinerek bırakıldı.
- **Cache'te kabul edilen artık risk:** TKMS bölge kimliğini gövdesinde taşımadığı için, hash çakışması ya da dosya kurcalaması hâlinde "yanlış ama geçerli" bir mesh okunabilir ve decoder bunu yakalayamaz. Kabul gerekçesi: 96-bit anahtarda 42.210 öğe için çakışma olasılığı **~1,1e-20** (ihmal edilebilir); kurcalama ise yerel dosya sistemine yazma erişimi gerektirir, o noktada zaten kaybedilmiş bir güven sınırıdır. Ölçülen ikinci sayı: 79 karakterlik gerçekçi kökte tam anahtar + temp eki **~197 karakter**, `MAX_PATH` altında. Kod eklenmedi.
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
