# Faz 2 — LOD üretimi

Tarih: 2026-08-17 · Durum: Tamamlandı (bir mimari sapma + beş inceleme turu)
Dal: feat/phase-2-lod-topology · Commit sayısı: 66

## Ne yapıldı
TerritoryKit CLI build edildi, zincirde **kaldı** (`import geoboundaries`); `simplify.py` topojson ile **paylaşılan arc**
üzerinden sadeleştiriyor (Karar 1); `build.py` `--lod` alıyor; `build_lod.py` zinciri tek komuta indiriyor,
`check_lod_report.py` CI'da denetliyor. 4. tur muhasebeyi yapısal değiştirdi (Karar 3); 5. tur `pickingUnsafe`'i
**zincirin tamamına** taşıdı.

## Nasıl doğrulandı
`ruff` + `mypy` temiz, **248 test geçti**, kapsam **%95**, determinizm byte-identik. **Çatlak testi (üçgenleme + float32
SONRASI, üç seviyede): boşluk tam 0,0 · çakışma tam 0,0** — tolerans değil zorunluluk: paylaşılan arc üzerinde
sadeleştirilen sınırın iki yanı aynı sayılar. Paylaşılan vertex bit-eşitliği ve kapsama (81×50 nokta, hepsi **tam 1**
üçgende) üç seviyede geçti.

| Seviye | Vertex | high'ın %'si | Üçgen | Parça | Delik | Korunan alan | En kötü parça | Birleşmenin eklediği |
|---|---|---|---|---|---|---|---|---|
| kaynak | 366.157 | — | — | 705 | 0 | — | — | — |
| high | 240.379 | %100 | 238.969 | 705 | 0 | %99,9994 | %53,9 | 0 km² |
| medium | 85.926 | %35,7 | 84.518 | 704 | 0 | %99,982 | %15,6 | 15,9 km² |
| low | 30.753 | **%12,8** | 29.383 | 685 | 0 | %99,842 | %15,6 | 269,8 km² |

## Kararlar ve gerekçeleri
1. **Sadeleştirme TerritoryKit yerine topojson** — önce denendi, sonra ölçüldü: her ring'i bağımsız Douglas-Peucker'dan
   geçiriyor. Kanıt, onarımın suçlanamayacağı `high`: 197 komşu çiftinin **32'sinde** çatlak (topojson'da sıfır).
2. **Parça sayısı seviyeler arası sabit değil, bilinçli** — bedeli tablodaki son kolon; alternatifi olan toleransı
   düşürmek %25 vertex bütçesini ihlal ediyor.
3. **Muhasebe "bilinen yolları say"dan "denklik kur"a geçti** (4. tur) — `lossy` alan **adının** önekine bakıyordu:
   `removedRings` bayrağı kaldırıyor, aynı şeyi anlatan `collapsedParts` kaldırmıyordu. Yerine kapalı `EVENT_KINDS`,
   bilinmeyen `kind`'de **fail-closed**, seviye başına alan + parça + delik denkliği (tutmazsa build düşer). CI şemayı
   import ediyor ama bayrağı, bütçeyi ve denklikleri **kendisi** hesaplıyor.

## 5. inceleme turu — `pickingUnsafe` neyin güvenliğini söylüyor
**Engelleyici (B1).** Bayrak yalnız `SimplifyResult`'tan türetiliyordu: "sadeleştirici topolojiyi değiştirdi mi"
sorusunun cevabı, "bu mesh'e tıklanabilir mi" cevabı diye yazılıyordu. Sentetik değil **gerçek zincirde** ölçüldü —
düzeltme öncesi `high` manifesti aynı anda `lossy:true` ve `pickingUnsafe:false` idi, CI geçiriyordu. Görünmeyenler:
üçgenleme kayıpları, `hole_merge`/`hole_split`, yalnız `part_split`, ve normalizasyonun attığı her şey.

Artık her olay türü `changes_topology`'yi kendisi beyan ediyor; `pickingUnsafe` = kayıp **veya** topoloji değişikliği,
**üç adımın hepsi** üzerinden. `lossy:true` + `pickingUnsafe:false` *ulaşılamaz* (her kayıp türü güvensiz, import anında
doğrulanıyor) ve CI bu ilişkiyi manifest üzerinde ayrıca denetliyor. Sınır kayması ve onarım
(`artifact_hole_removed`) tetiklemiyor: iddia topolojik aynılık, şekil aynılığı değil — her yerde `true` olan bayrak
bir şey söylemez. Tam tanım `docs/mesh-format.md`'de.

**Sonuç, sessiz kalmasın diye:** gerçek TUR ADM1 zincirinde artık **hiçbir seviye seçim için güvenli değil** — üçü de
`pickingUnsafe:true`. `high`'ın tek sebebi normalizasyonun attığı **7 adacık** (İstanbul 5, Muğla 2; 2,0–6,1 m²); kendi
sadeleştirmesi tertemiz ve bunu `simplification.topologyChanged:false` söylüyor. 4. turun "kanıtlanmış tek güvenli
seviye `high`" cümlesi **daraltıldı**: güvenli seçim, normalizasyonun bu 7 adacığı atmayı bırakmasını gerektiriyor.

## Bilinen eksikler ve riskler
- **Çatlak testi eksiksiz değil.** Boşluk metriği yalnız **kapalı** boşlukları görür, paylaşılan-vertex testi çift başına
  **en az bir** ortak vertex arar; kapatmak vertex dizisi karşılaştırması ister.
- **"Alan kaybı yok" doğru değil.** `low`'da kaynak alanın **1.236 km²'si** kapsanmıyor, çıktı kaynakta olmayan 1.357 km²
  kapsıyor; en kötü parça **%15,6'sında** kalıyor, 40 parça %50 altında (`high`'da 4,86 km²).
- **geoBoundaries dosyaları importer'a olduğu gibi girmiyor:** `shapeGroup` alpha-3 → alpha-2; 1e-9 deg² altındaki 7
  gerçek adacık manifeste `dropped_islet` olarak giriyor ama **veriden çıkıyor** — üç seviyeyi birden `lossy` ve
  `pickingUnsafe` yapan tek sebep bu.
- `low`'da 19 sahte delik oluşup siliniyor (11.617 m², onarım — kayıp değil); hiçbir toleransın hem %25 bütçesini hem
  sıfır sahte deliği sağlamadığı ölçüldü. Docker doğrulanmadı (Faz 0); topojson **1.10 zorunlu**.

## Bilinen eksikler — Faz 3 backlog
5. turda bulundu, **bilinçli düzeltilmedi**: B1 dışındakiler kod değişikliği istemiyor.
1. **Delik büzülmesi görünmüyor.** `_correspondence` (`simplify.py:908`) kesişimi **küçük** geometrinin alanına bölüyor;
   kaynak deliğinin %99'u yok olup %1'i kalsa "%100 eşleşme" — delik sayısı 1→1, üç denklik de geçiyor, `lossy:false`.
   Alan `boundary_advance`'e yazıldığı için sessiz değil, **yapısal** kayıp görünmüyor. Parçalarda bunu
   `retainedAreaRatio`/`severe_shrink` kapatıyor, deliklerde karşılığı yok → `severe_hole_shrink` eklenmeli.
2. **Manifest doğrulaması tam fail-closed değil.** `lossy` yoksa veya `"false"` **metni** ise denetçi kabul ediyor;
   `category`/`side` doğrulanmadan yeniden türetiliyor (`events_from_manifest`, `loss.py:458`); `NaN` alan kabul
   ediliyor, alan bütçesi fail-open kalabiliyor. → zorunlu boolean `lossy`, doğrulanan `category`/`side`, sonlu sayı
   şartı. (5. turda yalnız iki istemci bayrağı için tip denetimi eklendi.)
3. **Bütçe denkliklerinin sınırı, fazla iddia edilmesin.** Denklikler **bağımsız kanıt değil**, aynı muhasebenin
   tutarlılık kontrolü: alan kayıtları ölçümü üreten aynı parça ayrıştırmasından türüyor. Doğrulandı: gerçek bir
   `dropped_part` yerine `part_merge` + `boundary_retreat` kaydedilirse **üç denklik de geçiyor**, `lossy:false` çıkıyor.
   Yakalanan **eksik** kayıt; yanlış **sınıflandırma** yakalanmıyor. (Doğal veride `account_for`'un böyle bir
   sınıflandırma ürettiği bulunamadı — gerçek A+B→A girdisi `dropped_part` + `lossy:true` veriyor.)
4. **%10 eşiği bir politika kararı.** %11'ini koruyan parça hâlâ `change`, `loss` değil. Görünür
   (`retainedAreaRatio` + `severe_shrink`) ama sınıflandırmanın kendisi bilinçli tercih — belgelenmiş olsun.

## Tıkanmalar
**Bir tane, çözüldü.** `topology-safe` en kritik testi geçemiyordu; kendi implementasyonuma **geçmedim**, ölçümleri sunup
sordum. Issue A ve B tekrar üretimiyle hazır, **açılmadı**.

## Sonraki faza hazırlık
Faz 3 önkoşulu **hazır**: üç seviye deterministik, ayrı dizinlerde, önceden üretilmiş. Faz 4-5 için manifest
`topologyChanged` / `pickingUnsafe` taşıyor; tüketen kod o fazlarda yazılacak.

## Değişen dosyalar
`src/geometry_api/`: `loss.py` (yeniden yazıldı), `simplify.py`, `build.py`, `triangulate.py` · `tests/`: `test_loss.py`
(yeni), `test_lod.py`, `test_lod_scripts.py`, `test_build.py`, `test_triangulate.py` · `scripts/`: `build_lod.py`,
`check_lod_report.py`, `repro_territorykit_finding.py` · `docs/`: `mesh-format.md`, `PROJE-TALIMATI.md`,
`territorykit-simplification-finding.md`
