# Changelog

Bu proje [Semantic Versioning](https://semver.org/) kullanır.

## [Unreleased]

### Added
- Proje iskeleti, git akışı, geometri API sağlık uç noktası (Faz 0)
- Geometri motoru (Faz 1): dataset yükleyici (GeoJSON + TerritoryKit `dataset.json`),
  WGS84 → yerel metre projeksiyonu (ileri/ters), delik ve MultiPolygon destekli earcut
  üçgenlemesi, TKMS v1 encoder/decoder ve `python -m geometry_api.build` toplu mesh CLI'ı
