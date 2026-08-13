# Bulgu: TerritoryKit `topology-safe` stratejisi topolojiyi korumuyor

Tarih: 2026-08-13 · Sürüm: `@territory-kit/cli` 1.4.0 · Submodule commit: `8ae8e6b`

Bu belge Faz 2'nin neden planlandığı gibi yürümediğini kayda geçirir. Faz talimatı
`territory geometry simplify --strategy topology-safe` çıktısını kullanmayı, kendi
sadeleştirmemizi yazmamayı şart koşuyordu. Strateji denendi ve ölçüldü; komşu bölgelerin
paylaştığı sınırları koruMUYOR. Aşağıdakiler tekrar üretilebilir ölçümlerdir.

## Kök neden

`packages/generators/src/geometry-simplification.ts` içinde `simplifyGeometry` → `simplifyRing`,
her zone'un her ring'ini **bağımsız olarak** Ramer–Douglas–Peucker'dan geçiriyor. Paylaşılan
kenarı (arc) çıkaran, iki komşunun aynı sınır için aynı sonucu almasını sağlayan bir mekanizma
yok. İki komşu ring aynı koordinat dizisini içerse bile RDP'nin özyinelemesi ring'in kendi
başlangıç noktasından ve global şeklinden etkilendiği için farklı vertex alt kümeleri seçiliyor.

`topologyAudit` alanı bunu **ölçüyor ama engellemiyor**: `sharedBoundaryMismatchCount` sıfırdan
büyükken bile komut `ok: true` ve çıkış kodu 0 veriyor. Paket dokümanı da bunu ima ediyor —
`docs/geometry-simplification.md`: *"A future GEOS/topojson backend can implement the same report
contract for stricter shared-arc simplification."*

## Ölçüm 1 — TerritoryKit'in kendi raporu

`simplification-report.json`, TR ADM1 (81 il, geoBoundaries gbOpen):

| Seviye | Vertex | Paylaşılan segment (önce → sonra) | `sharedBoundaryMismatchCount` |
|---|---|---|---|
| kaynak | 366.157 | 57.978 | — |
| high | 241.329 | 57.978 → 44.609 | **13.369** (%23) |
| medium | 88.023 | 57.978 → 25.564 | **32.414** (%56) |
| low | 38.981 | 57.978 → 9.774 | **48.204** (%83) |

## Ölçüm 2 — Bağımsız geometrik doğrulama

Sayaca güvenilmedi; shapely ile komşu çiftler arasındaki gerçek boşluk/çakışma alanı ölçüldü.
Faz 1'in tespit ettiği 197 gerçek ortak sınırlı il çifti kullanıldı.

| Seviye | Çatlaklı çift | Boşluk | Çakışma | En kötü tek çift |
|---|---|---|---|---|
| kaynak | 0/197 | 0 | 0 | 0 |
| high | 32/197 | 0,006 km² | 0,019 km² | 1.314 m² |
| medium | 96/197 | 1,73 km² | 1,55 km² | 0,12 km² |
| low | **163/197** | **63,06 km²** | **68,06 km²** | **2,03 km²** |

Bu ölçüm henüz üçgenleme ve float32 yuvarlamasından **önce**, ham poligon seviyesinde. 2 km²'lik
bir boşluk Unity'de gözle görülür.

## Ölçüm 3 — Aynı toleranslarla topojson

Aynı tolerans değerleriyle (0,00005 / 0,0005 / 0,0025) `topojson` 1.10:

| Seviye | Vertex (TK → topojson) | Çatlaklı çift (TK → topojson) |
|---|---|---|
| high | 241.329 → 241.084 | 32 → **0** |
| medium | 88.023 → 86.586 | 96 → **0** |
| low | 38.981 → 31.331 | 163 → **0** |

topojson hem daha az vertex üretiyor hem de çatlak bırakmıyor. Ek maliyet yok; fark algoritmanın
arc tabanlı olmasından geliyor.

## İkincil bulgu — `import geoboundaries` gerçek geoBoundaries dosyasını kabul etmiyor

geoBoundaries'in yayınladığı dosyalar olduğu gibi verildiğinde import 88 hatayla düşüyor:

- **81 ×** `SOURCE_COUNTRY_MISMATCH` — geoBoundaries `properties.shapeGroup` alanına ISO alpha-3
  (`TUR`) yazıyor, adaptör ise `normalizeTerritoryCountryCode` ile alpha-2 dayatıyor. Her feature
  patlıyor. Adaptörün kendi örnek komutu (`--country TR` + `geoBoundaries-TUR-ADM1.geojson`) bu
  hataya birebir düşüyor.
- **7 ×** `GEOMETRY_RING_ZERO_AREA` (`repairable: false`) — Muğla ve İstanbul'daki ~10–20 m²'lik
  gerçek adacıklar, 1e-9 deg² eşiğinin altında kaldığı için import'u tümden durduruyor.

## Tekrar üretme

```bash
cd vendor/territorykit && corepack pnpm install && corepack pnpm --filter "@territory-kit/cli..." build
node packages/cli/dist/index.mjs import geoboundaries --country TR --admin-level ADM1 \
  --input <geoBoundaries-TUR-ADM1.geojson> --output /tmp/tr-adm1 --force
node packages/cli/dist/index.mjs geometry simplify /tmp/tr-adm1/dataset.json \
  --strategy topology-safe --detail high,medium,low --output /tmp/simplified --force
```

Rapor dosyasındaki `topologyAudit.sharedBoundaryMismatchCount` alanına bakmak yeterli.

## Bu projedeki sonuç

Sadeleştirme adımı `topojson`'a devredildi (`services/geometry-api/src/geometry_api/simplify.py`).
TerritoryKit zincirden çıkmadı: dataset şeması ve `import geoboundaries` adımı hâlâ kullanılıyor.
Değişen yalnızca ölçülerek çalışmadığı gösterilen adım.

Bulgu upstream'e bildirilecek.
