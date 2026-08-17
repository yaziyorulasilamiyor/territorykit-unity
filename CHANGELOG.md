# Changelog

Bu proje [Semantic Versioning](https://semver.org/) kullanır.

## [Unreleased]

### Added
- Proje iskeleti, git akışı, geometri API sağlık uç noktası (Faz 0)
- Geometri motoru (Faz 1): dataset yükleyici (GeoJSON + TerritoryKit `dataset.json`),
  WGS84 → yerel metre projeksiyonu (ileri/ters), delik ve MultiPolygon destekli earcut
  üçgenlemesi, TKMS v1 encoder/decoder ve `python -m geometry_api.build` toplu mesh CLI'ı
- LOD üretimi (Faz 2): paylaşılan arc üzerinden topoloji-koruyan sadeleştirme (`simplify.py`),
  build CLI'ında `--lod high|medium|low`, uçtan uca `scripts/build_lod.py` zinciri ve
  `scripts/check_lod_report.py` doğrulayıcısı. Üç seviyede de komşular arası boşluk ve
  çakışma **tam sıfır** (üçgenleme ve float32 sonrası ölçüldü)
- Kayıp muhasebesi için tek türetme noktası (`geometry_api/loss.py`): `lossy` bayrağı yalnız
  kayıtlardan hesaplanır, hiçbir boolean alan okunmaz
- Topoloji değişikliği muhasebesi: birleşme/bölünme sayıları manifestte `topologyChanges`
  altında, bölge bölge. Kayıp *değil*, ama parça sayısını değiştirdiği için raporlanıyor
- `--max-total-lost-area`: kümülatif kayıp alan kapısı (tek parça kapısına ek)
- `scripts/repro_territorykit_finding.py`: iki upstream bulgusunu tek komutla tekrar üretir
- HTTP API (Faz 3): `/v1/datasets`(+`{id}`), `.../territories`, `.../viewport` (cursor
  sayfalama, `?revision=` sabitleme), `.../revisions/{revisionId}/mesh/{territoryId}`
  (GET/HEAD, önceden üretilmiş gzip, strong ETag, weak `If-None-Match`, `304`),
  `.../mesh/batch` (TKMB v1 konteyner). `/health`/`/ready`/`/metrics` kasıtlı olarak `/v1`
  dışında
- `scripts/publish_dataset.py`: build çıktısını `manifest_validation.check()` +
  `check_report_matches_build()` ile doğrulayıp staging→verify→atomik rename ile
  içerik-adresli bir revizyona (tam SHA-256, 64 hex) yayınlar; `pruned-revisions.json`
  tombstone kaydıyla `404`/`410` ayrımı, lease dosyalarıyla aktif-istek-sırasında-budama
  koruması
- `geometry_api.manifest_validation`: `check_lod_report.py`'nin denetleyicisi artık paylaşılan
  bir modül — CI script'i ve publisher aynı fail-closed kontrolü çalıştırır
- `geometry_api.tkmb`: TKMB v1 encoder/decoder — TOC her zaman id-sıralı (istek sırası yok
  sayılır), eksik id'ler konteynerin kendi içinde
- `geometry_api.cache`: revizyona göre dizinlenmiş, içerik-adresli batch cache (LRU tahliye)
- `geometry_api.registry`: revizyon çözümleme, yayın-sonrası bütünlük denetimi (§3.5a),
  bellek-içi lease

### Known limitation
- Manifest doğrulaması, kayıt edilen olayların **kendi içinde tutarlı** olduğunu kanıtlar,
  olayların gerçek geometriyi **doğru sınıflandırdığını** kanıtlamaz — Faz 2'den devralınan,
  bilinçli olarak kapatılmamış bir açık (bkz. `docs/phases/FAZ-3-PLAN.md` §1.3)

### Changed
- `high` seviyesi artık 5e-05 toleransla sadeleştiriliyor: 81 il için 365.481 → 240.379 vertex.
  Her parça ve delik korunuyor (kayıp sıfır, build kapısı zorluyor). Muğla uint16 index
  tavanının %92,3'ünden %52,6'sına indi — Faz 1'in risk olarak işaretlediği pay sorunu kapandı

### Notes
- Sadeleştirme TerritoryKit'in `--strategy topology-safe` komutuyla **yapılmıyor**: strateji
  ring'leri bağımsız sadeleştiriyor; 197 komşu çiftinin `high`'da 32'sinde, `low`'da 161'inde
  çatlak bırakıyor. Ölçümler ve tekrar üretme: `docs/territorykit-simplification-finding.md`
- **Parça sayısı seviyeler arasında sabit değil** (`low`: 705 → 685). Bilinçli bir istisna;
  gerekçesi ve bedeli `docs/PROJE-TALIMATI.md` FAZ 2 maddesinde yazılı
