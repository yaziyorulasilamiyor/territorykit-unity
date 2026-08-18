# TKMS — TerritoryKit Mesh Stream v1

Bu spesifikasyon sabittir. Değişiklik gerekiyorsa yeni bir versiyon numarası (v2) tanımlanır,
mevcut v1 okuyucusu bozulmaz.

## Header — 32 byte, little-endian

| offset | tip | alan |
|---|---|---|
| 0 | `char[4]` | magic = `"TKMS"` |
| 4 | `uint16` | version = 1 |
| 6 | `uint16` | flags (bit0: 1 ise index'ler `uint32`, 0 ise `uint16`) |
| 8 | `uint32` | vertexCount |
| 12 | `uint32` | indexCount |
| 16 | `float32` | minX (yerel metre) |
| 20 | `float32` | minY |
| 24 | `float32` | maxX |
| 28 | `float32` | maxY |

## Body — little-endian

Header ile aynı byte sırası (little-endian) gövdede de geçerlidir; tüm `float32`/`uint16`/
`uint32` alanları little-endian kodlanır.

```
float32[vertexCount * 2]                 vertices — yerel metre, XY sırası (little-endian)
uint16[indexCount] | uint32[indexCount]  indices  — üçgen listesi (little-endian)
```

### Beklenen toplam payload uzunluğu

```
indexElementSize = 4 if (flags & 0b1) else 2
payloadLength = 32                                    # header
              + vertexCount * 2 * 4                    # float32 XY
              + indexCount * indexElementSize
```

Okuyucu, aldığı byte dizisinin uzunluğunu bu formülle karşılaştırmalıdır. **Trailing byte
(fazladan sondaki byte) davranışı:** dizide bu formülün ötesinde ekstra byte varsa okuyucu
onu yok sayar (ör. hizalama dolgusu); ancak dizide bu formülden **az** byte varsa geçersiz
mesh'tir ve reddedilmelidir.

Bu hoşgörü **okuyucu içindir, yazıcı için değil.** `decode_tkms`, kullandığı byte sayısını
`bytes_consumed` alanıyla döndürür; böylece çağıran dolguyu yükten ayırt edebilir. Bu projenin
kendi çıktısında dolgu **yoktur** ve bu bir testle sabitlenmiştir: build CLI'ın ürettiği 81
dosyanın her birinde `bytes_consumed == len(dosya)`
(`tests/test_build.py::test_written_meshes_carry_no_trailing_bytes`).

## Kurallar

- `vertexCount > 65535` ise `flags` bit0 = 1 olmak **zorunda** (Unity `IndexFormat.UInt32` limiti).
- `indexCount % 3 == 0` olmak zorunda.
- Her index değeri `vertexCount`'tan **küçük** olmak zorunda (`index < vertexCount`); aksi
  halde mesh geçersizdir ve reddedilmelidir.
- Üçgen sarım yönü (winding): **saat yönü (clockwise)** — Unity'de ön yüz budur.
- Boş geometri geçersizdir; en az 1 üçgen (3 index) olmalıdır.
- Koordinatlar Bölüm/`docs/projection.md`'deki dönüşümden geçmiş, dataset origin'i çıkarılmış
  yerel metre değerleridir — mutlak WGS84 derece değil.
- Vertex koordinatlarında `NaN` veya `Infinity` **yasaktır**; böyle bir değer içeren mesh
  geçersizdir ve encoder tarafından üretilmemeli, decoder tarafından reddedilmelidir.
- **Bilinmeyen flag bitleri** (bit0 dışındaki tüm bitler, v1'de tanımsız): okuyucu bunları
  **yok sayar** (ignore), reddetmez. Bu, gelecekteki v1-uyumlu uzantılar için esneklik
  bırakır — yeni davranış gerektiren değişiklikler `version` alanını artırmalıdır.

## Bounding box kuralı

Header'daki `minX/minY/maxX/maxY`, gövdedeki vertex'lerin **gerçek** min/max değeri olmak
zorundadır — daha geniş bir "güvenli" kutu da geçersizdir. Gerekçe: bu kutu Faz 3 ve Faz 5'te
viewport culling için kullanılacak; yanlış bir kutu bölgenin ekranda **bozuk görünmesine** değil,
**hiç görünmemesine** yol açar. `NaN`/`Infinity` yasak, `min <= max` zorunlu. Referans decoder
bunu her okumada doğrular.

## Uygulama ve doğrulama

Referans uygulama: `services/geometry-api/src/geometry_api/encoding.py`
(`encode_tkms` / `decode_tkms`). Yukarıdaki her kural en az bir yönde teste bağlıdır —
`services/geometry-api/tests/test_encoding.py`.

**Decoder ne kadar katı?** `decode_tkms(payload)` bir payload'ı *okunamaz veya güvenilmez*
yapan her şeyi reddeder: magic, version, beyan edilen uzunluk, index hizalaması ve aralığı,
`NaN`/`Infinity` koordinat, uint32 bayrağı tutarlılığı ve yukarıdaki bbox kuralı.
`decode_tkms(payload, strict=True)` ayrıca mesh'in **doğru render edilmesini** sağlayan iki
kuralı da kontrol eder: saat yönü sarım ve sıfır alanlı üçgen yokluğu. Bunlar encoder'ın
sözleşmesidir; varsayılan okuyucu her mesh'te bu maliyeti ödemez, build hattı ve testler öder.
Yani **varsayılan decoder "dokümanın geçersiz dediği her şeyi" reddetmez** — sarım ve dejenerasyon
yalnızca `strict=True` ile denetlenir.

Encoder tarafındaki iki karar:

- **Sarım yönü üçgen başına hesaplanır.** İşaretli alanı pozitif (CCW) çıkan her üçgende iki
  index takas edilir. Kör çevirme yapılmaz; earcut'ın çıktısı bugün tekdüze CCW olsa da, bu
  değiştiği gün kör çevirme tüm yüzleri sessizce ters çevirirdi.
- **`flags` bit0 çağıran tarafından verilmez**, `vertexCount > 65535` koşulundan türetilir —
  unutulması mümkün değil.

Sarım garantisi mesh'in **kendi XY uzayı** içindir. Unity'nin `(x, y)`'yi hangi eksenlere
yerleştirdiği ön yüzün ekranda doğru görünüp görünmediğini belirler; bu Faz 4'ün konusudur ve
sözleşme burada sabitlendiği için orada değişecek olan yerleştirme, formattır değil.

Ayrıca vertex'ler üçgenlemeden **önce** float32 ızgarasına yuvarlanır (bkz. `triangulate.py`):
float64'te üçgenleyip sonradan cast etmek, cast sırasında sıfır alana çöken — ve dolayısıyla
yönlendirilemeyen — üçgenler üretiyordu (ölçüm: 364.057 üçgenin 62'si, 16 ilde).

## Manifest bayrakları — `lossy`, `topologyChanged`, `pickingUnsafe`

Build CLI'ı her seviye dizinine bir `index.json` yazar. Üst düzeyindeki üç boolean, bir
istemcinin (Faz 4-5'te Unity) sayıları yeniden yorumlamadan okuyabilmesi içindir. Üçü de
**türetilir**, elle yazılmaz: kaynak, `loss` bloğundaki tipli olay kayıtlarıdır
(`services/geometry-api/src/geometry_api/loss.py`).

| Bayrak | `false` ne demek |
|---|---|
| `lossy` | Kaynakta olan hiçbir geometri çıktıda eksik değil |
| `topologyChanged` | Parça ve delik (enclave) sayısı/yapısı kaynakla aynı |
| `pickingUnsafe` | **Bu seviyenin geometrisi kaynakla topolojik olarak aynıdır; bölge seçimi (picking) güvenilirdir** |

`pickingUnsafe: false`, **nihai mesh** hakkında bir iddiadır — yalnız sadeleştirme adımı
hakkında değil. Kaynak poligonlarla o mesh arasındaki zincirin **hiçbir adımı** (geoBoundaries
normalizasyonu, sadeleştirme, üçgenleme) geometri kaybetmemiş ve parça/delik yapısını
değiştirmemiş demektir; dolayısıyla bir tıklama, kaynağın o noktanın sahibi dediği bölgeye
çözülür.

**Zorunlu ilişki:** `lossy: true` iken `pickingUnsafe: false` **olamaz.** Kayıp kategorisindeki
her olay türü aynı zamanda picking'i güvensiz işaretler; ayrıca `scripts/check_lod_report.py`
bu tutarlılığı manifest üzerinde bağımsız olarak denetler ve tutmazsa CI düşer.

Bayrakları **tetikleyen** olaylar: kaybolan parça/delik/adacık (hangi adımda olursa olsun),
üçgenlemenin atladığı parça veya halka, dejenere üçgen, parça birleşmesi/bölünmesi/oluşması,
delik birleşmesi/bölünmesi.

Bayrakları **tetiklemeyen** olaylar ve nedenleri:

- `boundary_retreat` / `boundary_advance` — sınırın tolerans kadar kayması. Her seviye (`high`
  dahil) bunu yapar; sadeleştirmenin tanımı budur. Her yerde `true` olan bir bayrak istemciye
  bir şey söylemez. Sınırın **ne kadar** kaydığı ayrı ve sayısal olarak raporlanır:
  `simplification.areaBudget.retainedAreaRatio` ve `minPartRetainedAreaRatio`.
- `severe_shrink` — alanının yarısından azını koruyan parça. Yapısal değil ölçek kaybıdır,
  yukarıdaki oranlarla raporlanır.
- `artifact_hole_removed` — sadeleştirmenin uydurduğu, bu boru hattının geri kapattığı delik.
  Çıktıyı kaynağa **yaklaştıran** tek yapısal olaydır; sonuçta kaynağın kapsadığı zemin kapsanır.

Seviyeye özel olan `simplification.topologyChanged`, yalnız sadeleştirme adımını anlatır; üst
düzeydeki bayraklar zincirin tamamını anlatır. İkisi kasıtlı olarak farklı sorulara cevap verir.

## Bilinen sınır: antimeridyen

Mesh koordinatları dataset origin'ine göredir ve origin bbox merkezinden hesaplanır; bu yüzden
±180° boylamını kesen dataset'ler desteklenmez (bkz. [projection.md](projection.md) — parçalar
milyonlarca metre uzağa düşer). Bölgesel dataset'ler için bilinçli bir sınırdır.

## TKMB v1 — Mesh Batch konteyneri

`POST /v1/datasets/{id}/revisions/{revisionId}/mesh/batch` birden çok TKMS mesh'ini tek yanıtta
döner. Referans uygulama: `services/geometry-api/src/geometry_api/tkmb.py`
(`encode_tkmb`/`decode_tkmb`).

### Header — 16 byte, little-endian

| offset | tip | alan |
|---|---|---|
| 0 | `char[4]` | magic = `"TKMB"` |
| 4 | `uint16` | version = 1 |
| 6 | `uint16` | flags (bit0: 1 ise girdiler gzip'li TKMS, 0 ise ham TKMS) |
| 8 | `uint32` | foundCount — TOC'taki kayıt sayısı |
| 12 | `uint32` | missingCount — bulunamayan kayıt sayısı |

### Gövde

```
TOC          — foundCount kayıt, territoryId'ye göre sözlüksel ARTAN sırada:
               uint16 idLength, char[idLength] territoryId (UTF-8), uint32 offset, uint32 length
Missing      — missingCount kayıt, territoryId'ye göre sözlüksel ARTAN sırada:
               uint16 idLength, char[idLength] territoryId
Payload      — TOC sırasıyla (yani id'ye göre artan), her territory'nin tam TKMS baytları
               (flags bit0=1 ise gzip'lenmiş)
```

**`offset` payload alt-bölümünün başlangıcına göredir**, dosyanın mutlak başlangıcına göre değil —
yani `header + TOC + missing` bölümünden sonraki ilk bayt `offset = 0` kabul edilir. `offset` ve
`length` `uint32`'dir (`< 2^32`); bir batch'in toplam payload boyutu bunu aşacaksa istek
`400 batch_too_large` ile reddedilir, sessizce sarmalanmaz veya kesilmez.

**TOC sırası her zaman id-artan, istek sırası değil.** İstemcinin `territoryIds` alanına hangi
sırayla yazdığından bağımsız olarak TOC (ve payload'ın kendi sırası) her zaman territoryId'ye göre
artan sıradadır — `["34","06"]` ve `["06","34"]` istekleri **byte-birebir aynı** TKMB üretir. Bu,
sunucunun içerik-adresli batch cache anahtarının da isteği sırasız (`sorted(set(...))`) ele
almasıyla tutarlıdır (`services/geometry-api/src/geometry_api/cache.py`).

**Bulunamayan id'ler konteynerin kendi içindedir, bir HTTP header'ında değil.** İstenip
bulunamayan territory'ler `Missing` bölümünde, ayrıştırılabilir biçimde taşınır — bir ara
vekil/proxy bir header'ı düşürse bile bilgi kaybolmaz. Tamamı eksik olsa bile yanıt `200` döner
(`foundCount: 0`); batch URL'si her zaman geçerlidir, döndürdüğü içerik boş olabilir.

**Yinelenen id.** `territoryIds` içinde aynı id birden çok geçerse sunucu tekilleştirir; TOC'ta tek
kayıt olarak yer alır, hata değildir.

**`entryEncoding`, HTTP `Accept-Encoding` değildir.** Girdilerin gzip'li olup olmadığı (`flags`
bit0) `Accept-Encoding` header'ından değil, istek gövdesindeki açık `entryEncoding` alanından
(`"identity" | "gzip"`, varsayılan `"identity"`) belirlenir — bu header'ın anlamı tüm HTTP mesaj
gövdesinin content-coding'idir, TKMB'nin iç yapısını seçen özel bir alan değil. TKMB yanıtının
kendisi `entryEncoding` değerinden bağımsız olarak her zaman `identity` HTTP content-coding'i ile
gönderilir — `entryEncoding=gzip` seçildiğinde içerik zaten girdi bazında sıkıştırılmıştır,
üstüne bir de HTTP gzip'i uygulamak çifte sıkıştırma olurdu. Her iki durumda da girdi baytları
yayınlama zamanında önceden üretilmiş `.tkms`/`.tkms.gz` dosyalarından okunur — istek sırasında
sıkıştırma **yapılmaz**.
