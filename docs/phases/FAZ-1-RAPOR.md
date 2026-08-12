# Faz 1 — Geometri motoru

Tarih: 2026-08-12 · Durum: Tamamlandı
Dal: feat/phase-1-geometry-engine · Commit sayısı: 18

## Ne yapıldı
- `loader.py` — GeoJSON ve TerritoryKit `dataset.json` otomatik algılanıp tek modele normalize
- `projection.py` — ileri/ters, vektörize; origin dataset bbox merkezinden **hesaplanıyor**
- `triangulate.py` — kümülatif ring **bitiş** offset'leri, parça başına üçgenleme + index offset
- `encoding.py` — TKMS v1; üçgen başına CW normalizasyonu, `flags` bit0 otomatik, `bytes_consumed`
- `build.py` — `python -m geometry_api.build --input … --output …` → 81 mesh + `index.json`

## Nasıl doğrulandı
| Kontrol | Sonuç |
|---|---|
| `ruff check` + `ruff format --check` + `mypy src/` + `pytest --cov` | Temiz · **104 geçti**, kapsam **%95** |
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
| Determinizm · trailing byte | iki koşu 82 dosya **0 fark** · her dosyada `bytes_consumed == len` |

**uint16 sınırı:** Muğla 60.478 vertex = sınırın **%92,3'ü**, **5.057 pay**; 81 ilin hepsi sığıyor, birleşik ülke mesh'i (365.481) 5,6× aşar. Build CLI %80 üstünü uyarıyor — tek tetikleyen Muğla.

## Faz 2 ön koşulu — paylaşılan sınır doğrulaması
Faz 2'nin çatlak testi şuna dayanıyor: komşu illerin paylaştığı sınır noktası her iki il için de
**aynı** değerle çıkmalı. Yuvarlama bunu bozabilecek tek adımdı. Ölçüldü (`test_shared_boundaries.py`):

| Ölçüm | Sonuç |
|---|---|
| Komşu il çifti (STRtree + `intersects`) | 200 |
| En az bir **birebir eşit** kaynak vertex'i paylaşan çift | **200 / 200** |
| Toplam paylaşılan vertex | 58.179 |
| Projeksiyon + yuvarlama sonrası **farklı** değere düşen | **0** |

Mekanizma ayrıca sabitlendi: projeksiyon ve yuvarlama eleman bazlı — aynı koordinat tek başına ve 5000
noktalık dizi içinde **aynı baytı** veriyor. **Faz 2 sağlam zeminde başlıyor**; kalan risk yuvarlamada
değil, arc grafiğinin eşitlik yerine toleransla kurulmasında olur.

## Kararlar ve gerekçeleri
1. **Yuvarlama üçgenlemeden ÖNCE** — sonra cast etmek 16 ilde 62 üçgeni sıfır alana çökertiyordu
   (yönlendirilemez). Yuvarlanmış girdiyle sayılar aynı, çöken üçgen 0, alan kaybı ≤ 1,4e-7.
   Alternatif (encode'da atmak) reddedildi: semptomu gizler, kök nedeni bırakır.
2. **Kapsama testi alan testinin üstüne** — ölçtüm: hatalı ring offset'i (`[4,8,12]` yerine `[4,12]`)
   toplam alanı **tam doğru** veriyor ama boşluk + çakışma bırakıyor. Alan testi görmüyor, kapsama görüyor.
3. **Trailing byte'ta okuyucu hoşgörülü, yazıcı katı** — doküman "yok say" diyor, o kaldı; ama
   `bytes_consumed` eklendi ve kendi 81 dosyamızın sıfır dolgu ürettiği testle sabitlendi.

## Bilinen eksikler ve riskler
- `cos(originLat)` **düzeltilmedi, ölçüldü**: uçlarda ~%4-5 sapma — sınır çizimi için yeterli, mesafe ölçümü için değil.
- **Muğla payı %7,7** — dataset güncellenirse sınır aşılabilir; `test_mugla_stays_under_the_uint16_limit` nöbette.
- Kapsama noktaları rastgele; kenara **tam** düşen nokta iki üçgende sayılır. Olasılık ~0, ama varsayım.
- Gerçek dataset'te **delik yok** — delik yolu tamamen elle yazılmış fixture'lara dayanıyor.
- Docker doğrulanmadı (Faz 0 tıkanması sürüyor); yerel Python 3.14, hedef 3.12 → doğrulama CI'da.

## Tıkanmalar — Yok

## Sonraki faza hazırlık
- Faz 2 önkoşulu: **hazır** — paylaşılan sınır doğrulaması geçti; `--lod` bayrağı ve manifest alanı yerinde.

## Değişen dosyalar
- `src/geometry_api/`: `loader.py`, `projection.py`, `triangulate.py`, `encoding.py`, `build.py`
- `tests/`: `conftest.py`, `helpers.py`, `test_shared_boundaries.py`, `test_{loader,projection,triangulate,encoding,build}.py`
- `tests/fixtures/`: 4 fixture + `README.md` · `docs/`: `projection.md`, `mesh-format.md`
