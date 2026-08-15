# TerritoryKit bulguları — upstream'e açılacak iki issue

Sürüm: `@territory-kit/cli` 1.4.0 · Submodule commit: `8ae8e6b` · Ölçüm tarihi: 2026-08-15
Veri: geoBoundaries gbOpen TUR ADM1, 81 il

Bu belge iki **ayrı** hatayı anlatır. Ayrı tutulmalarının sebebi farklı bileşenleri ilgilendirmeleri:
biri sadeleştirme algoritması, diğeri onu denetlediğini iddia eden ölçüm.

> **Bu belgede bilerek kullanılmayan bir sayı var.** Önceki taslak "48.204 bozuk segment" diyordu.
> O sayı `sharedSegmentCount(kaynak) − sharedSegmentCount(çıktı)` farkıdır ve **bozulmanın kanıtı
> değildir** — gerekçe Issue B'de. Doğru çalışan bir sadeleştirici de aynı sayıyı üretiyor.

---

## Issue A — `topology-safe` stratejisi ring bazlı çalışıyor, topolojiyi bozuyor

### Kök neden

`packages/generators/src/geometry-simplification.ts` içinde `simplifyGeometry` → `simplifyRing`,
her zone'un her ring'ini **bağımsız** `ramerDouglasPeucker`'dan geçiriyor. Paylaşılan kenarı (arc)
çıkaran, iki komşunun aynı sınır için aynı sonucu almasını sağlayan bir model yok. İki komşu ring
aynı koordinat dizisini içerse bile RDP'nin özyinelemesi ring'in kendi başlangıç noktasından ve
global şeklinden etkilendiği için farklı vertex alt kümeleri seçiyor.

### Kanıt — `high` seviyesi

`high` kasten seçildi: çıktısının **81 geometrisinin hepsi geçerli**, hiçbir onarım (`buffer(0)`,
`make_valid`) gerekmiyor. Yani aşağıdaki sayılar ölçüm yönteminden değil, doğrudan
TerritoryKit'in çıktısından geliyor.

Faz 1'de tespit edilen 197 gerçek ortak sınırlı il çifti üzerinde, `--detail high` çıktısı:

| Ölçüm | Kaynak | `high` çıktısı |
|---|---|---|
| Geçersiz geometri | 0 | **0** |
| Boşluk olan çift | 0 | **15** / 197 |
| Çakışma olan çift | 0 | **30** / 197 |
| Etkilenen çift (boşluk ∪ çakışma) | 0 | **32** / 197 |
| Toplam boşluk | 0 | **0,0061 km²** |
| Toplam çakışma | 0 | **0,0189 km²** |
| En kötü tek çift | 0 | 1.314 m² |

Komşu poligonlar sadeleştirmeden önce sınırlarında **bit-eşit** vertex paylaşıyordu; sonra
paylaşmıyorlar. Çakışma da boşluk kadar önemli: iki il aynı alanı iddia ediyor.

### Destekleyici — `medium` ve `low`

⚠️ Bu iki seviyenin çıktısında **geçersiz geometri var** (kendini kesen ring'ler), o yüzden
ölçüm bir onarım adımı gerektiriyor ve sayılar onarımın etkisini taşıyor. Aşağıda her bölgenin
**kendi** iç halkaları (onarım artefaktı) düşülerek yalnızca komşular *arasındaki* boşluk verildi:

| Seviye | Geçersiz geometri | Etkilenen çift | Boşluk | Çakışma |
|---|---|---|---|---|
| medium | **20** | 95 / 197 | 1,39 km² | 1,55 km² |
| low | **23** | 161 / 197 | 57,86 km² | 68,06 km² |

Geçersiz geometri üretmek başlı başına bir sorun; sadeleştirme sonrası `isValid` kontrolü yok.

### Karşılaştırma

Aynı toleranslarla (0,00005 / 0,0005 / 0,0025) `topojson` 1.10 — arc tabanlı bir sadeleştirici:

| Seviye | Vertex (TK → topojson) | Etkilenen çift (TK → topojson) |
|---|---|---|
| high | 241.329 → 241.084 | 32 → **0** |
| medium | 88.023 → 86.586 | 95 → **0** |
| low | 38.981 → 31.331 | 161 → **0** |

topojson hem daha az vertex üretiyor hem hiç çatlak bırakmıyor. Fark maliyetten değil, algoritmanın
ortak arc modelinden geliyor.

### Tekrar üretme

```bash
cd vendor/territorykit && corepack pnpm install
corepack pnpm --filter "@territory-kit/cli..." build
node packages/cli/dist/index.mjs import geoboundaries --country TR --admin-level ADM1 \
  --input <geoBoundaries-TUR-ADM1.geojson> --output /tmp/tr-adm1 --force
node packages/cli/dist/index.mjs geometry simplify /tmp/tr-adm1/dataset.json \
  --strategy topology-safe --detail high --output /tmp/simplified --force
```

Sonra `/tmp/simplified/high/dataset.json` içinde sınır paylaşan iki il alıp kesişimlerinin
alanına bakmak yeterli — sıfır olmalı, değil. (Import'un çalışması için önce Issue A ekindeki
normalizasyon gerekiyor; bkz. aşağısı.)

---

## Issue B — `topologyAudit` gerçek bir topoloji denetimi yapmıyor

### Sorun

`sharedBoundaryMismatchCount` şöyle hesaplanıyor:

```ts
sharedBoundaryMismatchCount: Math.max(0, sourceSharedSegments - sharedSegmentCountAfter)
```

Bu, **paylaşılan segment sayısındaki düşüş**tür — uyumsuzluk değil. Sadeleştirme doğası gereği çok
sayıda kısa ortak segmenti daha az sayıda uzun ortak segmente dönüştürür, dolayısıyla bu sayı
kusursuz bir sadeleştiricide de yüksek çıkar.

### Kanıt

Aynı formül, çatlak ürettiği ölçülen TerritoryKit çıktısına ve **hiç çatlak üretmediği ölçülen**
topojson çıktısına uygulandığında:

| Seviye | TerritoryKit | topojson (0 çatlak) |
|---|---|---|
| high | 13.369 | 13.288 |
| medium | 32.414 | 32.048 |
| low | 48.204 | **47.357** |

Sayılar neredeyse aynı. Metrik doğru çıktıyla bozuk çıktıyı **ayırt etmiyor**, dolayısıyla
bir topoloji denetimi olarak kullanılamaz.

### İkinci sorun — sessiz başarı

`geometry simplify`, `sharedBoundaryMismatchCount: 48204` yazarken **ve** çıktısında 23 geçersiz
geometri varken `ok: true`, `issues: []` ve çıkış kodu **0** dönüyor. Otomatik bir boru hattı için
bu, bozuk çıktının sessizce geçmesi demek.

### Öneri

Gerçek denetim, sadeleştirme sonrası komşu çiftlerinin ortak sınırlarını karşılaştırmalı (ya da
en azından `isValid` kontrolü yapmalı) ve uyumsuzluk bulunduğunda sıfırdan farklı çıkış kodu
dönmeli.

---

## Ek — `import geoboundaries` gerçek geoBoundaries dosyalarını kabul etmiyor

Yukarıdaki tekrar üretme adımlarının çalışması için gereken, kendi başına da bir hata. geoBoundaries'in
yayınladığı dosya olduğu gibi verildiğinde import 88 hatayla düşüyor, çıktı üretilmiyor:

- **81 ×** `SOURCE_COUNTRY_MISMATCH` — geoBoundaries `properties.shapeGroup` alanına ISO alpha-3
  (`TUR`) yazıyor, adaptör `normalizeTerritoryCountryCode` ile alpha-2 dayatıyor. Her feature
  patlıyor. Adaptörün **kendi örnek komutu** (`--country TR` + `geoBoundaries-TUR-ADM1.geojson`)
  bu hataya birebir düşüyor.
- **7 ×** `GEOMETRY_RING_ZERO_AREA` (`repairable: false`) — Muğla ve İstanbul'daki gerçek adacıklar
  1e-9 deg² eşiğinin altında kaldığı için import'u tümden durduruyor. Yerel metre projeksiyonunda
  ölçülen alanları: **2,0 – 6,1 m²**.

---

## Bu projedeki sonuç

Sadeleştirme `topojson`'a devredildi (`services/geometry-api/src/geometry_api/simplify.py`).
TerritoryKit zincirden çıkmadı: dataset şeması ve `import geoboundaries` adımı hâlâ kullanılıyor.
Değişen yalnızca ölçülerek çalışmadığı gösterilen adım.

**Issue'lar henüz açılmadı** — onay bekliyor.
