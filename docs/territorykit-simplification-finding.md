# TerritoryKit bulguları — upstream'e açılacak iki issue

Sürüm: `@territory-kit/cli` 1.4.0 · Submodule commit: `8ae8e6b` · Ölçüm tarihi: 2026-08-16
Veri: geoBoundaries gbOpen TUR ADM1, 81 il

Bu belge iki **ayrı** hatayı anlatır. Ayrı tutulmalarının sebebi farklı bileşenleri ilgilendirmeleri:
biri sadeleştirme algoritması, diğeri onu denetlediğini iddia eden ölçüm.

Bu belgedeki **her sayı tek bir komutla üretilir**; bkz. [Tekrar üretme](#tekrar-üretme). Aşağıda
komut çıktısından kopyalanmayan bir sayı yoktur.

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

Kaynakta ortak sınırı olan 197 il çifti üzerinde, `--detail high` çıktısı:

| Ölçüm | Kaynak | `high` çıktısı |
|---|---|---|
| Geçersiz geometri | 0 | **0** |
| Etkilenen çift (boşluk ∪ çakışma) | 0 | **32** / 197 |
| Toplam boşluk | 0 | **0,0061 km²** |
| Toplam çakışma | 0 | **0,0187 km²** |
| En kötü tek çift — **Gaziantep / Kilis** | 0 | **6.596,9 m² çakışma** |

Gaziantep / Kilis çifti belgeye adıyla yazıldı çünkü 197 çiftin yalnız 32'si etkileniyor:
"iki komşu seç ve kesişimlerine bak" talimatı çiftlerin %84'ünde hiçbir şey göstermez. Tekrar
üretme betiği bu çifti varsayılan olarak ölçer, `--pair` ile başka bir çift verilebilir.

Komşu poligonlar sadeleştirmeden önce sınırlarında **bit-eşit** vertex paylaşıyordu; sonra
paylaşmıyorlar. Çakışma da boşluk kadar önemli: iki il aynı alanı iddia ediyor.

### Destekleyici — `medium` ve `low`

⚠️ Bu iki seviyenin çıktısında **geçersiz geometri var** (kendini kesen ring'ler), o yüzden
ölçüm bir onarım adımı gerektiriyor ve sayılar onarımın etkisini taşıyor. Aşağıda her bölgenin
**kendi** iç halkaları (onarım artefaktı) düşülerek yalnızca komşular *arasındaki* boşluk verildi:

| Seviye | Geçersiz geometri | Etkilenen çift | Boşluk | Çakışma |
|---|---|---|---|---|
| medium | **20** | 90 / 197 | 1,39 km² | 1,56 km² |
| low | **23** | 161 / 197 | 58,09 km² | 68,24 km² |

Geçersiz geometri üretmek başlı başına bir sorun; sadeleştirme sonrası `isValid` kontrolü yok.

### Karşılaştırma

Aynı toleranslarla (0,00005 / 0,0005 / 0,0025) `topojson` 1.10 — arc tabanlı bir sadeleştirici:

| Seviye | Etkilenen çift (TK → topojson) | Geçersiz geometri (TK → topojson) |
|---|---|---|
| high | 32 → **0** | 0 → 0 |
| medium | 90 → **0** | 20 → 5 |
| low | 161 → **0** | 23 → 13 |

**Dürüstlük notu:** topojson da `medium` ve `low`'da kendini kesen geometri üretiyor (5 ve 13).
Fark onarıma ihtiyaç duyup duymamak değil; bu boru hattı onarıyor ve **sonrasında** çatlağın
sıfır olduğunu üçgenlenmiş, float32'ye indirilmiş, decode edilmiş mesh üzerinde ölçüyor.
`geometry simplify` ise 23 geçersiz geometriyi `ok: true` ve çıkış kodu 0 ile teslim ediyor.
Sorun sadeleştiricinin kusursuz olmaması değil, kusurunu bildirmemesi.

### Tekrar üretme

```bash
cd vendor/territorykit && corepack pnpm install
corepack pnpm --filter "@territory-kit/cli..." build
cd ../..
pip install -e services/geometry-api
python scripts/fetch_sample_dataset.py
python scripts/repro_territorykit_finding.py \
  --input services/geometry-api/data/datasets/turkey-provinces.geojson \
  --work /tmp/tk-repro
```

Betik zinciri baştan sona kendisi çalıştırıyor: normalizasyon (aşağıdaki Ek olmadan import
çalışmıyor), `territory import geoboundaries`, `territory geometry simplify` **üç seviyede**,
aynı toleranslarla topojson, ve iki tarafın ölçümü. Tek seviye için `--detail high` /
`--detail low`, etkilenen çiftlerin listesi için `--list-affected`.

**Girdi kimliği.** Farklı sayı alan biri önce verinin aynı olup olmadığını görebilsin diye betik
iki hash basıyor. Bu belgedeki sayılar şu ikisiyle üretildi:

| Dosya | sha256 |
|---|---|
| Ham geoBoundaries TUR ADM1 | `d9d6fcb243e61684c2bd1dbe608b798732da1a4ce87a3635837a87c1930f1ba6` |
| Normalize edilmiş kopya | `6fe9052a3a4eb60f956f1b34dbbef8215578925be0e54a5a43cf6ed7c1686189` |

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

Aynı formül (kaynak: 57.978 ortak segment), çatlak ürettiği ölçülen TerritoryKit çıktısına ve
**hiç çatlak üretmediği ölçülen** topojson çıktısına uygulandığında:

| Seviye | TerritoryKit | topojson (0 çatlak) |
|---|---|---|
| high | 13.369 | 13.288 |
| medium | 32.414 | 32.049 |
| low | **48.204** | **47.358** |

Sayılar neredeyse aynı. Metrik doğru çıktıyla bozuk çıktıyı **ayırt etmiyor**, dolayısıyla
bir topoloji denetimi olarak kullanılamaz.

#### Hangi sayı hangi aşamaya ait

Bu tablodaki **her sayı poligon katmanına aittir**, mesh'e değil. `low` seviyesinde:

| Aşama | `sharedBoundaryMismatchCount` | Geçersiz geometri |
|---|---|---|
| Ham topojson sadeleştirici çıktısı | 47.357 | 13 |
| **`make_valid` sonrası poligon** (`_simplify_geometries`'in döndürdüğü şey) | **47.358** | 0 |
| TerritoryKit `geometry simplify` | 48.204 (kendi raporu) / 48.200 (yazdığı JSON'dan yeniden hesap) | 23 |

İki düzeltme, önceki taslakta yanlış olan iki nokta:

1. **47.358 "nihai boru hattı çıktısı" DEĞİL.** `make_valid` uygulanmış **poligon** katmanıdır.
   Bu belge ve tekrar üretme betiği poligon üzerinde ölçüm yapar; üçgenleme ve float32 sonrası
   mesh'e hiç dokunmaz.
2. **Mesh'te sıfır çatlak iddiası bu sayıyla kanıtlanmıyor.** O iddia ayrı bir ölçümdür:
   `services/geometry-api/tests/test_lod.py`, üç seviyenin her birinde TKMS'e encode edip
   **decode ettikten sonra** 81 ilin birleşimindeki iç halka alanını ve çift bazlı çakışmayı
   ölçer; ikisi de tam 0,0. Tekrar üretme betiği o adımı çalıştırmaz ve çalıştırdığını iddia
   etmez — çıktısında bunu söyleyen bir not basar.

Önemli olan **her üç poligon değerinin de** birbirine yakın olması: metrik, çatlak ürettiği
ölçülen çıktıyı (32/197 çift, `high`) hiç üretmeyeninden (0/197) ayırt edemiyor.

Küçük bir ek fark: TerritoryKit'in kendi raporu `low` için **48.204** yazıyor, aynı formül yazdığı
`dataset.json` üzerinden yeniden hesaplandığında **48.200** çıkıyor. Denetim bellekteki geometri
üzerinde koşuyor, yeniden hesap ise JSON'a daha az hassasiyetle serileştirilmiş koordinatlar
üzerinde; 4'lük fark buradan geliyor ve bulguyla ilgisi yok (iki değer de çatlaksız bir
sadeleştiricinin ~47,4k'sının çok üstünde). Betik **ikisini de** basıyor ki bu fark "tekrar
üretilemedi" gibi okunmasın.

### İkinci sorun — sessiz başarı

`geometry simplify`, `sharedBoundaryMismatchCount: 48204` yazarken **ve** çıktısında 23 geçersiz
geometri varken `ok: true`, `issues: []` ve çıkış kodu **0** dönüyor. Otomatik bir boru hattı için
bu, bozuk çıktının sessizce geçmesi demek.

Bu, Issue B'nin **ana kanıtı**, o yüzden betik CLI'ın stdout'unu artık özetlemiyor; üç değeri de
olduğu gibi basıyor. `low` için gerçek çıktı:

```
  territorykit invalid=23  affected=161/197
               gap=58.0872 km²  overlap=68.2447 km²  sharedBoundaryMismatchCount=48200
  CLI verdict  exit code=0  ok=true  issues=[]
```

(Önceki taslakta betik bu JSON'u okuyup atıyordu, dolayısıyla iddianın arkasında basılı bir
kanıt yoktu.)

### Öneri

Gerçek denetim, sadeleştirme sonrası komşu çiftlerinin ortak sınırlarını karşılaştırmalı (ya da
en azından `isValid` kontrolü yapmalı) ve uyumsuzluk bulunduğunda sıfırdan farklı çıkış kodu
dönmeli.

### Tekrar üretme

Issue A ile aynı komut; `sharedBoundaryMismatchCount` satırları hem TerritoryKit hem topojson
için, üç seviyede de basılıyor. Formülün Python karşılığı betikte
`_shared_segment_count` içinde, `geometry-simplification.ts`'teki `collectSharedSegments` ile
koordinat koordinat aynı (9 ondalık, yön bağımsız anahtar).

CLI'ın kendi cevabı (`exit code`, `ok`, `issues`) her seviye için `CLI verdict` satırında
basılıyor. Mesh üzerindeki sıfır çatlak ölçümü bu betikte **değil**:

```bash
cd services/geometry-api
pytest tests/test_lod.py -k cracks -q
```

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

Normalizasyon `scripts/build_lod.py:normalize_geoboundaries` içinde; tekrar üretme betiği aynı
fonksiyonu çağırıyor, kaynak dosyayı değiştirmiyor ve düşürdüğü her parçayı sayıp yazdırıyor.

---

## Bu projedeki sonuç

Sadeleştirme `topojson`'a devredildi (`services/geometry-api/src/geometry_api/simplify.py`).
TerritoryKit zincirden çıkmadı: dataset şeması ve `import geoboundaries` adımı hâlâ kullanılıyor.
Değişen yalnızca ölçülerek çalışmadığı gösterilen adım.

**Issue'lar henüz açılmadı** — onay bekliyor. Tekrar üretme hazır ve çalışıyor; her iki issue'nun
her sayısı tek komuttan çıkıyor.

| Issue | Durum | 4. tur değişikliği |
|---|---|---|
| A — `topology-safe` ring bazlı çalışıyor | Gönderilebilir | Yok, dokunulmadı |
| B — `topologyAudit` denetim yapmıyor | Gönderilebilir | 47.358'in **`make_valid` sonrası poligon** olduğu düzeltildi; mesh/float32 iddiası ayrı ölçüm olarak ayrıştırıldı; betik CLI'ın `exit code` / `ok` / `issues` cevabını artık basıyor; 48.204 ↔ 48.200 farkı açıklandı |
