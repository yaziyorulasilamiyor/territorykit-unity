# TerritoryKit.Unity

Hiyerarşik, düzensiz poligon "bölgeler" (ülke → il → ilçe → mahalle) için Unity render adaptörü.
[TerritoryKit](https://github.com/mberatkaya/TerritoryKit) açık kaynak geospatial SDK'sının
web-dışı (MapLibre/Leaflet/OpenLayers dışı) ilk oyun motoru entegrasyonudur.

> Durum: Erken geliştirme (Faz 1 tamamlandı — geometri motoru). Henüz kullanılabilir bir sürüm yok.

## Mesh üretimi (Faz 1)

Örnek veriyi indirip tüm bölgeler için TKMS mesh üretmek:

```bash
python scripts/fetch_sample_dataset.py
cd services/geometry-api
python -m geometry_api.build --input data/datasets/turkey-provinces.geojson --output data/meshes
```

Çıktı: bölge başına bir `.tkms` dosyası ve origin, bölge sayıları, bbox ve kaynak lisansını
taşıyan bir `index.json`. Geçersiz geometri varsayılan olarak reddedilir (`--repair-invalid`),
float32 ızgarasında kaybolan parça/delik `high` seviyesinde hata verir (`--allow-lossy`).

## Bileşenler

| Bileşen | Ne yapar |
|---|---|
| `services/geometry-api` | Bölge poligonlarını sunucu tarafında üçgenler, LOD üretir, binary mesh (TKMS) olarak servis eder |
| `packages/com.oguzhanonur.territorykit-unity` | Bu mesh'leri indirir, Unity `Mesh` nesnesine çevirir, havuzlar, viewport'a göre yükler |

Neden ağır iş sunucuda yapılıyor: bkz. [docs/mesh-format.md](docs/mesh-format.md) ve [docs/projection.md](docs/projection.md).

## Geliştirme

Git akışı, dal stratejisi ve commit kuralları için [CONTRIBUTING.md](CONTRIBUTING.md) dosyasına bakın.

## Örnek veri seti atfı

`scripts/fetch_sample_dataset.py` ile indirilen örnek Türkiye il sınırları
[geoBoundaries](https://www.geoboundaries.org/) gbOpen TUR ADM1 veri setinden gelir:

> © OpenStreetMap contributors, via geoBoundaries (wmgeolab) — lisans: CC BY-SA 2.0

Bu veri seti sadece yerel geliştirme/test içindir, repoya commit edilmez (bkz. `.gitignore`).

## Lisans

MIT — bkz. [LICENSE](LICENSE). Örnek veri seti ayrı bir lisans altındadır (yukarı bakın).
