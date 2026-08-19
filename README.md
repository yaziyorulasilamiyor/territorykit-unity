# TerritoryKit.Unity

Hiyerarşik, düzensiz poligon "bölgeler" (ülke → il → ilçe → mahalle) için Unity render adaptörü.
[TerritoryKit](https://github.com/mberatkaya/TerritoryKit) açık kaynak geospatial SDK'sının
web-dışı (MapLibre/Leaflet/OpenLayers dışı) ilk oyun motoru entegrasyonudur.

> Durum: Erken geliştirme (Faz 5 tamamlandı — Unity paketi render, pooling, viewport streaming ve
> seçimle çalışıyor, `v0.5.0`). Henüz yayınlanmış bir sürüm yok; Faz 6 sağlamlaştırma ve ilk
> release'i hedefliyor.

![Örnek sahne — Türkiye il sınırları Unity'de](docs/phases/faz-4-ornek-sahne.png)

## LOD üretimi (Faz 2)

Ham geoBoundaries GeoJSON'undan üç detay seviyesinde TKMS mesh üretmek — tek komut:

```bash
python scripts/build_lod.py --input services/geometry-api/data/datasets/turkey-provinces.geojson --output data/lod --clean
```

Zincir: normalizasyon → `territory import geoboundaries` (TerritoryKit CLI) → `dataset.json` →
seviye başına sadeleştirme + üçgenleme → `high/`, `medium/`, `low/` dizinleri ve `lod-report.json`.

### Önkoşul: TerritoryKit CLI

Zincirin import adımı submodule'deki CLI'ı kullanır, o yüzden bir kez build edilmeli:

```bash
cd vendor/territorykit
corepack pnpm install
corepack pnpm --filter "@territory-kit/cli..." build
```

| Gereksinim | Sürüm | Not |
|---|---|---|
| Node.js | `>=22` (denenen: 24.18.0) | `vendor/territorykit/package.json` `engines` alanı |
| pnpm | 11.7.0 | `packageManager` ile sabitlenmiş; `corepack pnpm` ayrıca kurulum istemez |

İki bilinen takoz:

- `corepack enable` Windows'ta `C:\Program Files\nodejs` altına yazamayıp `EPERM` verebilir.
  Gerek yok — `corepack pnpm ...` doğrudan çalışır.
- pnpm, `@scarf/scarf` (telemetri) paketinin install script'ini bloklayıp `ERR_PNPM_IGNORED_BUILDS`
  ile çıkış kodu 1 döner. Script'i **onaylamayın**; `pnpm config set strict-dep-builds false`
  ile uyarıyı hataya çevirmeyi kapatın. Paket yine çalıştırılmaz.

> Sadeleştirme neden TerritoryKit'in `--strategy topology-safe` komutuyla yapılmıyor:
> [docs/territorykit-simplification-finding.md](docs/territorykit-simplification-finding.md).

## Mesh üretimi (tek seviye)

Örnek veriyi indirip tüm bölgeler için TKMS mesh üretmek:

Tek bir seviyeyi doğrudan üretmek (TerritoryKit CLI gerektirmez):

```bash
python scripts/fetch_sample_dataset.py
cd services/geometry-api
python -m geometry_api.build --input data/datasets/turkey-provinces.geojson --output data/meshes --lod high
```

Çıktı: bölge başına bir `.tkms` dosyası ve origin, bölge sayıları, bbox ve kaynak lisansını
taşıyan bir `index.json`. Geçersiz geometri varsayılan olarak reddedilir (`--repair-invalid`),
float32 ızgarasında kaybolan parça/delik `high` seviyesinde hata verir (`--allow-lossy`).

`--lod high|medium|low`; her seviye ayrı çıktı dizinine yazılır. 81 il ölçümü:

| Seviye | Vertex | high'ın %'si | Üçgen | Bayt | Parça | Delik |
|---|---|---|---|---|---|---|
| kaynak | 366.157 | — | — | — | 705 | 0 |
| high | 240.379 | %100 | 238.969 | 3.359.438 | 705 | 0 |
| medium | 85.926 | %35,7 | 84.518 | 1.197.108 | 704 | 0 |
| low | 30.753 | **%12,8** | 29.383 | 424.914 | 685 | 0 |

`high` her parçayı ve deliği koruyor (kayıp sıfır, build kapısı bunu zorluyor); `medium` ve `low`
küçük adaları bilerek düşürüyor ve düşen her parça `index.json`'a yazılıyor.

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
