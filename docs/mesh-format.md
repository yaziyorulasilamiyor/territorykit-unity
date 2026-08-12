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

## TKMB — Mesh Batch konteyneri (Faz 3'te kullanılır)

`POST /datasets/{id}/mesh/batch` birden çok TKMS mesh'ini tek yanıtta döner. Format Faz 3'te
tanımlanıp bu dosyaya eklenecektir.
