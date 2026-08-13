# Faz 1 — Geometri motoru

Tarih: 2026-08-13 · Durum: Tamamlandı (iki inceleme turu dahil)
Dal: feat/phase-1-geometry-engine · Commit sayısı: 33

## Ne yapıldı
- `loader.py` · `projection.py` — iki format algılama + **geçersiz geometri reddi**; ileri/ters projeksiyon, origin bbox merkezinden **hesaplanıyor**
- `triangulate.py` — ring **bitiş** offset'leri, parça başına üçgenleme + index offset, `GeometryLoss`
- `encoding.py` — TKMS v1; üçgen başına CW, `flags` bit0 otomatik, `bytes_consumed`, `strict` mod
- `build.py` — 81 mesh + `index.json`; `--clean`, `--allow-lossy`, `--repair-invalid`

## Nasıl doğrulandı
| Kontrol | Sonuç |
|---|---|
| `ruff` + `mypy` + `pytest --cov` | Temiz · **139 geçti**, kapsam **%96** |
| Alan korunumu (81 il) · dejenere üçgen | **< 1e-12** (sözleşme %0,1) · 364.057 üçgenin **0**'ı sıfır alanlı |
| **Kapsama** (84 geometri × 1000 nokta) | 84.000 noktanın hepsi **tam 1** üçgende |
| Delik (Fixture C/D) · MultiPolygon | delik içi nokta **0** üçgende · 21 geometride parça-arası üçgen **yok** |
| Winding · round-trip (81 il, uint16+uint32) | tüm üçgenler CW (`strict=True` ile de) · vertex/index birebir |
| **Hassasiyet (405 nokta, project → float32 → unproject)** · ölçek hatası | en kötü **0,032 m**, p95 **0,024 m** (sözleşme < 1 m) · **−%4,11 … +%4,81** |
| Build (81 il) · determinizm · kayıp muhasebesi | 365.481 vertex, 364.057 üçgen, 5.110.782 bayt · iki koşu **0 fark**, `bytes_consumed == len` · `skipped*`/`degenerateTriangles` **0**, `lossy: false` |

**uint16 sınırı:** Muğla 60.478 vertex = sınırın **%92,3'ü**, **5.057 pay**; 81 ilin hepsi sığıyor. Build CLI %80 üstünü uyarıyor — tek tetikleyen Muğla.

## İnceleme düzeltmeleri — 1. tur (3 kritik + 5 önemli + 4 küçük)
| # | Ne yapıldı |
|---|---|
| K1·K2 | Geçersiz poligon sessizce mesh oluyordu → politika açıkça **reddet** (`--repair-invalid` ile onarım + doğrulama + manifestte `repaired`). Windows'ta iki bölge aynı dosyaya yazılabiliyordu → case-fold anahtarlar, `CON/PRN/AUX/NUL/COM1-9/LPT1-9`, sondaki nokta |
| K3 | Çöken parça/delik sessizdi → sıfır alanlı ring sayılıyor, manifeste giriyor, `high`'da hata (2. turda genişletildi) |
| Ö1-Ö3 | CI gerçek veriyi koşuyor + skip **hata**; index dtype/işaret/aralık **cast'ten önce**; bbox katı (NaN/sıra/**gerçek vertex sınırlarıyla eşitlik**), winding+dejenerasyon `strict=True` |
| Ö4·Ö5·Kü1-5 | Hassasiyet gerçek yolda (**0,032 m**); antimeridyen belgelendi; README/CHANGELOG · `--clean` · `==` pin · **200 kesişen / 197 gerçek ortak sınır** · tekrarlanamayan sayı yerine sınır |

## İnceleme düzeltmeleri — 2. tur (3a, 3b)
**3a — üçüncü sessiz yol vardı ve yapısaldı.** Dejenere üçgen temizliği parça kaydedildikten *sonra*
çalışıyordu: bir parçanın tüm üçgenleri düşerse parça kayboluyor, `part_count` saymaya devam ediyordu.
3 geçerli parçalı girdide üretildi — üçüncü parça yok, `skippedParts: 0`, `lossy: false`. Çözüm bayrak
eklemek değil **yapı değiştirmek**: temizlik parça bazında ve kayıttan önce, `skipped_parts` **sonuçtan**
türetiliyor (giren − çıkan) — dördüncü yol kendiliğinden sayılır. Sayaçlar tek `GeometryLoss` yapısında;
`build.py` manifeste yayıyor; üçgeni olmayan kayıtlı parça iç değişmez kontrolüyle reddediliyor.

**3b — fixture boş kontroldü.** Eski delik duplicate temizliğinden sonra 2 noktaya düşüyordu: **eski
uzunluk kontrolü de** yakalardı. Yeni fixture duplicate sonrası **3 ayrı nokta** koruyor, yalnızca
float32'de doğrusallaşıyor (226 m²); test uzunluk kontrolünün **geçtiğini** ve reddedenin alan kontrolü
olduğunu ayrı ayrı iddia ediyor. **CI skip'i de sessiz değil**, atlanan test sayısı ve eksik kapsam basılıyor.

## Faz 2 ön koşulu — paylaşılan sınır doğrulaması
| Ölçüm | Sonuç |
|---|---|
| Kesişen il çifti · **gerçek ortak sınırı olan** | 200 · **197** (3'ü tek noktada temas) |
| Paylaşılan vertex · yuvarlama sonrası sapan | 58.179 · **0** |

Mekanizma da sabitlendi: yuvarlama eleman bazlı — aynı koordinat tek başına ve 5000 noktalık dizi içinde **aynı baytı** veriyor. **Faz 2 sağlam zeminde başlıyor.**

## Kararlar ve gerekçeleri
1. **Yuvarlama üçgenlemeden ÖNCE** — sonra cast etmek 16 ilde 62 üçgeni sıfır alana çökertiyordu; yuvarlanmış girdiyle sayılar aynı, çöken üçgen 0, alan kaybı ≤ 1,4e-7.
2. **Kayıp muhasebesi sonuç bazlı, sebep bazlı değil** — her sebebe bayrak eklemek üçüncü sessiz yolu doğurdu; giren/çıkan farkı dördüncüsünü baştan kapatıyor.
3. **Kayıp veri `high`'da hatadır** — Faz 2'nin alt seviyeleri detayı bilerek atacak; bu kapı yalnızca atmadığını iddia eden seviye için.

## Bilinen eksikler ve riskler
- `cos(originLat)` **düzeltilmedi, ölçüldü**: uçlarda ~%4-5 sapma. **Antimeridyen desteklenmiyor** · **Muğla payı %7,7**, regresyon testinde.
- Kapsama noktaları rastgele; kenara **tam** düşen nokta iki üçgende sayılır (olasılık ~0). Gerçek dataset'te **delik yok** — delik ve kayıp yolları fixture'lara dayanıyor. Docker doğrulanmadı (Faz 0 tıkanması); yerel Python 3.14, hedef 3.12 → doğrulama CI'da.

## Tıkanmalar — Yok

## Sonraki faza hazırlık
Faz 2 önkoşulu **hazır** — paylaşılan sınır doğrulaması geçti; `--lod` bayrağı ve manifest alanı yerinde.

## Değişen dosyalar
- `src/geometry_api/`: `loader.py`, `projection.py`, `triangulate.py`, `encoding.py`, `build.py`
- `tests/`: `conftest.py`, `helpers.py`, `test_shared_boundaries.py`, `test_{loader,projection,triangulate,encoding,build}.py` · `tests/fixtures/`: 7 fixture · `docs/`: `projection.md`, `mesh-format.md` · `ci.yml`, `pyproject.toml`, `README.md`, `CHANGELOG.md`
