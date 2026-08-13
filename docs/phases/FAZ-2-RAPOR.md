# Faz 2 — LOD üretimi

Tarih: 2026-08-13 · Durum: Tamamlandı (bir mimari sapmayla — aşağıda)
Dal: feat/phase-2-lod-topology · Commit sayısı: 9

## Ne yapıldı

- **TerritoryKit CLI build edildi ve zincirde kaldı** — `import geoboundaries` adımı onu kullanıyor
- `simplify.py` — topojson ile **paylaşılan arc** üzerinden sadeleştirme (yer tutucu değil, bkz. Kararlar)
- `build.py` — `--lod high|medium|low`, seviye başına ayrı çıktı dizini; `high` kapısı bir aşama öne alındı
- `scripts/build_lod.py` — geoBoundaries'ten üç seviyeye tek komut, deterministik (`--build-date` pinli)
- `scripts/check_lod_report.py` — CI'ın zincir çıktısını doğrulaması için; bozuk raporlara karşı sınandı
- `docs/territorykit-simplification-finding.md` — upstream'e bildirilecek bulgu, tekrar üretme adımlarıyla

## Nasıl doğrulandı

| Kontrol | Sonuç |
|---|---|
| `ruff` + `mypy` + `pytest --cov` | Temiz · **154 geçti**, kapsam **%96** |
| **Çatlak (üçgenleme + float32 SONRASI, 3 seviye)** | boşluk **tam 0,0** · çakışma **tam 0,0** |
| Paylaşılan vertex bit-eşitliği · kapsama | üç seviyede de geçti · 81×50 nokta, hepsi **tam 1** üçgende |
| Vertex azalması (`low` ≤ high'ın %25'i) | **%12,8** |
| Topoloji: bölge sayısı · uydurulan delik | 81 sabit · **0** (kaynakta da 0) |
| Determinizm | üç seviyede byte-identik |
| Uçtan uca zincir | geoBoundaries → 3 seviye, `lod-report.json` |

**Seviye tablosu (81 il)**

| Seviye | Vertex | high'ın %'si | Üçgen | Bayt | Parça | Delik | Kayıp |
|---|---|---|---|---|---|---|---|
| kaynak | 366.157 | — | — | — | 705 | 0 | — |
| high | 240.379 | %100 | 238.969 | 3.359.438 | 705 | 0 | **yok** |
| medium | 85.926 | %35,7 | 84.518 | 1.197.108 | 704 | 0 | 1 parça |
| low | 30.753 | **%12,8** | 29.383 | 424.914 | 685 | 0 | 20 parça, 19 halka |

Çatlağın **tam sıfır** olması tolerans değil zorunluluk: Faz 1 komşuların bit-eşit vertex aldığını
ve yuvarlamanın eleman bazlı olduğunu kanıtlamıştı; sadeleştirme paylaşılan arc üzerinde
çalıştığı için sınırın iki yanı **aynı sayılar**. Sıfırdan büyük her değer bu zincirin
kırıldığı anlamına gelir, tolerans onu gizlerdi.

## Kararlar ve gerekçeleri

1. **Sadeleştirme TerritoryKit yerine topojson ile** — Faz talimatı `--strategy topology-safe`
   kullanmayı şart koşuyordu; strateji **önce denendi, sonra ölçüldü** ve topolojiyi korumuyor:
   her ring'i bağımsız Douglas-Peucker'dan geçiriyor, kendi `topologyAudit` alanı `low`'da 57.978
   paylaşılan segmentin 48.204'ünün bozulduğunu yazıyor ve komut yine 0 dönüyor. Geometrik ölçüm:
   197 komşu çiftinin **163'ünde** çatlak, toplam 63 km² boşluk, tek çiftte 2,03 km²'ye kadar.
   topojson aynı toleranslarda hem daha az vertex hem **0 çatlak** veriyor. Kullanıcı onayıyla
   geçildi. Alternatif: kendi arc grafiğimi yazmak — reddedildi, yasak ve gereksiz; TerritoryKit'i
   düzeltip PR açmak — reddedildi, `vendor/`'a dokunma kuralıyla çelişiyor.
2. **`simplify.py` yer tutucu kalmadı** — talimattaki "yer tutucu" maddesi sadeleştirmeyi
   TerritoryKit'in yapması varsayımına dayanıyordu. Varsayım düştüğü için modül Faz 0 düzeninde
   kendisine ayrılan işi yapıyor.
3. **`high` artık sadeleştiriliyor (5e-05)** — 365.481 → 240.379 vertex. "Kaynağı korur" iddiası
   vertex sayısıyla değil **ölçümle** tanımlı: 705 parça ve 0 delik girip aynısı çıkıyor, kayıp
   sıfır ve build kapısı bunu zorluyor. Yan kazanç: Muğla uint16 tavanının %92,3'ünden %52,6'sına
   indi — Faz 1'in risk olarak yazdığı pay sorunu kapandı.

## Bilinen eksikler ve riskler

- **geoBoundaries dosyaları TerritoryKit importer'ına olduğu gibi girmiyor.** İki ön-normalizasyon
  şart: `shapeGroup` alpha-3 (`TUR`) → alpha-2, ve 1e-9 deg² altındaki **7 gerçek adacık**
  (Muğla/İstanbul, ~10-20 m²) düşürülüyor. Adacıklar sayılıyor, basılıyor ve rapora yazılıyor ama
  **veriden çıkıyorlar** — kaynak dosya değişmiyor.
- Ülke kodu `--country`'den alınıyor, `shapeGroup`'tan türetilmiyor (alpha-3 kısaltılamaz: `TUR` →
  `TU`). Importer'ın çapraz kontrolü kaybolmadı, öne alındı: dosyadaki tüm feature'lar tek bir
  `shapeGroup` üzerinde anlaşmak zorunda.
- `low`'da 19 sahte delik oluşuyor (dar körfezlerde sınırın kendi üzerinden geçmesi) ve
  siliniyor. Silmek çatlak açamaz — paylaşılan sınırlar dış halkada, silinen iç halkada — ama
  **hiçbir toleransın hem %25 vertex bütçesini hem sıfır sahte deliği sağlamadığı** ölçüldü;
  temizlik bu yüzden var, tercih değil.
- Delik yolları hâlâ fixture'lara dayanıyor: gerçek dataset'te delik yok. Docker doğrulanmadı
  (Faz 0 tıkanması). Yerel Python 3.14, hedef 3.12 → doğrulama CI'da.
- topojson 1.10 zorunlu: 1.9 numpy 2'de kaldırılan `np.in1d`'i çağırıyor.

## Tıkanmalar

**Bir tane, çözüldü.** TerritoryKit'in `topology-safe` stratejisi fazın en kritik testini
geçemiyordu. Talimatın "Tıkanma kuralı" maddesi uyarınca kendi implementasyonuma **geçmedim**;
ölçümleri sunup durdum ve kullanıcıya sordum. Karar: topojson ile devam, bulgu upstream'e
raporlanacak. Bulgu belgesi hazır, **henüz gönderilmedi.**

## Sonraki faza hazırlık

Faz 3 önkoşulu **hazır**: üç seviye üretiliyor, artifact'lar deterministik ve seviye başına ayrı
dizinde — "FastAPI istek başına geometri hesaplamaz" kuralının dayandığı önceden-üretilmiş
artifact modeli yerinde. `lod-report.json` ve `index.json` manifest için gereken alanları taşıyor.

## Değişen dosyalar

- `src/geometry_api/`: `simplify.py` (yeni), `build.py`
- `tests/`: `test_lod.py` (yeni), `test_build.py`
- `scripts/`: `build_lod.py`, `check_lod_report.py` (ikisi de yeni)
- `docs/`: `territorykit-simplification-finding.md` (yeni), `PROJE-TALIMATI.md`, `REVIEWER-BRIEF.md`
- `.github/workflows/ci.yml`, `README.md`, `CHANGELOG.md`, `pyproject.toml`
