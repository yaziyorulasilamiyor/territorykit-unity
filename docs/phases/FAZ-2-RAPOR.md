# Faz 2 — LOD üretimi

Tarih: 2026-08-17 · Durum: Tamamlandı (bir mimari sapma + dört inceleme turu)
Dal: feat/phase-2-lod-topology · Commit sayısı: 61

## Ne yapıldı
TerritoryKit CLI build edildi, zincirde **kaldı** (`import geoboundaries`); `simplify.py` topojson ile **paylaşılan arc**
üzerinden sadeleştiriyor (Karar 1); `build.py` `--lod` alıyor; `build_lod.py` zinciri tek komuta indiriyor,
`check_lod_report.py` CI'da denetliyor, `repro_territorykit_finding.py` upstream bulgularını üretiyor. 4. tur kayıp
muhasebesini **yapısal olarak** değiştirdi (Karar 3): tipli kayıt şeması + seviye başına bütçe denkliği.

## Nasıl doğrulandı
`ruff` + `mypy` temiz, **231 test geçti**, kapsam **%95**, determinizm byte-identik. **Çatlak testi (üçgenleme + float32
SONRASI, üç seviyede): boşluk tam 0,0 · çakışma tam 0,0** — tolerans değil zorunluluk: paylaşılan arc üzerinde
sadeleştirilen sınırın iki yanı aynı sayılar. Paylaşılan vertex bit-eşitliği ve kapsama (81×50 nokta, hepsi **tam 1**
üçgende) üç seviyede geçti. Seçim güvenliği manifeste yazılı: `high` `pickingUnsafe:false`, medium/low `true`.

| Seviye | Vertex | high'ın %'si | Üçgen | Parça | Delik | Korunan alan | En kötü parça | Birleşmenin eklediği |
|---|---|---|---|---|---|---|---|---|
| kaynak | 366.157 | — | — | 705 | 0 | — | — | — |
| high | 240.379 | %100 | 238.969 | 705 | 0 | %99,9994 | %53,9 | 0 km² |
| medium | 85.926 | %35,7 | 84.518 | 704 | 0 | %99,982 | %15,6 | 15,9 km² |
| low | 30.753 | **%12,8** | 29.383 | 685 | 0 | %99,842 | %15,6 | 269,8 km² |

## Kararlar ve gerekçeleri
1. **Sadeleştirme TerritoryKit yerine topojson** — strateji **önce denendi, sonra ölçüldü**: her ring'i bağımsız
   Douglas-Peucker'dan geçiriyor. Kanıt, 81 geometrisi geçerli olduğu için onarımın suçlanamayacağı `high`: 197 komşu
   çiftinin **32'sinde** çatlak (kaynakta sıfır, topojson'da sıfır).
2. **Parça sayısı seviyeler arası sabit değil, bilinçli** — bedeli ölçüldü (yukarıdaki son kolon); alternatif olan
   toleransı düşürmek %25 vertex bütçesini ihlal ediyor.
3. **Muhasebe "bilinen yolları say"dan "denklik kur"a geçti** (4. tur) — üç turda her düzeltme yeni bulunan bir kayıp
   yolunu saydı ve her turda listede olmayan bir yol çıktı; dördüncü kez tek tek yamamak reddedildi.

## 4. inceleme turu — yapı değişti
Önceki üç turun düzeltmeleri git geçmişinde. Bu tur **kuralın kendisini** kırdı: `lossy`, alan adının `dropped`/`skipped`/
`lost`/`removed`/`degenerate` ile başlamasına bakıyordu — `removedRings` bayrağı kaldırıyor, aynı şeyi anlatan
`collapsedParts` ve `discarded_holes` kaldırmıyordu, CI üçünü de kabul ediyordu; önek listesi de iki kopyaydı.

- **Engelleyici (B1) — kaybolan kaynak deliği.** `_drop_artifact_holes` yalnız **çıktıda hâlâ bulunan** delikleri
  dolaşıyordu: kaynak deliği tamamen kaybolunca incelenecek ring yok, `holeCount` 0'a düşüyor, seviye `lossy:false`, CI
  `[]`. Eşleşme artık **iki yönlü** (`dropped_hole`), CI yalnız *eklenen* deliğe bakmıyor. İki kalıcı test: biri kaydı,
  biri denkliğin **delik nedir bilmeden** aynı vakayı yakaladığını gösteriyor.
- **Y1/Y2 — isim öneki gitti.** `LossEvent(stage, kind, count, area, details)`, kapalı `EVENT_KINDS`, bilinmeyen `kind`'de
  **FAIL-CLOSED**, `lossy = bool(kayıp kategorisi olayları)`. Şema **tek yerde**; `check_lod_report.py` onu import ediyor
  ama bayrağı, bütçeyi ve iki denkliği **kendisi** hesaplıyor — ortak sözlük, ayrı cevap.
- **Y3 — bütçe denkliği.** İl başına `kaynak alan + kaydedilen eklenen − kaydedilen çıkan = çıktı alan`; `kaynak parça −
  çıktı parça = düşen + birleşme − bölünme − oluşan`; aynısı delikler için. Tutmazsa **build düşer**.
- **Ö1 — %10 uçurumu.** %10 artık yalnız "işlevsel olarak kaybolmuş parça" politikası; hayatta kalanın kaybı ayrı kayıt
  (`retainedAreaRatio`, `minPartRetainedAreaRatio`, `severe_shrink` <%50). %11 hem hayatta hem kayıtlı ve %89 bütçenin
  çıkan tarafında, %9 düşen parça — ikisi de test.
- **Ö2/Ö3 — oluşan parça.** Özdeşlik yalnız gerçek fixture'da sınanıyordu, sentetik problarda **yanlıştı**; eksik terim
  eşleşmeyen **çıktı** parçasıydı (`part_created`). Her prob artık aynı denkliği sınıyor, A+B→A+C testi C'yi de.
- **Ö5 / Ö4.** `topologyChanged` + `pickingUnsafe` manifestin üst düzeyinde, `mergeAddedArea` sahte kara köprüsünün
  büyüklüğü (üst sınır), CI ikisini kayıtlardan türetiyor. Talimattaki "seçim için high/medium" → **yalnız high**:
  `medium`'da da 3 birleşme, 3 bölünme, 1 yok olan parça (Artvin) ölçüldü.
- **I2 — Issue B hazır.** 47.358 "nihai boru hattı" değil **`make_valid` sonrası poligon**; mesh/float32 sıfır-çatlak
  iddiası ayrı ölçüm olarak ayrıştırıldı; betik CLI'ın `exit code=0 ok=true issues=[]` cevabını artık **basıyor** (ana
  kanıt, önceden okunup atılıyordu); 48.204 ↔ 48.200 farkı açıklandı. **Issue A'ya dokunulmadı.**

## Bilinen eksikler ve riskler
- **Bütçe denkliğinin sınırı, fazla iddia edilmesin diye:** alan tarafında kayıtlar ölçümü üreten aynı ayrıştırmadan
  türüyor, doğru bir build'de iki taraf inşaat gereği uyuşur; yakaladığı şey ayrıştırmanın **dışında** değişen geometri.
  **Asıl dişler sayma denkliklerinde** — olayların iddiasını geometrinin gerçeğiyle karşılaştırıyor, B1 böyle yakalandı.
- **Çatlak testi eksiksiz değil.** Boşluk metriği yalnız **kapalı** boşlukları görür, paylaşılan-vertex testi çift başına
  **en az bir** ortak vertex arar; il-il iç sınırlar yakalanıyor, kapatmak vertex dizisi karşılaştırması ister.
- **"Alan kaybı yok" doğru değil.** `low`'da kaynak alanın **1.236 km²'si** kapsanmıyor, çıktı kaynakta olmayan 1.357 km²
  kapsıyor; en kötü parça **%15,6'sında** kalıyor, 40 parça %50 altında (`high`'da 4,86 km²).
- **geoBoundaries dosyaları importer'a olduğu gibi girmiyor:** `shapeGroup` alpha-3 → alpha-2; 1e-9 deg² altındaki **7
  gerçek adacık** (2,0–6,1 m²) `dropped_islet` olarak sayılıp manifeste giriyor ama **veriden çıkıyorlar**.
- `low`'da 22 sahte delik oluşup siliniyor (`artifact_hole_removed` — onarım, kayıp değil); hiçbir toleransın hem %25
  bütçesini hem sıfır sahte deliği sağlamadığı ölçüldü. Docker doğrulanmadı (Faz 0); topojson **1.10 zorunlu**.

## Tıkanmalar
**Bir tane, çözüldü.** `topology-safe` en kritik testi geçemiyordu; kendi implementasyonuma **geçmedim**, ölçümleri sunup
sordum. Issue A ve B tekrar üretimiyle hazır, **açılmadı**.

## Sonraki faza hazırlık
Faz 3 önkoşulu **hazır**: üç seviye deterministik, ayrı dizinlerde, önceden üretilmiş. Faz 4-5 için manifest
`topologyChanged` / `pickingUnsafe` taşıyor; tüketen kod o fazlarda yazılacak.

## Değişen dosyalar
`src/geometry_api/`: `loss.py` (yeniden yazıldı), `simplify.py`, `build.py`, `triangulate.py` · `tests/`: `test_loss.py`
(yeni), `test_lod.py`, `test_lod_scripts.py`, `test_build.py`, `test_triangulate.py` · `scripts/`: `build_lod.py`,
`check_lod_report.py`, `repro_territorykit_finding.py` · `docs/`: `territorykit-simplification-finding.md`, `PROJE-TALIMATI.md`
