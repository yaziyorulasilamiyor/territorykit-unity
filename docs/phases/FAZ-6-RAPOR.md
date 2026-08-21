# Faz 6 — Sağlamlaştırma ve yayın

Tarih: 2026-08-21 · Durum: Tamamlandı (UPM temiz-proje doğrulaması kullanıcıda)
Dal: feat/phase-6-hardening-release · Commit sayısı: 8

## Ne yapıldı
- Hata yönetimi: sunucu kapalı ve aktarım ortasında bağlantı kopması için PlayMode testleri —
  bozuk veri ve iptal zaten Faz 4/5'te kanıtlıydı
- `~110 KB/bölge` çöp tahminini kalemlere ayıran testler: JSON parse, TKMS decode, disk-cache
  byte kopyası, mesh upload, URL kurma, `UnityWebRequest` nesne grafiği ayrı ayrı ölçüldü
- CI'a devre dışı (`if: false`) ama tam yazılmış bir `unity-tests` job'ı eklendi; secret istenmedi,
  manuel komut `CONTRIBUTING.md`'ye yazıldı
- README yeniden yazıldı: mimari şeması, hızlı başlangıç, doğrulanmış "Alternatifler" bölümü
  (Cesium for Unity v1.23, ArcGIS Maps SDK, Mapbox Unity SDK — her biri web'den kontrol edildi)
- `CHANGELOG.md` tek "Unreleased" bloğundan mevcut `v0.N.0` etiketleriyle eşleşen sürümlenmiş
  bölümlere ayrıldı (Faz 4 ve 5 hiç yazılmamıştı); `package.json` `0.1.0`'dan `0.6.0`'a çekildi

## Nasıl doğrulandı
| Kontrol | Komut | Sonuç |
|---|---|---|
| EditMode | `Unity -runTests -testPlatform EditMode` | **77 geçti** |
| PlayMode | `-testPlatform PlayMode` | **49 geçti** (41 Faz 5 + 2 hata yönetimi + 6 çöp kalemi) |

**Çöp kalemleri** (2.967 vertex / 29.702 bayt TKMS, gerçek bir bölge ölçeği):

| Kalem | Bayt/yineleme |
|---|---|
| TKMS decode | **0** (NativeArray tabanlı, tasarımın iddiası doğrulandı) |
| Mesh.Apply (`SetVertexBufferData`) | **0** (aynı) |
| URL kurma (`StringBuilder`+`EscapeDataString`) | **156** |
| `UnityWebRequest.Get` nesne grafiği | **143** |
| Disk cache için `byte[]` kopyası | **32.768** (yalnız `MeshDiskCache` açıkken, ağ yanıtı başına bir kez) |
| `/viewport` sayfası JSON parse (50 id) | **1.188** (sayfa başına, bölge başına değil) |

Ölçülen kalemler toplamı bir bölge için ~**33 KB** — Faz 5'in ~110 KB tahmininin üçte biri.
Kalan fark **ölçülmedi**: Task/async state machine'leri ve `UnityWebRequest`'in gerçek gönderim
yolu gerçek bir ağ round-trip'i gerektiriyor, bu da ölçümü bozan aynı tek-toplama sorununu geri
getiriyor (Faz 5'in kısıtı). Uydurmadık, ölçülmedi yazıyoruz.

## Draw call batch'leme seçenekleri (V2 öncesi karar, uygulanmadı)
- **Static/dynamic batching, GPU instancing:** kapalı — bölgeler havuzlanıp geri veriliyor
  (kalıcı değil), her bölge benzersiz mesh (instancing aynı mesh ister)
- **SRP Batcher:** yalnız CPU maliyetini düşürür, draw call sayısını değil; URP geçişi ister
- **Chunk'lanmış `CombineMeshes`** (önerilen ilk adım): viewport'taki bölgeleri N'li gruplar
  halinde tek Mesh'e birleştir → draw call `ceil(görünenBölge / N)`'e düşer. Bedel: per-region
  `MaterialPropertyBlock` rengi vertex color'a taşınmalı, chunk'lar her viewport değişiminde
  yeniden kurulmalı; CPU picking etkilenmez (veri ayrı tutuluyor)
- **Doku-atlas / bölge-id dokusu:** tek zemin mesh'i + fragment'ta bölge kimliği okuyan doku,
  picking doku örneklemesine kayar. 42.210 mahalle için asıl çözüm, mimari pivotu büyük — V2

## Kararlar ve gerekçeleri
1. **CI Unity job'ı `if: false`** — üçüncü taraf hesap bilgisi eklemek repo sahibinin kararı
2. **2022.3 elle doğrulanmadı** — ortamda kurulu değil, README'de niyet olarak işaretli

## Bilinen eksikler ve riskler
- `territorykit-simplification-finding.md`'deki "issue'lar henüz açılmadı" doğrulanmadı; UPM git
  URL ile temiz proje kurulumu **kullanıcı tarafından doğrulanacak**
- Çöpün kalan ~77 KB'lık kısmı ölçülmedi (Task/async, gerçek ağ round-trip'i)

## Tıkanmalar
Yok.

## Sonraki faza hazırlık
Yok — bu son faz. Onay sonrası: `main`'e `--no-ff` merge, `v0.6.0` tag, GitHub Release
(`CHANGELOG.md` `[Unreleased]`'den).

## Değişen dosyalar
- `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `.gitignore`, `.github/workflows/ci.yml`
- `packages/.../package.json`, `Tests/Common/MockGeometryServer.cs`,
  `Tests/Runtime/TerritoryClientPlayModeTests.cs`, `AllocationBreakdownTests.cs` (yeni)
