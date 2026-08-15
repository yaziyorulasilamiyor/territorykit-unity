# Faz 2 — LOD üretimi

Tarih: 2026-08-16 · Durum: Tamamlandı (bir mimari sapma + iki inceleme turu)
Dal: feat/phase-2-lod-topology · Commit sayısı: 21

## Ne yapıldı
TerritoryKit CLI build edildi, zincirde **kaldı** (`import geoboundaries`). `simplify.py` topojson ile **paylaşılan
arc** üzerinden sadeleştiriyor (Karar 1); `build.py` `--lod` alıyor; `build_lod.py` zinciri tek komuta indiriyor,
`check_lod_report.py` CI'da denetliyor, `repro_territorykit_finding.py` upstream bulgularını tek komutla üretiyor.

## Nasıl doğrulandı
`ruff` + `mypy` temiz, **186 test geçti**, kapsam **%95**. **Çatlak testi (üçgenleme + float32 SONRASI, üç seviyede):
boşluk tam 0,0 · çakışma tam 0,0** — tolerans değil zorunluluk: paylaşılan arc üzerinde sadeleştirilen sınırın iki
yanı aynı sayılar. Paylaşılan vertex bit-eşitliği ve kapsama (81×50 nokta, hepsi **tam 1** üçgende) üç seviyede
geçti; determinizm byte-identik.

| Seviye | Vertex | high'ın %'si | Üçgen | Bayt | Parça | Delik |
|---|---|---|---|---|---|---|
| kaynak | 366.157 | — | — | — | 705 | 0 |
| high | 240.379 | %100 | 238.969 | 3.359.438 | 705 | 0 |
| medium | 85.926 | %35,7 | 84.518 | 1.197.108 | 704 | 0 |
| low | 30.753 | **%12,8** | 29.383 | 424.914 | 685 | 0 |

## Kararlar ve gerekçeleri
1. **Sadeleştirme TerritoryKit yerine topojson** — talimat `--strategy topology-safe` şart koşuyordu; strateji **önce
   denendi, sonra ölçüldü**: her ring'i bağımsız Douglas-Peucker'dan geçiriyor. Kanıt, 81 geometrisi de geçerli olduğu
   için onarımın suçlanamayacağı `high`: 197 komşu çiftinin **32'sinde** çatlak (0,0061 km² boşluk + 0,0187 km²
   çakışma; kaynakta sıfır, topojson'da sıfır).
2. **`simplify.py` yer tutucu kalmadı** — o madde sadeleştirmeyi TerritoryKit'in yapması varsayımına dayanıyordu; varsayım düşünce modül kendisine ayrılan işi yapıyor.
3. **Parça sayısı seviyeler arasında sabit değil, bilinçli olarak** — `PROJE-TALIMATI.md` FAZ 2 maddesine açık istisna, gerekçe ve bedeliyle yazıldı; muhasebesi aşağıda.

## İnceleme düzeltmeleri
**1. tur** — "48.204 bozuk segment" iddiası geri çekildi (`sharedSegmentCount` farkı; doğru sadeleştirici de yüksek alıyor); 63 km²'lik rakam onarım artefaktından ayrıştırıldı; üst `lossy` tüm aşamaları kapsadı.
**2. tur — tek türetme noktası.** Aynı hata sınıfı üçüncü kez çıktı: `collect_loss` `dropped_parts` listesine değil
`loss.is_lossy` boolean'ına bakıyor, `{"droppedParts":7,"lossy":false}` girdisini kabul ediyordu. Artık **tek** yer
karar veriyor (`geometry_api/loss.py`): bayrak yalnız **kayıt**lardan türetilir, boolean okunmaz, çelişirse sayaç
kazanır. **Tespitçi** iki karşıörnekle çürütüldü, ikisi de kalıcı test — kaynak A+B → çıktı A+C (eşit parça sayısı, B
kayıp, erken dönüş `[]` veriyordu) ve 1,2 milyon m²'lik parçanın tek noktadan teması (`intersects()` "hayatta" diyordu);
hayatta kalma artık **alan örtüşmesiyle** (%10 eşik). Kümülatif `--max-total-lost-area` eklendi: tek parça eşiği 5.000
adet 9.999 m²'lik parçayı geçiriyordu. **Topoloji muhasebesi** ayrıldı — birleşme/bölünme kayıp değil, `topologyChanges`
bloğunda (`low`: **30 birleşme − 11 bölünme = net 19** + yok olan 1 parça = 20) ve özdeşlik testle sabit.
**CI'da geçen üç mutasyon** düşüyor: sayaç var/detay yok, negatif sayaç, `loss.upstream` yokken `lossy:true`.
`normalize_geoboundaries` ve `check_lod_report` ilk kez test edildi (19 test); `test_lod.py` geçersiz decode yüzeyini
onarmıyor, **düşürüyor**.

## Bilinen eksikler ve riskler
- **Çatlak testi eksiksiz değil.** Boşluk metriği yalnız **kapalı** boşlukları görür (dış sınıra açılan çatlak delik
  oluşturmaz); paylaşılan-vertex testi çift başına **en az bir** ortak vertex arar. İl-il iç sınırlar — bozulmanın
  asıl yeri — yakalanıyor; kapatmak vertex dizisi karşılaştırması ister.
- **"Alan kaybı yok" doğru değildi.** Hiçbir parça tamamen yok olmadı (Artvin, 685 m² hariç), ama sınırlar kaydığı için
  `low`'da kaynak alanın **1.236 km²'si kapsanmıyor**; çıktı kaynakta olmayan 1.357 km² kapsıyor (net **+120,6 km²**,
  ülke alanının binde 1,5'i; `high`'da 4,86 km²). 705 parçanın 572'si %5'ten fazla kayıyor, en uçtaki %15,6'ya iniyor —
  ≈210 m toleransı için beklenen. **Birleşmenin bedeli:** su olan boğaz `low`'da kara, tıklama orada ili seçiyor.
- **geoBoundaries dosyaları importer'a olduğu gibi girmiyor.** `shapeGroup` alpha-3 → alpha-2, ve 1e-9 deg² altındaki
  **7 gerçek adacık** (yerel projeksiyonda **2,0–6,1 m²**) düşürülüyor — sayılıyor ve manifeste giriyor, ama
  **veriden çıkıyorlar**.
- `low`'da 19 sahte delik oluşup siliniyor; **hiçbir toleransın hem %25 bütçesini hem sıfır sahte deliği sağlamadığı**
  ölçüldü. Docker doğrulanmadı (Faz 0); yerel Python 3.14, hedef 3.12 → CI'da; topojson **1.10 zorunlu** (1.9
  `np.in1d` çağırıyor).

## Tıkanmalar
**Bir tane, çözüldü.** `topology-safe` en kritik testi geçemiyordu; kendi implementasyonuma **geçmedim**, ölçümleri sunup sordum. Issue A ve B tekrar üretimiyle hazır, **açılmadı**.

## Sonraki faza hazırlık
Faz 3 önkoşulu **hazır**: üç seviye deterministik, ayrı dizinlerde, önceden üretilmiş.

## Değişen dosyalar
`src/geometry_api/`: `loss.py`, `simplify.py` (yeni), `build.py`, `triangulate.py` · `tests/`: `test_lod.py`,
`test_lod_scripts.py` (yeni), `test_build.py`, `conftest.py` · `scripts/`: `build_lod.py`, `check_lod_report.py`,
`repro_territorykit_finding.py` (yeni) · `docs/`: `territorykit-simplification-finding.md`, `PROJE-TALIMATI.md`, `ci.yml`
