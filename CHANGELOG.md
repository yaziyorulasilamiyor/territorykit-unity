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

### Changed
- `high` seviyesi artık 5e-05 toleransla sadeleştiriliyor: 81 il için 365.481 → 240.379 vertex.
  Her parça ve delik korunuyor (kayıp sıfır, build kapısı zorluyor). Muğla uint16 index
  tavanının %92,3'ünden %52,6'sına indi — Faz 1'in risk olarak işaretlediği pay sorunu kapandı

### Notes
- Sadeleştirme TerritoryKit'in `--strategy topology-safe` komutuyla **yapılmıyor**: strateji
  ring'leri bağımsız sadeleştiriyor ve 197 komşu çiftinin 163'ünde çatlak bırakıyor.
  Ölçümler ve tekrar üretme adımları: `docs/territorykit-simplification-finding.md`
