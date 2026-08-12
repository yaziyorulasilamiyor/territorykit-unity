# Geometry API — planlanan uç noktalar

Bu dosya, HTTP API'nin planlanan yüzeyini listeler. Faz 3'te uygulanana kadar aşağıdaki
uç noktaların çoğu **henüz mevcut değildir**. Faz 0'da sadece `GET /health` uygulanmıştır.

| Uç nokta | Durum | Açıklama |
|---|---|---|
| `GET /health` | ✅ Faz 0 | `{"status":"ok","version":"0.1.0"}` |
| `GET /datasets` | ⏳ Faz 3 | Mevcut dataset listesi |
| `GET /datasets/{id}` | ⏳ Faz 3 | Metadata: `originLon`, `originLat`, `projection`, seviye listesi, bölge sayısı, bbox |
| `GET /datasets/{id}/territories` | ⏳ Faz 3 | Bölge listesi: id, ad, parent, bbox, komşular |
| `GET /datasets/{id}/mesh/{territory_id}?lod=medium` | ⏳ Faz 3 | TKMS binary mesh (bkz. [mesh-format.md](mesh-format.md)) |
| `GET /datasets/{id}/viewport?bbox=x1,y1,x2,y2&lod=medium` | ⏳ Faz 3 | Görünür bölge id listesi |
| `POST /datasets/{id}/mesh/batch` | ⏳ Faz 3 | Çoklu mesh, tek istekte (TKMB konteyner formatı) |

## Sözleşme notları (Faz 3'te uygulanacak)

- Binary yanıtlar (`mesh`, `mesh/batch`) `Content-Type: application/octet-stream`.
- `ETag` + `Cache-Control` desteklenecek, `304 Not Modified` dönebilecek.
- Hatalar tutarlı bir JSON şemasıyla dönecek: `{"error": {"code": ..., "message": ...}}`.
- `lod` parametresi `high` | `medium` | `low` değerlerini alacak (bkz. Faz 2).
- **Tüm bbox koordinatları (`/datasets/{id}` metadata, `/territories` içindeki bbox, ve
  `/viewport?bbox=...` sorgu parametresi) yerel metre uzayındadır** —
  [docs/projection.md](projection.md)'de tanımlanan, dataset origin'i çıkarılmış post-projection
  koordinatlar. WGS84 derece **değildir**. Bu, Unity tarafının kamera viewport'unu doğrudan
  aynı uzayda hesaplayıp sorgulayabilmesi içindir; ekstra bir dönüşüm gerekmez.

Tam OpenAPI dokümanı Faz 3'te `/docs` altında otomatik üretilecektir.
