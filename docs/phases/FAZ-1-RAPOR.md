# Faz 1 — Geometri motoru

Tarih: 2026-08-12 · Durum: Tamamlandı (inceleme düzeltmeleri dahil)
Dal: feat/phase-1-geometry-engine · Commit sayısı: 27

## Ne yapıldı
- `loader.py` — GeoJSON + TerritoryKit `dataset.json` algılama; **geçersiz geometri reddedilir**
- `projection.py` — ileri/ters, vektörize; origin bbox merkezinden **hesaplanıyor**
- `triangulate.py` — kümülatif ring **bitiş** offset'leri, parça başına üçgenleme + index offset
- `encoding.py` — TKMS v1; üçgen başına CW, `flags` bit0 otomatik, `bytes_consumed`, `strict` mod
- `build.py` — 81 mesh + `index.json`; `--clean`, `--allow-lossy`, `--repair-invalid`

## Nasıl doğrulandı
| Kontrol | Sonuç |
|---|---|
| `ruff` + `mypy` + `pytest --cov` | Temiz · **134 geçti**, kapsam **%96** |
| Alan korunumu (81 il) · dejenere üçgen | **< 1e-12** (sözleşme %0,1) · 364.057 üçgenin **0**'ı sıfır alanlı |
| **Kapsama** (84 geometri × 1000 nokta) | 84.000 noktanın hepsi **tam 1** üçgende |
| Delik (Fixture C/D) · MultiPolygon | delik içi nokta **0** üçgende · 21 geometride parça-arası üçgen **yok** |
| Winding · round-trip (81 il, uint16+uint32) | tüm üçgenler CW (`strict=True` ile de) · vertex/index birebir |
| **Hassasiyet (405 nokta, project → float32 → unproject)** | en kötü **0,032 m**, p95 **0,024 m** (sözleşme < 1 m) |
| Ölçek hatası (bbox boyunca) | **−%4,11 … +%4,81**, origin enleminde < 1e-9 |
| Build (81 il) · determinizm · trailing byte | 365.481 vertex, 364.057 üçgen, 5.110.782 bayt, 0,2 s · iki koşu **0 fark** · `bytes_consumed == len` |

**uint16 sınırı:** Muğla 60.478 vertex = sınırın **%92,3'ü**, **5.057 pay**; 81 ilin hepsi sığıyor. Build CLI %80 üstünü uyarıyor — tek tetikleyen Muğla.

## İnceleme düzeltmeleri (3 kritik + 5 önemli + 4 küçük)
| # | Bulgu | Ne yapıldı |
|---|---|---|
| K1 | Geçersiz poligon sessizce mesh oluyordu | Politika açıkça **reddet**; `--repair-invalid` ile `make_valid` + sonuç doğrulaması + manifestte `repaired` |
| K2 | Windows'ta iki bölge aynı dosyaya yazılabiliyordu | Çakışma anahtarları case-fold; `CON/PRN/AUX/NUL/COM1-9/LPT1-9` ve sondaki nokta ele alındı |
| K3 | Çöken parça/delik sessizce kayboluyordu | Sıfır alanlı ring artık **sayılıyor**; manifestte bölge başına kayıt, uyarı satırı, `high`'da **hata** (`--allow-lossy` ile izin) |
| Ö1 | CI gerçek veriyi hiç çalıştırmıyordu | `ci.yml` fetch betiğini koşuyor; `GEOMETRY_API_REQUIRE_SAMPLE_DATASET` ile skip **hata** sayılıyor |
| Ö2·Ö3 | Encoder index'i sessizce cast ediyor, decoder geçersiz mesh kabul ediyordu | dtype/işaret/aralık **cast'ten önce**; bbox katı (NaN/sıra/**gerçek vertex sınırlarıyla eşitlik**); winding+dejenerasyon `strict=True`; doküman iddiası daraltıldı |
| Ö4 | Hassasiyet testi yanlış katmanı ölçüyordu | float32 cast'i yola eklendi: **0,032 m** (eski 1e-6 sayısı kaldırıldı) |
| Ö5 | Antimeridyen sessizdi | `projection.md` + `mesh-format.md`'ye **bilinen sınır** olarak yazıldı (38.814 km → 38.814 milyon m ölçümüyle) |
| Kü1-3 | README/CHANGELOG eski, çıktı dizini kalıntı bırakıyor, bağımlılıklar gevşek | Güncellendi · `--clean` eklendi · sürümler `==` ile sabitlendi |
| Kü4·Kü5 | "200 komşu çift" yanıltıcı, "5,4e-15" tekrarlanamıyordu | **200 kesişen / 197 gerçek ortak sınır** ikisi de teste bağlandı; tekrarlanamayan sayı yerine **sınır** (`< 1e-12`) |

## Faz 2 ön koşulu — paylaşılan sınır doğrulaması
| Ölçüm | Sonuç |
|---|---|
| Kesişen il çifti · **gerçek ortak sınırı olan** | 200 · **197** (3'ü tek noktada temas) |
| En az bir **birebir eşit** kaynak vertex'i paylaşan çift | **200 / 200** |
| Toplam paylaşılan vertex · yuvarlama sonrası sapan | 58.179 · **0** |

Mekanizma da sabitlendi: yuvarlama eleman bazlı — aynı koordinat tek başına ve 5000 noktalık dizi içinde **aynı baytı** veriyor. **Faz 2 sağlam zeminde başlıyor.**

## Kararlar ve gerekçeleri
1. **Yuvarlama üçgenlemeden ÖNCE** — sonra cast etmek 16 ilde 62 üçgeni sıfır alana çökertiyordu.
   Yuvarlanmış girdiyle sayılar aynı, çöken üçgen 0, alan kaybı ≤ 1,4e-7.
2. **Geçersiz geometride reddet, onarma varsayılan değil** — `make_valid` şekli değiştirir; bu bir
   karar olmalı. Onarılan bölge manifestte işaretleniyor.
3. **Kayıp veri `high`'da hatadır** — Faz 2'nin alt seviyeleri detayı bilerek atacak; bu kapı yalnızca atmadığını iddia eden seviye için.

## Bilinen eksikler ve riskler
- `cos(originLat)` **düzeltilmedi, ölçüldü**: uçlarda ~%4-5 sapma — mesafe ölçümü için kullanılamaz. **Antimeridyen desteklenmiyor** (Ö5) · **Muğla payı %7,7**, regresyon testinde.
- Kapsama noktaları rastgele; kenara **tam** düşen nokta iki üçgende sayılır. Olasılık ~0, ama varsayım.
- Gerçek dataset'te **delik yok** — delik ve kayıp yolları elle yazılmış fixture'lara dayanıyor.
- Docker doğrulanmadı (Faz 0 tıkanması sürüyor); yerel Python 3.14, hedef 3.12 → doğrulama CI'da.

## Tıkanmalar — Yok

## Sonraki faza hazırlık
- Faz 2 önkoşulu: **hazır** — paylaşılan sınır doğrulaması geçti; `--lod` bayrağı ve manifest alanı yerinde.

## Değişen dosyalar
- `src/geometry_api/`: `loader.py`, `projection.py`, `triangulate.py`, `encoding.py`, `build.py`
- `tests/`: `conftest.py`, `helpers.py`, `test_shared_boundaries.py`, `test_{loader,projection,triangulate,encoding,build}.py` · `tests/fixtures/`: 6 fixture
- `docs/`: `projection.md`, `mesh-format.md` · `.github/workflows/ci.yml`, `pyproject.toml`, `README.md`, `CHANGELOG.md`
