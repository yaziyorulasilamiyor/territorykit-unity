# Faz 2 — LOD üretimi

Tarih: 2026-08-15 · Durum: Tamamlandı (bir mimari sapma + inceleme turu)
Dal: feat/phase-2-lod-topology · Commit sayısı: 16

## Ne yapıldı
TerritoryKit CLI build edildi, zincirde **kaldı** (`import geoboundaries`). `simplify.py` topojson ile
**paylaşılan arc** üzerinden sadeleştiriyor (Karar 2); `build.py` `--lod` alıyor; `scripts/build_lod.py`
zinciri tek komuta indiriyor, `check_lod_report.py` CI'da denetliyor.

## Nasıl doğrulandı
`ruff` + `mypy` temiz, **157 test geçti**, kapsam **%95**. **Çatlak testi (üçgenleme + float32 SONRASI,
üç seviyede): boşluk tam 0,0 · çakışma tam 0,0.** Paylaşılan vertex bit-eşitliği ve kapsama (81×50
noktanın hepsi **tam 1** üçgende) üç seviyede geçti; determinizm byte-identik; delik **0**.

| Seviye | Vertex | high'ın %'si | Üçgen | Bayt | Parça | Delik |
|---|---|---|---|---|---|---|
| kaynak | 366.157 | — | — | — | 705 | 0 |
| high | 240.379 | %100 | 238.969 | 3.359.438 | 705 | 0 |
| medium | 85.926 | %35,7 | 84.518 | 1.197.108 | 704 | 0 |
| low | 30.753 | **%12,8** | 29.383 | 424.914 | 685 | 0 |

Çatlağın **tam sıfır** olması tolerans değil zorunluluk: sadeleştirme paylaşılan arc üzerinde çalıştığı
için sınırın iki yanı **aynı sayılar**; sıfırdan büyüğü zincirin kırıldığını gösterir.

## Kararlar ve gerekçeleri
1. **Sadeleştirme TerritoryKit yerine topojson** — talimat `--strategy topology-safe` şart koşuyordu;
   strateji **önce denendi, sonra ölçüldü**: her ring'i bağımsız Douglas-Peucker'dan geçiriyor. Kanıt
   `high` çıktısı — 81 geometrinin **hepsi geçerli**, onarım gerekmiyor: 197 komşu çiftinin **32'sinde**
   çatlak, 0,0061 km² boşluk + 0,0189 km² çakışma (kaynakta sıfır). `low` daha kötü (161 çift, 57,86
   km²) ama çıktısında **23 geçersiz geometri** var. topojson: daha az vertex, **0 çatlak**.
2. **`simplify.py` yer tutucu kalmadı** — o madde sadeleştirmeyi TerritoryKit'in yapması varsayımına
   dayanıyordu; varsayım düşünce modül kendisine ayrılan işi yapıyor.
3. **`high` artık sadeleştiriliyor (5e-05)** — 365.481 → 240.379 vertex; "kaynağı korur" iddiası
   **ölçümle** tanımlı (705 parça, 0 delik, kayıp sıfır). Muğla uint16 payı %92,3 → %52,6.

## İnceleme düzeltmeleri
**U1 — geri çekilen iddia.** "48.204 bozuk segment" aslında `sharedSegmentCount` farkı ve **doğru
çalışan** sadeleştiricide de yüksek çıkıyor: **0 çatlak üreten topojson aynı formülde 47.357** alıyor
(doğrulandı). İddia kaldırıldı; metriğin işe yaramaması artık Issue B'nin konusu, A'nın kanıtı değil.
**U2 — düzeltilen sayı.** 63 km² şişmişti: TK'nın medium/low çıktısındaki 20/23 geçersiz geometri
onarılınca oluşan iç delikler boşluğa karışıyordu. Ayrıştırılınca low = **161/197, 57,86 km²**.
**U3** iki issue oldu (A `high` ile açılıyor) · **P1a/P1b** üst `lossy` tüm kaynakları kapsıyor, silinen
iç halkalar sayılıyor · **P2a-d** normalizasyon kaybı manifeste + CI'ya bağlandı, kayıp alan bazlı ve
eşikli, adacık alanları ve test sınırları düzeltildi.

## Bilinen eksikler ve riskler
- **Çatlak testi eksiksiz değil.** Boşluk metriği yalnız **kapalı** boşlukları görür — ülke dış sınırına
  açılan çatlak delik oluşturmaz; paylaşılan-vertex testi çift başına **en az bir** ortak vertex arar.
  İl-il iç sınırlar (bozulmanın asıl yeri) yakalanıyor; kapatmak vertex dizisi karşılaştırması ister.
- **geoBoundaries dosyaları importer'a olduğu gibi girmiyor.** `shapeGroup` alpha-3 → alpha-2, ve 1e-9
  deg² altındaki **7 gerçek adacık** (yerel projeksiyonda **2,0–6,1 m²**) düşürülüyor — sayılıyor ve
  her seviyenin manifestine giriyor, ama **veriden çıkıyorlar**.
- **Parça sayısı düşüşü kayıp değil.** `low`'da sayı 20 azalıyor ama gerçekten **1** parça yok oluyor
  (Artvin, 685 m²); kalan 19'u komşusuyla **birleşiyor**. Kayıp artık alan bazlı ve eşikli
  (`--max-lost-area`, varsayılan 10.000 m²).
- `low`'da sahte delikler oluşup siliniyor; **hiçbir toleransın hem %25 bütçesini hem sıfır sahte
  deliği sağlamadığı** ölçüldü. Delik yolları fixture'lara dayanıyor; Docker doğrulanmadı (Faz 0);
  yerel Python 3.14, hedef 3.12 → CI'da; topojson **1.10 zorunlu** (1.9 `np.in1d` çağırıyor).

## Tıkanmalar
**Bir tane, çözüldü.** `topology-safe` en kritik testi geçemiyordu; kendi implementasyonuma **geçmedim**, ölçümleri sunup sordum. Issue A ve B hazır, **açılmadı**.

## Sonraki faza hazırlık
Faz 3 önkoşulu **hazır**: üç seviye deterministik, ayrı dizinlerde, önceden üretilmiş.

## Değişen dosyalar
`src/geometry_api/`: `simplify.py` (yeni), `build.py` · `tests/test_lod.py` (yeni), `test_build.py` ·
`scripts/`: `build_lod.py`, `check_lod_report.py` (yeni) · `docs/territorykit-simplification-finding.md`
(yeni) · `ci.yml`, `README.md`, `CHANGELOG.md`, `pyproject.toml`
