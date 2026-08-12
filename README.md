# TerritoryKit.Unity

Hiyerarşik, düzensiz poligon "bölgeler" (ülke → il → ilçe → mahalle) için Unity render adaptörü.
[TerritoryKit](https://github.com/mberatkaya/TerritoryKit) açık kaynak geospatial SDK'sının
web-dışı (MapLibre/Leaflet/OpenLayers dışı) ilk oyun motoru entegrasyonudur.

> Durum: Erken geliştirme (Faz 0). Henüz kullanılabilir bir sürüm yok.

## Bileşenler

| Bileşen | Ne yapar |
|---|---|
| `services/geometry-api` | Bölge poligonlarını sunucu tarafında üçgenler, LOD üretir, binary mesh (TKMS) olarak servis eder |
| `packages/com.oguzhanonur.territorykit-unity` | Bu mesh'leri indirir, Unity `Mesh` nesnesine çevirir, havuzlar, viewport'a göre yükler |

Neden ağır iş sunucuda yapılıyor: bkz. [docs/mesh-format.md](docs/mesh-format.md) ve [docs/projection.md](docs/projection.md).

## Geliştirme

Git akışı, dal stratejisi ve commit kuralları için [CONTRIBUTING.md](CONTRIBUTING.md) dosyasına bakın.

## Lisans

MIT — bkz. [LICENSE](LICENSE).
