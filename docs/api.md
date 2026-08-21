# Geometry API — HTTP yüzeyi

Kaynak API'si `/v1` altındadır. Altyapı/ops uç noktaları (`/health`, `/ready`, `/metrics`)
kasıtlı olarak `/v1` **dışında**, kökte kalır — bunlar kaynak temsilinin bir sürümü değil,
Kubernetes tarzı probe'lardır (bkz. `docs/phases/FAZ-3-PLAN.md` §13.0). Tam OpenAPI dokümanı
`/docs` altında otomatik üretilir.

## Uç noktalar

| Uç nokta | Açıklama |
|---|---|
| `GET /health` | Liveness. I/O yok. `{"status":"ok","version":"..."}` |
| `GET /ready` | Readiness — yapılandırılmış dataset'lerin **tamamı** sağlam mı |
| `GET /metrics` | Süreç-içi istek/cache sayaçları (kalıcı değil, çoklu-worker'da toplanmaz) |
| `GET /v1/datasets` | Yayınlanmış dataset listesi |
| `GET /v1/datasets/{id}` | Metadata: origin, bbox, `revisionId`, seviye başına `lossy`/`topologyChanged`/`pickingUnsafe` |
| `GET /v1/datasets/{id}/territories` | Cursor sayfalanmış bölge listesi; filtreler: `lod`, `bbox`, `parentId`, `administrativeLevel` |
| `GET /v1/datasets/{id}/viewport` | Cursor sayfalanmış görünür bölge id listesi (`bbox`, `lod` zorunlu) |
| `GET`/`HEAD /v1/datasets/{id}/revisions/{revisionId}/mesh/{territoryId}` | TKMS binary mesh (`lod` zorunlu query) |
| `POST /v1/datasets/{id}/revisions/{revisionId}/mesh/batch` | Çoklu mesh, TKMB konteyner |

`/v1/datasets/{id}`, `.../territories` ve `.../viewport` **`?revision=<id>`** kabul eder —
verilmezse güncel revizyon kullanılır. Mesh/batch uç noktalarında revizyon her zaman path'in bir
parçasıdır, "güncel" takma adı yoktur — artifact URL'leri her zaman kanonik ve değişmezdir.

## Sözleşme notları

- Binary yanıtlar (`mesh`, `mesh/batch`) `Content-Type: application/octet-stream`.
- Hatalar tek bir JSON şemasıyla döner (validation ve 500 dahil):
  `{"error": {"code": "...", "message": "...", "details": {}}}`.
- `lod` zorunlu, varsayılan yok — eksikse `422 validation_error`, geçersiz değerse
  (`high|medium|low` dışında) `400 unknown_lod`. LOD seçimi Unity'ye ait; `/v1/datasets/{id}`
  seviye bayraklarını **indirmeden önce** okumak için buradadır.
- **Tüm bbox koordinatları** (`/datasets/{id}` metadata, `territories`/`territory.bboxLocal`,
  `viewport?bbox=...`) yerel metre uzayındadır — [docs/projection.md](projection.md)'de
  tanımlanan, dataset origin'i çıkarılmış post-projection koordinatlar. WGS84 derece **değildir**.
- Mesh/batch yanıtları `Cache-Control: public, max-age=31536000, immutable` taşır — yalnız
  revizyonlu artifact URL'lerinde; metadata uç noktaları `public, max-age=30, must-revalidate`.
- ETag'ler strong, yayınlama zamanında önceden hesaplanır; `identity` ve `gzip` gövdeleri ayrı
  ETag alır. `If-None-Match` weak comparison ile karşılaştırılır (RFC 9110 §8.8.3.2); `304`
  yanıtı `ETag`/`Cache-Control`/`Vary` header'larını korur.
- Gzip önceden üretilmiş bir varyanttır (`.tkms.gz`), koşulsuz middleware ile değil — sunucu
  yalnız `Accept-Encoding` header'ına göre hangi hazır dosyayı döneceğine karar verir, hiçbir
  zaman istek sırasında sıkıştırma yapmaz. `Vary: Accept-Encoding` bu yüzden mesh yanıtlarında
  vardır.
- TKMB konteynerinin `entryEncoding`'i (`identity`/`gzip`) ayrı bir kavramdır, `Accept-Encoding`
  header'ından **türetilmez** — istek gövdesinde açık bir alan (bkz.
  [mesh-format.md](mesh-format.md)).
- Batch istek gövdesi
  `{"territoryIds":["id-a","id-b"],"lod":"high","entryEncoding":"identity"}` biçimindedir;
  `territoryIds` boş olamaz, tekrarlar tekilleştirilir ve dağıtılan varsayılan sözleşme en fazla
  **200 benzersiz id** kabul eder (`>200` → `400 batch_too_large`). Bulunamayan id'ler HTTP 404
  yerine TKMB'nin `missing` tablosunda döner; `entryEncoding` yalnız `identity` veya `gzip`'tir.

## Bilinmeyen/silinmiş revizyon

- Hiç var olmamış bir `revisionId` → `404 revision_not_found`.
- Retention penceresi dışına çıkmış (budanmış) bir `revisionId` → `410 revision_gone`,
  `error.details.prunedAt` ile.
- Yayınlama sonrası bozulmuş (checksum kendi adıyla uyuşmayan) bir revizyon → `500
  revision_corrupted`; aynı durum dataset'in **güncel** revizyonuysa `/ready` de `503` döner.

## Docker

`docker compose up` yerine `uvicorn geometry_api.main:app` ile yerel çalıştırma ve CI'da
`docker build` + gerçek konteyner health-check'i esas alınır — gerekçe
`docs/phases/FAZ-3-PLAN.md` §15'te.
