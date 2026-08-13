# Faz 2 — LOD üretimi

Tarih: 2026-08-13 · Durum: Tamamlandı (bir mimari sapmayla)
Dal: feat/phase-2-lod-topology · Commit sayısı: 10

## Ne yapıldı
- TerritoryKit CLI build edildi, zincirde **kaldı** (`import geoboundaries`) · `simplify.py` — topojson
  ile **paylaşılan arc** üzerinden sadeleştirme (yer tutucu değil, bkz. Karar 2)
- `build.py` — `--lod high|medium|low`, ayrı dizin; `high` kayıp kapısı bir aşama öne alındı ·
  `scripts/build_lod.py` (tek komut, `--build-date` pinli) + `check_lod_report.py` (CI doğrulayıcısı)

## Nasıl doğrulandı
| Kontrol | Sonuç |
|---|---|
| `ruff` + `mypy` + `pytest --cov` | Temiz · **154 geçti**, kapsam **%96** |
| **Çatlak (üçgenleme + float32 SONRASI, 3 seviye)** | boşluk **tam 0,0** · çakışma **tam 0,0** |
| Paylaşılan vertex bit-eşitliği · kapsama | üç seviyede geçti · 81×50 noktanın hepsi **tam 1** üçgende |
| Vertex azalması (`low` ≤ high'ın %25'i) · determinizm | **%12,8** · üç seviyede byte-identik |
| Topoloji: bölge sayısı · uydurulan delik | 81 sabit · **0** (kaynakta da 0) |

| Seviye | Vertex | high'ın %'si | Üçgen | Bayt | Parça | Delik | Kayıp |
|---|---|---|---|---|---|---|---|
| kaynak | 366.157 | — | — | — | 705 | 0 | — |
| high | 240.379 | %100 | 238.969 | 3.359.438 | 705 | 0 | **yok** |
| medium | 85.926 | %35,7 | 84.518 | 1.197.108 | 704 | 0 | 1 parça |
| low | 30.753 | **%12,8** | 29.383 | 424.914 | 685 | 0 | 20 parça, 19 halka |

Çatlağın **tam sıfır** olması tolerans değil zorunluluk: sadeleştirme paylaşılan arc üzerinde çalıştığı
için sınırın iki yanı **aynı sayılar**; sıfırdan büyük her değer zincirin kırıldığını gösterir.

## Kararlar ve gerekçeleri
1. **Sadeleştirme TerritoryKit yerine topojson** — talimat `--strategy topology-safe` şart koşuyordu;
   strateji **önce denendi, sonra ölçüldü** ve topolojiyi korumuyor: her ring'i bağımsız
   Douglas-Peucker'dan geçiriyor, kendi `topologyAudit`'i `low`'da 57.978 segmentin 48.204'ünün
   bozulduğunu yazıyor, komut yine 0 dönüyor. Geometrik ölçüm: 197 komşu çiftinin **163'ünde** çatlak,
   63 km² boşluk, tek çiftte 2,03 km²'ye kadar. topojson aynı toleranslarda hem daha az vertex hem
   **0 çatlak**. Kullanıcı onayıyla geçildi. Alternatifler: kendi arc grafiğim (yasak ve gereksiz),
   TerritoryKit'i düzeltmek (`vendor/`'a dokunma kuralıyla çelişiyor).
2. **`simplify.py` yer tutucu kalmadı** — o madde sadeleştirmeyi TerritoryKit'in yapması varsayımına
   dayanıyordu; varsayım düşünce modül Faz 0 düzeninde kendisine ayrılan işi yapıyor.
3. **`high` artık sadeleştiriliyor (5e-05)** — 365.481 → 240.379 vertex. "Kaynağı korur" iddiası vertex
   sayısıyla değil **ölçümle** tanımlı: 705 parça ve 0 delik girip aynısı çıkıyor, kayıp sıfır, build
   kapısı zorluyor. Yan kazanç: Muğla uint16 tavanının %92,3'ünden %52,6'sına indi.

## Bilinen eksikler ve riskler
- **geoBoundaries dosyaları importer'a olduğu gibi girmiyor.** İki ön-normalizasyon şart: `shapeGroup`
  alpha-3 → alpha-2, ve 1e-9 deg² altındaki **7 gerçek adacık** (~10-20 m²) düşürülüyor. Sayılıyor ve
  rapora yazılıyor ama **veriden çıkıyorlar**; kaynak dosya değişmiyor.
  Ülke kodu `--country`'den alınıyor (alpha-3 kısaltılamaz: `TUR` → `TU`); importer'ın çapraz kontrolü
  kaybolmadı, öne alındı — tüm feature'lar tek `shapeGroup` üzerinde anlaşmak zorunda.
- `low`'da 19 sahte delik oluşup siliniyor. Silmek çatlak açamaz (paylaşılan sınırlar dış halkada), ama
  **hiçbir toleransın hem %25 bütçesini hem sıfır sahte deliği sağlamadığı** ölçüldü — temizlik zorunlu.
- Delik yolları fixture'lara dayanıyor (gerçek dataset'te delik yok). Docker doğrulanmadı (Faz 0
  tıkanması). Yerel Python 3.14, hedef 3.12 → CI'da. topojson **1.10 zorunlu** (1.9, numpy 2'de
  kaldırılan `np.in1d`'i çağırıyor).

## Tıkanmalar
**Bir tane, çözüldü.** `topology-safe` fazın en kritik testini geçemiyordu. "Tıkanma kuralı" uyarınca
kendi implementasyonuma **geçmedim**; ölçümleri sunup durdum ve sordum. Karar: topojson ile devam.
Bulgu belgesi hazır, **henüz upstream'e gönderilmedi** — Faz 3 öncesi yapılacak.

## Sonraki faza hazırlık
Faz 3 önkoşulu **hazır**: üç seviye deterministik ve ayrı dizinlerde — "FastAPI istek başına geometri
hesaplamaz" kuralının dayandığı artifact modeli yerinde.

## Değişen dosyalar
- `src/geometry_api/`: `simplify.py` (yeni), `build.py` · `tests/`: `test_lod.py` (yeni), `test_build.py`
- `scripts/`: `build_lod.py`, `check_lod_report.py` (yeni) · `docs/`:
  `territorykit-simplification-finding.md` (yeni), `PROJE-TALIMATI.md`, `REVIEWER-BRIEF.md`
- `.github/workflows/ci.yml`, `README.md`, `CHANGELOG.md`, `pyproject.toml`
