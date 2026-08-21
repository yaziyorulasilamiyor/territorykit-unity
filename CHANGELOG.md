# Changelog

Bu proje [Semantic Versioning](https://semver.org/) kullanır. Her `v0.N.0` etiketi bir fazın
sonunda `main`'e merge edilen halidir (`v0.N.0` = Faz N) — bkz. `docs/phases/FAZ-N-RAPOR.md`.

## [Unreleased]

Faz 6 — sağlamlaştırma ve yayın. `v0.6.0` olarak etiketlenecek.

### Added
- Hata yönetimi: sunucu kapalı (connection refused) ve aktarım sırasında bağlantı kopması için
  PlayMode testleri — bozuk veri ve iptal zaten Faz 4/5'te test edilmişti
- CI'da devre dışı ama hazır bir `unity-tests` job'ı (`game-ci/unity-test-runner`); etkinleştirmek
  için gereken tek şey Unity lisans secret'larının eklenmesi ve `if: false`'un kaldırılması

### Changed
- README: mimari şeması, uçtan uca hızlı başlangıç, doğrulanmış (kaynağı kontrol edilmiş)
  "Alternatifler" bölümü, güncel durum satırı

## [0.5.0] - 2026-08-19 (Faz 5 — havuzlama, viewport streaming, seçim)

### Added
- `TerritoryPool`: GameObject + Mesh için versiyonlu checkout/release, çift-release koruması
- `ViewportStreamer`: kamera bbox → `/viewport` → pool diff, transactional commit, 200'lük
  istek parçalama, generation tabanlı doğrulama
- `LodHysteresis`: iki eşikli coarsen/refine durum makinesi
- `TerritoryPicker`: bbox ön-eleme + CPU nokta-üçgen picking (`MeshCollider` yok)
- `MeshDiskCache`: revizyon+bölge+lod anahtarlı, SHA-256 kodlu, atomik yazma, kendini onaran
  bozuk kayıt tespiti
- `TerritoryClient.GetViewportAsync`/`GetMeshDataAsync`/`GetMeshDataBatchAsync`
- `Samples~/BasicMap`: pan/zoom/tıkla-vurgula; `scripts/measure_render.ps1` render ölçüm hattı

### Fixed
- `scripts/build_lod.py`'nin göreli `--output` ile kırılması (alt süreç `cwd` değiştiriyordu)

### Known limitations
- **GC hedefi streaming için karşılanmıyor**: duran kamera ve havuz sıfır tahsis, ama bölge
  yükleyen tik gerçek boyutlu mesh'lerde tik başına megabaytlar üretiyor (~110 KB/bölge kaba
  tahmin, kalem kalem ayrılmamıştı — Faz 6'da ayrıştırıldı)
- **Draw call = SetPass**, batch'leme yok: 81 bölgede 83 draw call, 973 bölgede ~975 olacak
- CPU picking belleği ilçe ölçeğinde önemsiz, mahalle ölçeğinde (42.210) tasarım kırılıyor

## [0.4.0] - 2026-08-18 (Faz 4 — Unity paketi, temel render)

### Added
- `TkmsHeader` + `MeshDecoder`: worker thread'de tam doğrulama, ana thread'de yalnız Mesh çağrıları
- `TerritoryClient`: metadata, sayfalı liste, tekil mesh, TKMB batch; `nativeData` ile kopyasız
  okuma, gerçek `Abort()` iptali, `LodPolicy`, `TerritoryMapPlacement`
- `TerritoryMapRenderer` + `Samples~/BasicMap` (81 il, tek batch); `capture_sample.ps1`,
  `check_lod_report.py` CI sentinel'i

### Fixed
- İptal yolunda mesh sızıntısı (mesh sahipliği artık `finally` altında geçiyor)
- `CancellationToken.Register` callback'inin yanlış thread'de koşması (token artık isteği
  başlatan thread'de yoklanıyor)

## [0.3.0] - 2026-08-18 (Faz 3 — HTTP API ve cache)

### Added
- `/v1/datasets`(+`{id}`), `.../territories`, `.../viewport` (cursor sayfalama, `?revision=`),
  `.../revisions/{revisionId}/mesh/{territoryId}` (GET/HEAD, önceden üretilmiş gzip, strong ETag,
  weak `If-None-Match`, `304`), `.../mesh/batch` (TKMB v1 konteyner). `/health`/`/ready`/`/metrics`
  kasıtlı olarak `/v1` dışında
- `scripts/publish_dataset.py`: staging→verify→atomik rename ile içerik-adresli revizyon yayını;
  `pruned-revisions.json` tombstone kaydıyla `404`/`410` ayrımı, lease dosyalarıyla
  aktif-istek-sırasında-budama koruması
- `geometry_api.manifest_validation`, `geometry_api.tkmb`, `geometry_api.cache`,
  `geometry_api.registry`

### Known limitation
- Manifest doğrulaması olayların kendi içinde tutarlı olduğunu kanıtlar, olayların gerçek
  geometriyi doğru sınıflandırdığını kanıtlamaz (bkz. `docs/phases/FAZ-3-PLAN.md` §1.3)

## [0.2.0] - 2026-08-17 (Faz 2 — LOD üretimi)

### Added
- Paylaşılan arc üzerinden topoloji-koruyan sadeleştirme zinciri (`scripts/build_lod.py`),
  build CLI'ında `--lod high|medium|low`, `scripts/check_lod_report.py` doğrulayıcısı
- Kayıp muhasebesi için tek türetme noktası (`geometry_api/loss.py`)
- Topoloji değişikliği muhasebesi: birleşme/bölünme sayıları manifestte `topologyChanges` altında
- `--max-total-lost-area`: kümülatif kayıp alan kapısı
- `scripts/repro_territorykit_finding.py`

### Changed
- `high` seviyesi 5e-05 toleransla sadeleştiriliyor: 81 il için 365.481 → 240.379 vertex, kayıp
  sıfır; Muğla uint16 index tavanı %92,3'ten %52,6'ya indi

### Notes
- Sadeleştirme TerritoryKit'in `--strategy topology-safe` komutuyla **yapılmıyor**: ring'leri
  bağımsız sadeleştiriyor, 197 komşu çiftinin `high`'da 32'sinde, `low`'da 161'inde çatlak
  bırakıyor (bkz. `docs/territorykit-simplification-finding.md`)
- Parça sayısı seviyeler arasında sabit değil (`low`: 705 → 685) — bilinçli istisna

## [0.1.0] - 2026-08-13 (Faz 1 — geometri motoru)

### Added
- Dataset yükleyici (GeoJSON + TerritoryKit `dataset.json`)
- WGS84 → yerel metre projeksiyonu (ileri/ters)
- Delik ve MultiPolygon destekli earcut üçgenlemesi
- TKMS v1 encoder/decoder
- `python -m geometry_api.build` toplu mesh CLI'ı

## [0.0.0] - 2026-08-12 (Faz 0 — iskelet, git akışı, sözleşme)

### Added
- Proje iskeleti, git akışı, `CONTRIBUTING.md`
- Geometri API sağlık uç noktası (`GET /health`)
- `docs/mesh-format.md`, `docs/projection.md`, `docs/api.md`
- `vendor/territorykit` submodule, `scripts/fetch_sample_dataset.py`, CI iskeleti
