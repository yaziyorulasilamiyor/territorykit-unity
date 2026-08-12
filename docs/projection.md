# Koordinat dönüşümü

`float32` hassasiyeti ile enlem/boylam derecelerini doğrudan kullanmak kabul edilemez.
Türkiye enlemlerinde (`lat≈36-42°`) bir `float32` ULP (en küçük temsil edilebilir adım)
dereceler için ~0.4 m'ye, Web Mercator metrelerinde ise ~0.5 m'ye karşılık gelir — yani bu
tek başına "metrelerce hata" değil, sub-metre bir kuantalama sorunudur. Asıl risk, bu
kuantalamanın Unity'nin transform ve derinlik (depth) matematiğinde büyük mutlak
koordinatlarla (Mercator'da milyonlarca metre) birikmesi ve pratikte gözle görülür jitter'a
dönüşmesidir. Origin çıkarma, mutlak değerleri küçük tutarak bu birikimi engeller — bu yüzden
dönüşüm zorunludur.

## Akış

```
WGS84 (lon, lat) derece
   → Web Mercator (EPSG:3857) metre
   → origin çıkarılır  (dataset merkezinin Mercator karşılığı)
   → ölçek düzeltmesi: cos(originLatitude) ile çarp
   → Unity'ye float32 XY olarak gider
```

`origin`, mesh içinde değil **dataset seviyesinde** tanımlanır. `/datasets/{id}` endpoint'i
`originLon`, `originLat` ve `projection` alanlarını döner. Unity tarafı bu origin'i bir kez
alır; o dataset'e ait tüm mesh'ler aynı yerel koordinat uzayında yorumlanır.

## Formüller

Web Mercator (R = 6378137 m, WGS84 küresel yarıçap yaklaşımı):

```
x_merc = R * radians(lon)
y_merc = R * ln(tan(pi/4 + radians(lat)/2))
```

Origin çıkarma + ölçek düzeltmesi:

```
scale = cos(radians(originLat))
local_x = (x_merc - origin_x_merc) * scale
local_y = (y_merc - origin_y_merc) * scale
```

`cos(originLat)` çarpanı, Web Mercator'ün yüksek enlemlerde şiştirdiği mesafeleri origin
enlemine göre yerel olarak düzeltir — böylece origin çevresinde metre birimleri gerçek yer
mesafesine yakın kalır.

**Bu bir yaklaşıklıktır, dataset geneli için değil.** Ölçek sadece origin enleminde tam
doğrudur; origin'den enlemde uzaklaştıkça sapar. Sapma oranı `cos(originLat) / cos(lat)`'tır.

İki farklı sayıyı karıştırmamak gerekir:

| Ölçüm | Değer |
|---|---|
| Uçtan uca **yayılım** (`cos(36°)/cos(42°)`) | ~%9 |
| Örnek dataset'te origin'e göre **sapma** (origin `lat=38,956°`, bbox `35,808°–42,105°`) | güneyde **−%4,11**, kuzeyde **+%4,81** |

Yani 500 km'lik bir kuzey-güney mesafesi, dataset uçlarında gerçek yer mesafesinden ~%4-5
sapar. Şekiller bozulmaz — Mercator konformdur, sapma her iki eksende aynıdır — sadece ölçek
kayar. Bu, sınır çizimi ve göreli konum için kabul edilebilir; **gerçek mesafe/alan ölçümü için
kullanılamaz**.

Bu sayılar tahmin değil, ölçüm: `tests/test_projection.py::test_scale_error_across_dataset_bbox`
haversine ile hesaplanmış gerçek yer mesafesine karşı ölçüyor ve `0,03 < hata < 0,06` bandını
iddia ediyor. Band bilinçli olarak iki taraflı: biri eşit-alanlı bir projeksiyona geçerse alt
sınır kırılır ve karar bilinçli alınmak zorunda kalır.

## Sayısal örnek

Origin: Ankara, `lon=32.8597`, `lat=39.9334`

| Adım | X (m) | Y (m) |
|---|---|---|
| origin Mercator | 3,657,925.07 | 4,856,268.86 |
| İstanbul (`lon=28.9784, lat=41.0082`) Mercator | 3,225,860.73 | 5,013,551.24 |
| delta (Mercator) | -432,064.34 | 157,282.37 |
| `scale = cos(39.9334°)` | 0.766791 | — |
| **yerel metre (Unity'ye giden)** | **-331,303.09** | **120,602.72** |

## Ters dönüşüm

Yerel metreden WGS84'e dönmek için (örn. tıklama noktasından koordinat okuma):

```
x_merc = local_x / scale + origin_x_merc
y_merc = local_y / scale + origin_y_merc
lon = degrees(x_merc / R)
lat = degrees(2 * atan(exp(y_merc / R)) - pi/2)
```

## Origin nasıl seçilir

Origin **hesaplanır, yapılandırılmaz**: dataset'teki tüm bölgelerin birleşik bbox'ının
merkezidir. Aynı girdi her zaman aynı yerel uzayı verir — Faz 3'ün içerik-adresli cache'i buna
dayanır. Örnek dataset için `originLon=35,2416`, `originLat=38,9562`, `scale=0,777626`.

## Durum

Uygulandı — `services/geometry-api/src/geometry_api/projection.py`. Ölçülen değerler:

| Kontrol | Sonuç |
|---|---|
| İleri→ters gidiş-dönüş (81 il centroid'i + bbox köşesi, 405 nokta) | en kötü hata **< 1e-6 m** (sözleşme: < 1 m) |
| Ölçek sapması (bbox boyunca) | **−%4,11 … +%4,81**, origin enleminde < 1e-9 |
| Konformluk (D-B ve K-G eksenlerinde sapma farkı) | < 1e-4 |

Yukarıdaki sayısal örnek de testtir: `test_matches_the_worked_example_in_docs_projection_md`.
