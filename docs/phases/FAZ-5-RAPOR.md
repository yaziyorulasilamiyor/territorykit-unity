# Faz 5 — Havuzlama, viewport streaming, seçim

Tarih: 2026-08-18 · Durum: Tamamlandı
Dal: feat/phase-5-streaming-pooling · Commit sayısı: 10

## Ne yapıldı
- `TerritoryPool`: GameObject ve Mesh için iki bağımsız yığın, `WarmUp` sonrası `Checkout`/`Release` hiç tahsis yapmıyor; `PooledTerritory`/`VisibleTerritory` `readonly struct` yapıldı (sınıf olsaydı checkout başına bir `new`)
- `ViewportStreamer`: kamera kutusu → `/viewport` → pool diff'i; en fazla bir istek uçuşta, üstüne gelen istek eskisini iptal eder; LOD değişimi görüneni tamamen yeniden yükler
- `LodHysteresis`: saf, iki eşikli (coarsen/refine) durum makinesi; `ViewportStreamer` "son istenen" seviyeyi izliyor — "son uygulanan"ı değil (aşağıya bakın)
- `TerritoryPicker`: bbox ön-eleme + CPU nokta-üçgen; `MeshCollider` yok
- `MeshDiskCache`: revizyon+bölge+lod anahtarlı, atomik yazma, bozuk kayıt kendini onarıyor
- `TerritoryClient`: `GetViewportAsync`/`GetMeshDataAsync`/`GetMeshDataBatchAsync` eklendi, eski metodlar değişmedi
- `Samples~/BasicMap`: `ViewportStreamer` + yeni `BasicMapCameraController` (pan/zoom/tıkla-vurgula)
- `scripts/build_lod.py`: göreli `--output` düzeltmesi (Faz 4'ün bıraktığı tuzak)

## Nasıl doğrulandı
| Kontrol | Komut | Sonuç |
|---|---|---|
| EditMode | `Unity -runTests -testPlatform EditMode` | **65 geçti**, 0 atlandı |
| PlayMode | `Unity -runTests -testPlatform PlayMode` | **25 geçti**, 0 atlandı (`-nographics` render testlerini kırıyor — kullanılmadı) |
| Python (build_lod fix) | `pytest tests/test_lod_scripts.py` | **44 geçti** |
| **A1 — GC bütçesi** | 500 checkout/release, `GC.GetAllocatedBytesForCurrentThread` | **0 byte/döngü** (ısınma sonrası); kaynak yok çünkü struct'lar tahsis etmiyor, `Stack<T>.Push/Pop` etmiyor, yol üzerinde async/closure yok |
| **A3 — CPU picking belleği** | Gerçek `high` build'den hesap (240.379 vertex/238.969 üçgen/81 il) | **52,1 KB/bölge** ortalama; 973 ilçe ≈ **49,5 MB**, 42.210 mahalle ≈ **2,10 GB** (aynı anda tutulursa) |
| **A5 — LOD salınım** | Yavaş yukarı+aşağı süpürme, iki sınırda jitter | Tam **4 geçiş** (high→medium→low→medium→high), sıçrama yok |
| **A4 — draw call** | Ölçülemedi, bkz. Tıkanmalar | Akıl yürütmeyle: görünen bölge sayısı kadar |

## Kararlar ve gerekçeleri
1. **CPU nokta-üçgen picking, `MeshCollider` değil** — havuzlanan mesh içeriği sürekli değiştiği için `MeshCollider` her checkout'ta yeniden pişirilirdi. Alternatif: `MeshCollider` — reddedildi, ayrıca A3'ün gösterdiği gibi CPU tarafı zaten ~40 binlik ölçekte kırılıyor; fizik collider'ın ek belleği bunu daha erken kırardı.
2. **`PooledTerritory`/`VisibleTerritory` `readonly struct`** — A1'in "≈0" hedefini gerçek sıfıra taşıdı. Alternatif: sınıf olarak bırakıp bütçeyi gevşetmek — reddedildi, ölçülebilir ve ücretsiz bir kazanç varken bütçeyi gevşetmenin gerekçesi yok.
3. **Hysteresis "son istenen" seviyeye göre çalışıyor, "son uygulanan"a göre değil** — ilk yazımda ikisi aynıydı ve bir kilitlenmeye yol açtı: LOD değişince her frame hâlâ eski seviyeyi görüp isteği kendi kendine iptal ediyordu, ağdan bir frame'den yavaş hiçbir yanıt asla tamamlanamıyordu. `ViewportStreamerTests` bunu yakaladı.

## Bilinen eksikler ve riskler
- **A4 draw call sayısı ölçülemedi** (Tıkanma, aşağıda). Muhakeme güçlü ama doğrulanmamış.
- CPU picking'in bellek maliyeti il/ilçe ölçeğinde (81/973) önemsiz, mahalle ölçeğinde (42.210) aynı anda tutulan bölge sayısı sınırlanmazsa yüzlerce MB–GB'a çıkabilir — bu tasarımın kırıldığı nokta, çözülmedi.
- LOD histerezis varsayılan eşikleri (60k/45k/180k/140k) bu sahnenin ölçeğine göre seçildi, doğrulanmış evrensel sabitler değil.
- Disk cache boyut sınırı/eviction yok (kapsam dışı bırakıldı, 81×3 seviye küçük).
- TKMB `entryEncoding: gzip` hâlâ okunmuyor (Faz 4'ten devam eden, bu fazda büyümedi).
- 2022.3 LTS hâlâ hiç çalıştırılmadı (Faz 4'ten devam eden).

## Tıkanmalar
Gerçek GPU draw call sayısını `-batchmode`'da ölçmek: `UnityStats.drawCalls`, `ProfilerRecorder(ProfilerCategory.Render, "Draw Calls Count")` ve eski `Recorder` API'si denendi — üçü de PlayMode frame döngüsü içinden, `camera.Render()` + `RenderTexture` ile bile sıfır/geçersiz örnek döndürdü (eski `Recorder` sayaç ateşlediğini gösteriyor ama sayıyı vermiyor). RenderCoverageTests'in kanıtladığı gibi render'ın kendisi doğru çalışıyor; sorun sadece istatistik toplama. İnteraktif Editor oturumunda Profiler/Frame Debugger ile doğrulanmalı — kullanıcıya soruluyor.

## Sonraki faza hazırlık
Faz 6 için önkoşul hazır. Devreden: A4'ün interaktif doğrulaması, 2022.3 testi, TKMB gzip, disk cache eviction kararı.

## Değişen dosyalar
- `Runtime/`: `TerritoryPool`, `ViewportStreamer`, `TerritoryPicker`, `LodHysteresis`, `MeshDiskCache` (yeni); `TerritoryClient`, `TerritoryMapPlacement` (güncellendi)
- `Tests/Editor/`: `TerritoryPickerTests`, `LodHysteresisTests`, `MeshDiskCacheTests`, `GroundPlaneTests`
- `Tests/Runtime/`: `TerritoryPoolTests`, `ViewportStreamerTests`, `TerritoryClientCacheTests`
- `Tests/Common/MockGeometryServer.cs`: `/viewport` desteği eklendi
- `Samples~/BasicMap/`: `BasicMap.unity`, yeni `BasicMapCameraController.cs`, `README.md`
- `scripts/build_lod.py` + Python testi, iki paket `README.md`, `package.json`
