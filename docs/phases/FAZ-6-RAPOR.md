# Faz 6 — Sağlamlaştırma ve yayın

Tarih: 2026-08-21 · Durum: Tamamlandı · Dal: feat/phase-6-hardening-release · Commit: 9

## Temiz proje doğrulaması gerçek bir hata buldu
Kullanıcı boş bir Unity 6 projesine paketi UPM git URL'iyle kurup `BasicMap`'i denedi: Unity 6
varsayılanı "Input System Package (New)", örnek eski `UnityEngine.Input` kullanıyordu — Play'e
basar basmaz `InvalidOperationException`. Dev proje "Input Manager (Old)" seçili olduğu için hiç
görünmemişti; klasik "benim makinemde çalışıyor" hatası. Düzeltme: `BasicMapCameraController`
artık `ENABLE_LEGACY_INPUT_MANAGER`/`ENABLE_INPUT_SYSTEM` koşullu derlemesiyle Old/New/Both'ta
çalışacak şekilde ayrıldı, bağımlılık eklemeden; ikisi de yoksa tek uyarı loglayıp kontrolü
kapatıyor, harita yine çiziliyor. Dev proje artık **Both** modunda. Old-only mevcut davranıştı;
New-only dalı temiz derlendi ama test suit'i çalıştırılmadı; tam 77 EditMode + 49 PlayMode koşusu
yalnız Both altında geçti. New-only pan/zoom/tıklama davranışını kullanıcı Unity'de doğrulayacak.

## Ne yapıldı
- Hata yönetimi: sunucu kapalı/bağlantı kopması testleri (bozuk veri+iptal Faz 4/5'te kanıtlıydı);
  Input backend hatası aşağıda ayrı bölümde
- `~110 KB/bölge` çöp tahminini kalemlere ayıran testler: JSON parse, TKMS decode, disk-cache
  kopyası, mesh upload, URL kurma, `UnityWebRequest` nesne grafiği ayrı ayrı ölçüldü
- CI'a devre dışı (`if: false`) tam yazılmış `unity-tests` job'ı; manuel komut `CONTRIBUTING.md`'de
- README yeniden yazıldı: mimari şeması, hızlı başlangıç, doğrulanmış "Alternatifler" bölümü
- `CHANGELOG.md` sürümlenmiş bölümlere ayrıldı (Faz 4/5 hiç yazılmamıştı); `package.json`
  `0.1.0`→`0.6.0`

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

Toplam ~**33 KB** — Faz 5'in ~110 KB tahmininin üçte biri. Kalan fark **ölçülmedi**: Task/async
ve `UnityWebRequest`'in gönderim yolu gerçek ağ round-trip'i gerektiriyor, bu da ölçümü bozan
aynı tek-toplama sorununu geri getiriyor.

## Draw call batch'leme seçenekleri (V2 öncesi karar, uygulanmadı)
- **Static/dynamic batching, GPU instancing:** kapalı — bölgeler havuzlanıyor (kalıcı değil),
  her biri benzersiz mesh. **SRP Batcher:** yalnız CPU maliyetini düşürür, sayıyı değil
- **Chunk'lanmış `CombineMeshes`** (önerilen ilk adım): bölgeleri N'li gruplarda tek Mesh'e
  birleştir → draw call `ceil(görünenBölge/N)`'e düşer. Bedel: per-region renk vertex color'a
  taşınmalı, chunk'lar viewport değişiminde yeniden kurulmalı; CPU picking etkilenmez
- **Doku-atlas / bölge-id dokusu:** 42.210 mahalle için asıl çözüm, mimari pivotu büyük — V2

## Kararlar ve gerekçeleri
1. **CI Unity job'ı `if: false`** — üçüncü taraf hesap bilgisi eklemek repo sahibinin kararı
2. **2022.3 elle doğrulanmadı** — ortamda kurulu değil, README'de niyet olarak işaretli

## Bilinen eksikler, riskler, tıkanmalar
- `territorykit-simplification-finding.md`'deki "issue'lar henüz açılmadı" doğrulanmadı
- Çöpün kalan ~77 KB'lık kısmı ölçülmedi (Task/async, gerçek ağ round-trip'i)
- Tıkanma yok

## Sonraki faza hazırlık
Yok — bu son faz. Onay sonrası: `main`'e `--no-ff` merge, `v0.6.0` tag, GitHub Release
(`CHANGELOG.md` `[Unreleased]`'den).

## Değişen dosyalar
- `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `.gitignore`, `.github/workflows/ci.yml`
- `packages/.../package.json`, `Samples~/BasicMap/*`, `Tests/Common/MockGeometryServer.cs`
- `Tests/Runtime/*PlayModeTests.cs`, `AllocationBreakdownTests.cs` (yeni), dev proje manifest'i
