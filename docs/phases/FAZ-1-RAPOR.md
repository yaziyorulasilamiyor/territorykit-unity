# Faz 1 — Geometri motoru

Tarih: 2026-08-12 · Durum: Tamamlandı
Dal: feat/phase-1-geometry-engine · Commit sayısı: 13

## Ne yapıldı
- `loader.py` — GeoJSON `FeatureCollection` ve TerritoryKit `dataset.json` otomatik algılanıp tek
  modele normalize ediliyor; parent, `childIds` tersine çevrilerek türetiliyor
- `projection.py` — ileri/ters dönüşüm, numpy üzerinde vektörize; origin dataset bbox merkezinden
  **hesaplanıyor**, yapılandırılmıyor (aynı girdi → aynı yerel uzay)
- `triangulate.py` — kümülatif ring **bitiş** offset'leri, parça başına bağımsız üçgenleme +
  index offset, ring sarım normalizasyonu
- `encoding.py` — TKMS v1; üçgen başına saat yönü normalizasyonu, `flags` bit0 otomatik seçimi
- `build.py` — `python -m geometry_api.build --input … --output …` → 81 mesh + `index.json`
- `docs/projection.md` ve `docs/mesh-format.md` ölçülen sayılarla güncellendi

## Nasıl doğrulandı
| Kontrol | Sonuç |
|---|---|
| `ruff check`, `ruff format --check`, `mypy src/` | Temiz, 11 dosya |
| `pytest --cov=geometry_api` | **97 geçti**, kapsam **%95** |
| Alan korunumu (81 il) | en kötü **5,4e-15** (sınır %0,1) |
| **Kapsama** (84 geometri × 1000 nokta) | 84.000 noktanın hepsi **tam 1** üçgende |
| Dejenere üçgen | 364.057 üçgenin **0**'ı sıfır alanlı |
| Delik (Fixture C/D) | delik içi noktalar **0** üçgende |
| MultiPolygon | 21 çok parçalı geometride parça-arası üçgen **yok** |
| Winding | encode sonrası tüm üçgenler CW |
| Round-trip (81 il, uint16+uint32) | vertex/index birebir |
| Hassasiyet (405 nokta, ileri→ters) | en kötü **< 1e-6 m** (sözleşme < 1 m) |
| Ölçek hatası (bbox boyunca) | **−%4,11 … +%4,81**, origin enleminde < 1e-9 |
| Build (81 il) | 365.481 vertex, 364.057 üçgen, 5.110.782 bayt, 0,2 s |
| Determinizm | iki koşu, 82 dosya, **0 fark** |

**uint16 sınırı:** Muğla 60.478 vertex = sınırın **%92,3'ü**, **5.057 vertex pay**. 81 ilin hepsi
uint16'ya sığıyor; birleşik ülke mesh'i (365.481) 5,6× aşar → uint32 zorunlu olur. Build CLI %80
üstünü uyarıyor; bugün tetikleyen tek il Muğla.

## Kararlar ve gerekçeleri
1. **Vertex'ler üçgenlemeden ÖNCE float32'ye yuvarlanıyor** — float64'te üçgenleyip sonradan cast
   etmek 16 ilde 62 üçgeni sıfır alana çökertiyordu (yönlendirilemez → encoder reddediyor).
   Yuvarlanmış girdiyle vertex/üçgen sayısı aynı, çöken üçgen 0, alan kaybı ≤ 1,4e-7.
   Alternatif (çöken üçgenleri encode'da atmak) reddedildi: semptomu gizler, kök nedeni bırakır.
2. **Kapsama testi alan testinin yerine değil, üstüne** — ölçtüm: earcut'a hatalı ring offset'i
   (`[4,8,12]` yerine `[4,12]`) verildiğinde toplam alan **tam doğru** çıkıyor ama mesh'te boşluk
   ve çakışma var. Alan testi bu hatayı görmüyor, kapsama testi görüyor.
3. **Sarım normalizasyonu encode'da, ring normalizasyonu triangulate'te** — TKMS baytına dönüşen
   her şey tek boğazdan geçsin diye; Faz 2 LOD'ları ve Faz 3 batch'i garantiyi devralır.

## Bilinen eksikler ve riskler
- `cos(originLat)` modeli **düzeltilmedi, ölçüldü**: uçlarda ~%4-5 ölçek sapması. Sınır çizimi için
  kabul edilebilir, gerçek mesafe/alan ölçümü için değil.
- **Muğla payı %7,7** — dataset güncellenirse sınır aşılabilir; `test_mugla_stays_under_the_uint16_limit`
  sabit sayıyla nöbette.
- Kapsama testi noktaları rastgele; kenara **tam** düşen nokta iki üçgende sayılır. Olasılık pratikte
  sıfır, ama teorik olarak kırılgan bir varsayım.
- Gerçek dataset'te **delik yok** — delik yolu tamamen elle yazılmış fixture'lara dayanıyor.
- Docker hâlâ doğrulanmadı (Faz 0 tıkanması sürüyor); yerel Python 3.14, hedef 3.12 → doğrulama CI'da.

## Tıkanmalar
- Yok.

## Sonraki faza hazırlık
- Faz 2 için önkoşul durumu: **hazır**. `triangulate` projekte edilmiş shapely geometrisi aldığı için
  basitleştirme araya giriyor; `build.py`'de `--lod` bayrağı ve manifest alanı hazır.

## Değişen dosyalar
- `src/geometry_api/`: `loader.py`, `projection.py`, `triangulate.py`, `encoding.py`, `build.py`
- `tests/`: `conftest.py`, `helpers.py` + `test_{loader,projection,triangulate,encoding,build}.py`
- `tests/fixtures/`: 4 fixture + `README.md`
- `docs/projection.md`, `docs/mesh-format.md`
