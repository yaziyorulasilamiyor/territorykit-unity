# Faz 3 Planı — HTTP API ve cache (v3 — son, uygulamaya geçiliyor)

Bu, üçüncü ve son plan turu. v1 → v2'de 21 madde (X1-X21), v2 → v3'te 16 madde (Z1-Z16)
düzeltildi. Bundan sonra plan tekrar inceletilmeyecek; uygulama bu belgenin görev sırasına göre
yapılacak. `docs/PROJE-TALIMATI.md` §FAZ 3, `docs/api.md`, `docs/mesh-format.md`, Faz 1-2
raporları, v1 ve v2 temel alındı. Faz raporu satır sınırı (70) Faz 3'ten itibaren yeniden
yürürlükte — bu plan dokümanı o sınıra tabi değil.

## 0. İnceleme maddesi → bölüm haritası (bu tur)

| Madde | Konu | Bölüm |
|---|---|---|
| Z1 | Publisher yanlış girdi üzerinde denetliyordu — `lod-report.json` bütünü + rapor/manifest çapraz kontrolü | §1.1 |
| Z2 | Rename sonrası pointer çökmesi → tekrar deneme pointer'ı tamamlamalı | §3.2 |
| Z3 | `revisionId` staging snapshot'ından hesaplanmalı | §3.1, §3.2 |
| Z4 | Tombstone (`pruned-revisions.json`) atomik yazılmalı | §3.3 |
| Z5 | Aktif istek sırasında budama koruması — lease | §3.4 |
| Z6 | Yayın-sonrası mutasyon: readiness checksum doğrulasın + `build_lod.py` `revisions/` altına yazamasın | §3.5 |
| Z7 | Docker CI çalışma dizini yanlıştı | §15 |
| Z8 | Health/ready/metrics yolu donduruldu | §13.0 |
| Z9 | TKMB TOC sırası donduruldu (sıralı) | §10.2 |
| Z10 | Batch cache dosya yolu revizyona göre isimlendirildi | §10.3 |
| Z11 | Dataset şemasına `boundsLocal` eklendi | §6 |
| Z12 | `levels[]`'e `lossy` eklendi | §6 |
| Z13 | TKMB offset tanımı + taşma davranışı | §10.2 |
| Z14 | Cursor "son taranan" id taşır | §4.2 |
| Z15 | Yanlış-sınıflandırma açığı — devralınan bilinen sınır | §1.3 |
| Z16 | Kesin "bitti sayılır" checklist'i + eksik test senaryoları | §16 |

---

## 1. İstek sırasında sıfır geometri hesabı + Faz 2 garantisinin sınırı

### 1.1 Doğrulama nerede yapılır — DOĞRU girdi üzerinde (Z1)

**v2'nin hatası:** `check_lod_report.py::check()` **tüm `lod-report.json`** üzerinde çalışır —
üç seviye arasında **çapraz** kontroller yapar: sıkı kabalaşma (`high > medium > low` vertex),
territory sayısının seviyeler arası tutarlılığı, normalizasyonun kaybının **her** seviyeye
yayıldığı (`check_lod_report.py:453-462`), `high`'ın kaynağı hiç kaybetmediği
(`check_lod_report.py:341-351`). v2'nin planı publisher'ın **her `index.json`'ı ayrı ayrı**
doğrulayacağını söylüyordu — bu, `check()`'in gerçekte yaptığı işin bir alt kümesi: seviyeler
arası kontroller hiç çalışmaz, üç `index.json` birbiriyle tutarsız da olsa (örn. `medium`
`high`'dan fazla vertex taşısa) publisher bunu fark etmez.

**Doğru sıra — iki ayrı denetim, ikisi de zorunlu:**

1. **Rapor bütünü denetimi (mevcut, değişmeden):** `manifest_validation.check(report)` —
   `check_lod_report.py::check()`'in **birebir taşınmış hâli** — `{build_dir}/lod-report.json`'ın
   tamamına karşı çalışır. Bu fonksiyon zaten üç seviyeyi birlikte görüyor; publisher onu
   **olduğu gibi** çağırır, alt kümesini değil.
2. **Rapor↔manifest çapraz doğrulaması (yeni):** `manifest_validation.check_report_matches_build(
   report, build_dir)` — rapor **kendi içinde** tutarlı olabilir ama gerçekten **bu build_dir**'in
   ürünü olmayabilir (örn. eski bir `lod-report.json` yeni bir `index.json` seti ile aynı dizinde
   kalmış, ya da biri elle bir dosyayı değiştirmiş). Her `lod` için `report["levels"][lod]`
   alanları (`vertices`, `triangles`, `bytes`, `territoryCount`, `lossy`, `topologyChanged`,
   `pickingUnsafe`, `simplification` bloğu — tamamı) ile `{build_dir}/{lod}/index.json`'daki
   karşılık gelen alanlar **birebir** karşılaştırılır. Herhangi bir alan uyuşmazsa isim + iki
   değer verilerek reddedilir.

Her iki denetim de `publish_dataset.py`'de dosyaları `revisions/`'a taşımadan **önce**, staging
oluşturulmadan **önce** çalışır (rapor `build_dir`'in kendisinde zaten var, staging'e ihtiyaç yok)
— **herhangi biri** başarısız olursa publisher sıfırdan farklı çıkış koduyla durur, **hiçbir
dosyaya dokunulmaz** (staging bile oluşturulmaz).

`check_lod_report.py::check()`'in çekirdeği `services/geometry-api/src/geometry_api/
manifest_validation.py`'a taşınır (kopyalanmaz — tek kaynak); `check_lod_report.py` bu modülü
import eden ince bir CLI kalır, davranışı ve mevcut testleri **değişmez**. Faz 1/2'nin tam
`pytest` paketi ve `check_lod_report.py`'nin kendi CI job'ı (`lod-chain`) **aynen** çalışmaya
devam eder.

**Test — tersine çevrildi (v2'den):**
- `test_corrupted_manifest_is_rejected_at_publish` — bir seviyenin `index.json`'ında
  `pickingUnsafe` elle `false` yapılır → **rapor bütünü denetimi** bunu yakalar (`_check_lossy_
  implies_unsafe` zaten `report["levels"][lod]` üzerinden çalışıyor — publisher'a rapor bütünüyle
  verildiği için bu kontrol artık gerçekten çalışıyor).
- `test_report_manifest_mismatch_is_rejected_at_publish` (**yeni**, Z1'in ikinci denetimini
  kanıtlıyor) — `lod-report.json` **kendi içinde tutarlı** bırakılır ama `medium/index.json`'daki
  `totals.vertices` elle değiştirilir (rapordakiyle **artık uyuşmuyor**) → publish reddedilir,
  hata mesajı hangi alanın hangi iki değeri taşıdığını söyler.
- `test_valid_manifest_publishes_unchanged` — v2 ile aynı.

### 1.2 "Sıfır geometri" testinin kendisi — değişmedi

v2'deki tarif (statik AST taraması + temiz subprocess kontrolü, `tests/test_no_geometry_imports.py`
+ `tests/test_no_geometry_at_startup.py`) aynen duruyor.

### 1.3 Devralınan bilinen sınır — yanlış sınıflandırma (Z15)

**Faz 3 bunu çözmüyor, bilinçli olarak.** Faz 2'nin kendi raporu (`FAZ-2-RAPOR.md`, "Bilinen
eksikler — Faz 3 backlog" madde 3) şunu belgeliyor: `check()`'in üç denklik kontrolü (alan, parça,
delik), **kaydedilen olayların kendi içinde tutarlı olduğunu** kanıtlar — olayların **gerçek
geometriyi doğru sınıflandırdığını** kanıtlamaz. Gerçekte düşen bir parça `dropped_part` yerine
`part_merge` + `boundary_retreat` olarak kaydedilirse, üç denklik de tutar, `lossy: false` çıkar,
ve §1.1'in yayınlama-zamanı denetimi bunu **geçirir** — çünkü denetim, `manifest_validation`'ın
(dolayısıyla `build.py`/`simplify.py`'nin) doğru sınıflandırdığını **varsayıyor**, yeniden ölçüp
karşı-kanıtlamıyor (bu geometriyi yeniden hesaplamak demek olurdu — §1'in yasakladığı şey).

Bu, Faz 3'ün getirdiği fail-closed yayınlama denetiminin **sınırını** dürüstçe işaretliyor:
denetim "manifest kendi anlattığı hikayeyle tutarlı mı" sorusuna kesin cevap verir, "hikaye doğru
mu" sorusuna vermez. Doğal TR ADM1 verisinde böyle bir yanlış sınıflandırma üretildiği
**gözlemlenmedi** (Faz 2 raporu) — risk teorik, kapatılmamış, ve rapora böyle yazılacak.

---

## 2. Dizin/veri katmanları

| Ayar | Varsayılan | Kim yazar | Kim okur | İçerik |
|---|---|---|---|---|
| `dataset_dir` (mevcut) | `data/datasets` | `fetch_sample_dataset.py`, geliştirici | yalnız build script'leri | ham GeoJSON/`dataset.json` |
| `artifacts_dir` (**yeni**) | `data/artifacts` | yalnız `scripts/publish_dataset.py` | API süreci (**yalnızca okuma** — bkz. §3.5 Z6b) | bkz. aşağıdaki ağaç |
| `cache_dir` (mevcut, boş) | `data/cache` | API süreci (çalışma zamanı) | API süreci | batch cache + lease dosyaları |

```
data/artifacts/{datasetId}/
  latest-revision.json          # {"revisionId": "...", "publishedAt": "..."} — atomik yazılır
  pruned-revisions.json         # [{"revisionId": "...", "prunedAt": "..."}]  — atomik yazılır (Z4)
  revisions/
    {revisionId}/                # {revisionId} = tam 64 hex karakter, §3.1
      _meta.json                # {"revisionId": ..., "publishedAt": ..., "sourceBuildDir": ...}
      high/  {*.tkms, *.tkms.gz, index.json, etags.json}
      medium/ ...
      low/   ...
  .staging/                     # yalnız yayınlama sırasında var olur; §3.2

data/cache/
  batch/
    {revisionId}/
      {key}.tkmb                # §10.3 — revizyona göre dizinlenmiş, budama trivial
  leases/
    {revisionId}/
      {uuid}.lease               # boş dosya, mtime = oluşturulma zamanı; §3.4
```

`artifacts_dir`, API süreci için **okuma-only**'dir — leases ve batch cache `cache_dir` altında
tutulur, `artifacts_dir`'e API'nin hiçbir yazma erişimi yoktur (§3.5'in "yalnız `publish_dataset.
py` yazar" garantisi bu ayrımla tutarlı kalıyor).

---

## 3. Revizyon modeli

### 3.1 `revisionId` hesaplama — staging'den, çakışmasız, tam SHA-256 (Z3, Z18-v2)

```python
def compute_revision_id(root: Path) -> str:
    """root: high/medium/low alt dizinlerini içeren bir dizin — build_dir DEĞİL, staging."""
    hasher = hashlib.sha256()
    for lod in ("high", "medium", "low"):
        for path in sorted((root / lod).rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix().encode("utf-8")
            content = path.read_bytes()
            hasher.update(len(rel).to_bytes(4, "big"))
            hasher.update(rel)
            hasher.update(len(content).to_bytes(8, "big"))
            hasher.update(hashlib.sha256(content).digest())
    return hasher.hexdigest()  # tam 64 hex karakter
```

**Neden `build_dir` değil `staging`'den (Z3):** v2, `revisionId`'yi `build_dir`'den hesaplayıp
*sonra* aynı `build_dir`'den staging'e kopyalıyordu — hesaplama ile kopyalama arasında bir
pencere vardı; bu pencerede `build_dir` değişirse (biri üstüne yeni bir build yazarsa) dizin adı
(`revisionId`) ile yayımlanan baytlar ayrışabilirdi. Düzeltme: **önce** kopyala (staging'e,
`.tkms.gz`/`etags.json` üretimi dahil — bunlar da hash'e girer, çünkü onlar da yayımlanan baytın
parçası), **sonra** staging'in kendisinden hash'i hesapla. Hash artık her zaman "yayımlanacak tam
olan bu" der, "kaynak şu anda böyleydi" değil.

Bu fonksiyon iki yerde kullanılır: (1) yayınlama sırasında `revisionId`'yi üretmek için (§3.2),
(2) registry'nin bütünlük kontrolü için — bir revizyonu ilk kez çözerken `revisions/{revisionId}/`
üzerinde tekrar çalıştırılır, sonuç dizin adıyla eşleşmiyorsa yayın-sonrası bozulma tespit edilir
(§3.5, Z6a). Aynı fonksiyon iki amaca hizmet ediyor — ayrı bir checksum şeması icat edilmedi.

### 3.2 Atomik yayınlama sırası — pointer'ı tamamlayan tekrar deneme (Z2, Z3)

```
scripts/publish_dataset.py --build-dir <build_lod çıktısı, lod-report.json içerir>
                            --dataset-id <id> --artifacts-dir data/artifacts
                            [--keep N] [--published-at ISO8601]

1. report = build_dir/lod-report.json oku (yoksa hata, dur)
2. manifest_validation.check(report) çalıştır                              [§1.1, adım 1]
   manifest_validation.check_report_matches_build(report, build_dir) çalıştır [§1.1, adım 2]
   → herhangi bir hata: dur, HİÇBİR ŞEY YAZILMADI.
3. staging = artifacts_dir/{id}/.staging/{uuid4().hex}     (revisionId HENÜZ yok — Z3)
4. try:
     staging'i oluştur
     her lod için: build_dir/{lod}'daki dosyaları staging/{lod}/'a kopyala,
                   .tkms.gz üret, etags.json yaz, _meta.json'a sourceBuildDir yaz
     _verify(build_dir, staging):
       - staging'deki .tkms/index.json kümesi == build_dir'daki kümesi
       - her dosya: uzunluk eşit VE sha256(kaynak) == sha256(hedef)
     revisionId = compute_revision_id(staging)                              [§3.1]
     final = artifacts_dir/{id}/revisions/{revisionId}
     if final.exists():
       # Z2: önceki bir koşu rename'i bitirip pointer'da çökmüş OLABİLİR — sessizce çıkma.
       if compute_revision_id(final) != revisionId:
         raise PublishError("existing revisions/{revisionId} does not hash to its own name — "
                             "corrupted after publish, refusing to overwrite or point to it")
       shutil.rmtree(staging)   # final zaten doğru ve doğrulanmış, staging gereksiz
     else:
       os.rename(staging, final)   # aynı dosya sistemi → atomik
   except Exception:
     shutil.rmtree(staging, ignore_errors=True)
     raise   # latest-revision.json'a DOKUNULMADI, final'e (varsa) DOKUNULMADI
5. (yalnız final artık var VE doğrulanmışsa — ister bu koşu ister önceki bir koşu yayınlamış
   olsun) latest-revision.json'ı atomik güncelle (geçici dosya + os.replace)
6. _prune_old_revisions(...)                                                [§3.3]
```

Adım 4'ün `final.exists()` dalı, hem "bu build zaten yayınlanmış, tekrar iş yapma" (idempotency,
v2'den) hem "rename bitti ama pointer yazımı çöktü, bu koşu onu tamamlasın" (Z2) davranışını
**tek bir kod yoluyla** karşılıyor — ikisi de "final zaten doğru" durumundan adım 5'e devam
etmek. Fark yalnız *neden* oraya varıldığında, davranışta değil.

**Hata enjeksiyon testleri** (v2'den, isimler netleşti):
- `test_interrupted_during_copy_leaves_no_trace` — kopyalama sırasında kesinti → `.staging`
  temiz, `revisions/` altında yeni dizin yok, pointer değişmedi.
- `test_checksum_mutation_during_verify_is_rejected` — `_verify` adımında staging içinde bir
  dosya bilerek değiştirilir → publish reddedilir, aynı üç iddia.
- `test_retry_after_pointer_write_crash_completes_pointer` (**yeni, Z2**) — `final`'i elle
  oluşturup (gerçek bir önceki başarılı `rename`'i simüle ederek) `latest-revision.json`'ı
  **yazmadan** script'i tekrar çalıştır → ikinci koşu `final`'i doğrular, kopyalamadan geçer,
  **pointer'ı tamamlar**; `revisions/` altında **ikinci bir dizin oluşmaz**.

### 3.3 Saklama, tombstone, 404 vs 410 (dataset başına; Z4)

v2'deki kapsam/öncelik/doğrulama kuralları aynen duruyor (dataset başına, `--keep`/
`revision_retain_count` önceliği, `N≥1`). **Tombstone yazımı artık atomik (Z4):**
`pruned-revisions.json` güncellemesi *oku → listeye ekle → geçici dosyaya yaz → `os.replace`*
sırasıyla yapılır — `latest-revision.json` ile aynı desen. Süreç bu adımın ortasında kesilirse
dosya ya eski (bozulmamış) hâlinde kalır ya da yeni (tam, tutarlı) hâline geçer; asla yarım JSON
olarak kalmaz.

**Budama sırası (lease kontrolü eklendi, bkz. §3.4):** her budama adayı için önce §3.4'ün lease
kontrolü yapılır; aktif lease varsa o revizyon **bu koşuda atlanır** (silinmez, bir sonraki
`publish_dataset.py` çağrısında tekrar denenir) — bu, `keep` sayısının **geçici olarak** aşılmasına
izin verir (aktif bir istek bitene kadar), disk kullanımını değil doğruluğu önceliklendirir.

**Cache temizliği (Z10 ile birlikte, §10.3):** bir revizyon fiilen silindiğinde
`cache_dir/batch/{revisionId}/` ve `cache_dir/leases/{revisionId}/` de `shutil.rmtree` ile
silinir — artık bir dizin olduğu için (v2'deki "dosya adı revisionId içerir" varsayımı yanlıştı)
bu tek bir çağrı, dosya adı deseni aramaya gerek yok.

### 3.4 Aktif istek koruması — lease (Z5)

**Sorun:** bir istek `revisionId=X`'i çözdükten sonra, eşzamanlı bir `publish_dataset.py` çağrısı
X'i budayabilir — istek dosyaları okumaya devam ederken dizin siliniyor olabilir. API ve
`publish_dataset.py` **ayrı süreçler** olduğundan bellek-içi bir referans sayacı yeterli değil;
korumanın disk üzerinde, süreçler arası görünür olması gerekiyor.

**Çözüm — dosya tabanlı lease, `cache_dir` altında (artifacts_dir'e API yazmıyor, §2):**
- `resolve_revision` FastAPI bağımlılığı (§3.5, `?revision=` çözümlemesinin de yapıldığı tek
  nokta), revizyonu çözdükten **hemen sonra** `cache_dir/leases/{revisionId}/{uuid4().hex}.lease`
  adında boş bir dosya oluşturur (`yield` öncesi), yanıt tamamlandığında (`finally`, `yield`
  sonrası) siler. Yanıt gövdeleri gerçek HTTP akışı sırasında değil, diskten **tam okunduktan
  sonra** döndürülür (mesh/batch boyutları bunu bellekte tutmaya elverişli) — bu yüzden lease'in
  kapsadığı pencere disk-okuma penceresini **tam** kapsıyor, ayrı bir senkronizasyona gerek yok.
- `publish_dataset.py`'nin budama adımı, bir revizyonu silmeden **önce**
  `cache_dir/leases/{revisionId}/` içeriğine bakar: boşsa sil; dolu ama içindeki `.lease`
  dosyalarının **hepsi** `mtime`'ı 5 dakikadan eskiyse (çökmüş bir isteğin bıraktığı ölü lease —
  hiçbir gerçek istek bu kadar sürmez) yine de sil; en az bir **taze** (< 5 dk) lease varsa **bu
  koşuda atla**.
- **Test:** `tests/test_publish_atomicity.py::test_active_lease_defers_pruning` — bir lease
  dosyasını elle oluşturup `publish_dataset.py --keep 1`'i tetikle → budanması gereken revizyon
  **hâlâ diskte**, `pruned-revisions.json`'a **eklenmedi**. Lease dosyasını sil, tekrar çalıştır →
  şimdi budanıyor.

### 3.5 Yayın-sonrası mutasyonun yakalanması + yazma yolu koruması (Z6)

**a) Readiness'in checksum doğrulaması.** `DatasetRegistry`, bir dataset'in **güncel** (`latest-
revision.json`'ın işaret ettiği) revizyonunu her (yeniden) yüklediğinde — süreç başlangıcında ve
`latest-revision.json`'ın `mtime`'ı değiştiği her seferinde, yani **istek sırasında değil, nadir
bir yükleme anında** — `compute_revision_id(artifacts_dir/{id}/revisions/{revisionId})`'i tekrar
çalıştırır ve dizinin adıyla eşleştiğini doğrular. Uyuşmazsa o dataset `not_ready` işaretlenir
(`GET /ready` → `503`, `reason: "current revision content does not match its own revisionId —
corrupted after publish"`), süreç bunu bir daha denemez (bir sonraki başarılı yayın yeni bir
`revisionId` ile gelene kadar). **Pinlenmiş (`?revision=`) ama güncel olmayan** revizyonlar için
aynı kontrol **tembel** çalışır — bir revizyon ilk kez `?revision=` ile istendiğinde bir kez
doğrulanır, sonucu süreç ömrü boyunca bellekte önbelleklenir (aynı revizyon iki kez hash'lenmez).
Bu, `/ready`'nin "istek sırasında ağır iş yok" ilkesini korurken (doğrulama nadir/tembel) yayın-
sonrası bozulmayı gerçekten yakalar (v2'de hiç yakalanmıyordu).

- **Test:** `tests/routes/test_ops.py::test_ready_detects_post_publish_corruption` — geçerli bir
  yayın sonrası `revisions/{revisionId}/high/index.json`'ı elle değiştir (dosya adı hâlâ
  `revisionId`'yi taşıyor ama içerik artık ona hash'lenmiyor), süreci (yeniden) başlat veya
  `latest-revision.json`'ı dokunmadan-yeniden-yaz (mtime tetikler) → `/ready` `503` döner.

**b) `build_lod.py`'ın `revisions/` altına doğrudan yazmasını engelleme.** `build.py::write_build()`
(Faz 1/2, `build_lod.py` tarafından da çağrılıyor), yazmadan önce basit bir sözdizimsel kural
denetler: `output_dir`'in **hiçbir** yol bileşeni tam olarak `"revisions"` **olamaz**. Bu,
`artifacts_dir`'in gerçek yapılandırılmış değerini bilmeye gerek duymayan, saf bir isim kuralı —
`revisions/` her zaman yalnız `publish_dataset.py`'nin atomik `rename` hedefidir, hiçbir build
komutunun doğrudan hedefi olamaz. İhlalde `BuildError` (dolayısıyla `build_lod.py`'de
`BuildLodError`), açık mesajla: *"refusing to write into a path containing a 'revisions'
component — that directory is reserved for scripts/publish_dataset.py's atomic publish; build
into a plain directory and publish it"*.

Bu, `build.py`'ye eklenen **tek** yeni davranış — mevcut hiçbir çağrı yolu (`--output` hiçbir
zaman `revisions` bileşeni taşımıyor) etkilenmez, Faz 1/2'nin testleri değişmeden geçer.

- **Test:** `tests/test_build.py::test_write_build_refuses_output_under_a_revisions_directory`.

---

## 4. `territories` — tam şema, cursor, filtreler

(v2'deki §4 ile aynı — yalnız §4.2'nin cursor semantiği netleşti.)

### 4.2 Cursor: "son taranan" id, "son dönen" değil (Z14)

Cursor'ın `lastId` alanı, **dönen** son öğenin değil, **taranan** son öğenin id'sidir — filtreye
uymayıp **atlanan** öğeler de tarama pozisyonunu ilerletir. Fark, `scanTruncated: true` ile biten
düşük-seçicilikli bir sayfada görünür: sayfa 0 öğe dönse bile (`limit` dolmadan
`territories_scan_cap`'e ulaşıldıysa), `lastId` taramanın gerçekten nereye vardığını gösterir —
"dönen son öğe" tanımıyla bu alan **değişmeden** kalırdı (hiç öğe dönmediyse), bir sonraki istek
**aynı taranmış-ve-reddedilmiş aralığı** tekrar tarardı. "Taranan son öğe" tanımıyla ilerleme her
zaman garanti — filtre ne kadar seçici olursa olsun sayfalama sonunda biter.

Geri kalan her şey (şema, filtreler, `cursor_filter_mismatch`, `O(log n)+O(n)` karmaşıklık notu,
`scanTruncated`, `territories_scan_cap=5000`) v2 ile **aynı**.

---

## 5. `viewport` — değişmedi

v2 ile aynı.

---

## 6. `datasets`, `datasets/{id}` — `boundsLocal` ve `lossy` eklendi (Z11, Z12)

`GET /v1/datasets/{id}?revision=`:
```json
{
  "id": "tr-adm1",
  "name": "TR ADM1 il sınırları",
  "sourceFormat": "territorykit",
  "revisionId": "<64-hex>",
  "isCurrentRevision": true,
  "publishedAt": "2026-08-17T10:00:00Z",
  "origin": {"lon": 35.24, "lat": 39.06, "projection": "webmercator-local-meters", "scale": 1.0},
  "boundsWgs84": [25.66, 35.81, 44.83, 42.11],
  "boundsLocal": [-870000.0, -690000.0, 870000.0, 690000.0],
  "territoryCount": 81,
  "levels": [
    {
      "lod": "high", "territoryCount": 81, "vertexCount": 240379, "triangleCount": 238969,
      "byteLength": 3312482, "lossy": true, "topologyChanged": true, "pickingUnsafe": true,
      "simplification": {"topologyChanged": false}
    },
    {"lod": "medium", "...": "..."},
    {"lod": "low", "...": "..."}
  ]
}
```

- **`boundsLocal` (Z11):** `docs/api.md`'nin "tüm bbox'lar yerel metre" kuralına uymak için
  eklendi — `high` seviyesinin `index.json`'ındaki `boundsLocal`'ı yansıtır (en az sadeleştirilmiş,
  kaynağa en yakın referans olduğu için dataset-düzeyi özet olarak seçildi; her seviyenin **kendi**
  `boundsLocal`'ı zaten `territories` uç noktasındaki `bboxLocal` alanları ve gerekirse
  `levels[].boundsLocal` ile — bu fazda eklenmedi, gerek görülmedi — elde edilebilir).
  `boundsWgs84` seviyeden bağımsızdır (kaynak geometriden, projeksiyon öncesi).
- **`levels[].lossy` (Z12):** üçüncü istemci bayrağı eksikti — `topologyChanged`/`pickingUnsafe`
  yanında artık `lossy` de var, doğrudan `index.json`'ın kendi `lossy` alanından, değiştirilmeden.

---

## 7. Hata sözleşmesi — v2 ile aynı, `revision_corrupted` eklendi

Kod kümesine bir tane daha: `revision_corrupted` (500 — §3.5a'nın bütünlük kontrolü başarısız
olursa; istemci hatası değil, sunucu tarafı veri bozulması).

---

## 8. Seviye metadata'sı — v2 ile aynı

---

## 9. Tekil mesh — v2 ile aynı (LOD query param, donduruldu), sıraya lease eklendi

§3.4'ün `resolve_revision` bağımlılığı artık lease de üstleniyor; §9.2'nin sırası: (1) revizyon
çözümü + lease edinimi, (2) territory var mı, (3) `Accept-Encoding`/`If-None-Match`, (4) yanıt
tamamlanınca lease serbest bırakılır (`finally`).

---

## 10. TKMB v1 — TOC sıralaması ve offset tanımı netleşti (Z9, Z13); cache yolu düzeltildi (Z10)

### 10.1 `Accept-Encoding` ayrımı — v2 ile aynı (`entryEncoding`, ayrı kavram)

### 10.2 Binary layout — TOC daima sıralı, offset ve taşma tanımlı

```
Header — 16 byte, little-endian:
  0   char[4]   magic = "TKMB"
  4   uint16    version = 1
  6   uint16    flags        (bit0: 1 ise entryEncoding=gzip, 0 ise identity)
  8   uint32    foundCount
  12  uint32    missingCount

TOC — foundCount kayıt, **territoryId'ye göre sözlüksel artan sırada** (Z9):
  uint16 idLength, char[idLength] territoryId (UTF-8), uint32 offset, uint32 length

Missing — missingCount kayıt, **territoryId'ye göre sözlüksel artan sırada**:
  uint16 idLength, char[idLength] territoryId

Payload:
  TOC sırasıyla (yani id'ye göre artan), her territory'nin tam TKMS baytları
```

- **TOC sıralaması donduruldu (Z9):** istek gövdesindeki `territoryIds` hangi sırada gelirse
  gelsin, TOC (ve payload'ın kendi sırası) **her zaman** id'ye göre artan. Bu, cache anahtarının
  zaten sırayı yok saymasıyla (§10.3, `sorted(set(...))`) tutarlı: `[A,B]` ve `[B,A]` istekleri
  aynı cache anahtarına düşer **ve** aynı baytı üretir — v2'de bu ikisi arasında bir tutarsızlık
  vardı (aynı anahtar, TOC sırası isteğe göre değişebiliyordu). İstemci sırasını korumuyoruz;
  `docs/mesh-format.md`'ye bu açıkça yazılacak.
- **`offset` tanımı (Z13):** payload **alt-bölümünün** başlangıcından itibaren, yani header + TOC
  + missing bölümünden **sonraki ilk baytı 0 kabul eden**, dosyanın mutlak başlangıcına göre
  **değil**. (`decode_tkmb`, header+TOC+missing'in toplam uzunluğunu hesaplayıp `offset`'e ekleyerek
  dosya içindeki gerçek konumu bulur.)
- **Taşma davranışı (Z13):** `offset`/`length` `uint32` (`< 2**32`). Bir batch'in toplam payload
  boyutu bunu aşacaksa (pratikte `batch_max_territories=200` ile bugünkü mesh boyutlarında asla
  olmaz, ama genel sözleşme olarak tanımlanmalı), sunucu TOC'u yazmadan **önce** toplam boyutu
  hesaplar; `2**32 - 1`'i aşıyorsa istek `400 batch_too_large` ile reddedilir (mesajda hangi
  sınırın — sayı mı bayt mı — aşıldığı belirtilir). Asla sessizce sarmalanmaz/kesilmez.
- Yinelenen id davranışı (tekilleştirme) — v2 ile aynı.

### 10.3 Cache yolu ve budama — revizyona göre dizinlendi (Z10)

**v2'nin hatası:** cache dosya adı yalnız `sha256(...)` idi; budama "adı `revisionId` içeren
dosyaları sil" diyordu ama böyle bir ad **yoktu** — dosya adı `revisionId`'yi hiç taşımıyordu.

**Düzeltme:** cache yolu `cache_dir/batch/{revisionId}/{key}.tkmb` (§2'nin ağacı) — `revisionId`
artık dizin adı olarak **gerçekten** var, hash formülünden çıkarıldı (dizin zaten onu kodluyor,
tekrar etmeye gerek yok):

```
key = sha256(datasetId + "/" + lod + "/" + entryEncoding + "/"
             + ",".join(sorted(set(requestedTerritoryIds))))
```

Budama artık trivial: `shutil.rmtree(cache_dir / "batch" / revisionId, ignore_errors=True)` —
dosya adı deseni aramaya gerek yok (§3.3).

Revizyon-önce kontrolü (v2'nin X15 düzeltmesi — önce §3.4'ün `resolve_revision`'ı, sonra cache) ve
eşzamanlılık/atomik-rename davranışı **değişmedi**.

---

## 11. ETag, `Vary`, `Cache-Control`, 304 — v2 ile aynı

---

## 12. Önceden üretilmiş gzip varyantı — v2 ile aynı

---

## 13. CORS, sınırlar, health/ready/metrics yolu, metrikler

### 13.0 Yol donduruldu: `/health`, `/ready`, `/metrics` — versiyonsuz (Z8)

**Karar:** altyapı/ops uç noktaları (`GET /health`, `GET /ready`, `GET /metrics`) **kasıtlı
olarak `/v1/` dışında**, kökte kalır; kaynak API'sinin tamamı (`/v1/datasets`, `/v1/datasets/{id}
/territories`, `.../mesh/...`, `.../mesh/batch`) `/v1/` altındadır. Gerekçe: (1) `GET /health`
zaten Faz 0'da uygulanmış, test edilmiş, `docs/api.md`'ye yazılmış bir sözleşme — kökte kalması
onu **hiç değiştirmiyor**. (2) health/readiness/metrics probe'ları endüstri genelinde (Kubernetes
`livez`/`readyz`, Prometheus `/metrics`) versiyonlama dışında tutulan altyapı uç noktalarıdır —
kaynak temsilinin bir sürümü değiller. `/ready` ve `/metrics` bu fazda yeni ama aynı kuralı takip
ediyor, tutarlılık için. v2'nin "hem `/v1/health` hem `/health`" ikili takma-ad tasarımı ve Docker
CI adımının `curl .../v1/health` çağırması **düzeltildi** — tek yol, `/health`.

`docs/api.md`'ye bu kural açıkça not düşülecek.

### 13.1 Readiness — v2 ile aynı + Z6a'nın checksum kontrolü (§3.5'e taşındı)

### 13.2 CORS ve sınırlar — v2 ile aynı

### 13.3 Metrikler — v2 ile aynı

---

## 14. HEAD var, Range yok — v2 ile aynı

---

## 15. Docker — CI job'ı ayrı, çalışma dizini düzeltildi (Z7); health yolu düzeltildi (Z8)

**v2'nin hatası:** `geometry-api` job'ının `defaults.run.working-directory:
services/geometry-api` olduğu (`.github/workflows/ci.yml:11-13`) hesaba katılmadan `docker build
-t geometry-api:ci ./services/geometry-api` o job'a eklenseydi, gerçek yol
`services/geometry-api/services/geometry-api` olurdu — var olmayan bir dizin.

**Düzeltme:** Docker adımları **ayrı bir job** olarak eklenir (mevcut `lod-chain` job'ıyla aynı
desen — repo kökünde çalışır, `defaults.run.working-directory` **yok**):

```yaml
  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build image
        run: docker build -t geometry-api:ci ./services/geometry-api
      - name: Run container
        run: docker run -d --name geometry-api-ci -p 8000:8000 geometry-api:ci
      - name: Wait for health (max 60s)
        run: |
          for i in $(seq 1 30); do
            if curl -sf http://localhost:8000/health; then exit 0; fi
            sleep 2
          done
          echo "container did not become healthy within 60s" >&2
          exit 1
      - name: Dump container logs on failure
        if: failure()
        run: docker logs geometry-api-ci
      - name: Cleanup
        if: always()
        run: docker rm -f geometry-api-ci || true
```

(`curl .../health`, `/v1/health` değil — §13.0.) Geri kalan her şey (karar (b), yerel doğrulama
komutları, `PROJE-TALIMATI.md`'nin güncellenmesi) v2 ile aynı.

---

## 16. Bitiş kriterleri — kesin checklist (Z16)

v2'nin "v1 ile aynı" referansları burada **tam** olarak yazıldı; artık başka bir belgeye bakmaya
gerek yok.

### 16.1 Komut listesi — hepsi geçmeli, atlanan (skip) sayısı **sıfır**

```bash
cd services/geometry-api
ruff check . ../../scripts
ruff format --check . ../../scripts
mypy src/
GEOMETRY_API_REQUIRE_SAMPLE_DATASET=1 pytest -q --cov=geometry_api   # 0 skipped
python scripts/check_lod_report.py <lod-report.json>                 # lod-chain job, değişmedi
python scripts/publish_dataset.py --build-dir <build_lod çıktısı> \
  --dataset-id tr-adm1 --artifacts-dir data/artifacts
uvicorn geometry_api.main:app &
curl -sf localhost:8000/health
curl -sf localhost:8000/ready
python scripts/bench_api.py --base-url http://localhost:8000 --requests 500 \
  --output docs/phases/bench-results.json
test -s docs/phases/bench-results.json   # dosya var VE boş değil
```

Yeni testlerin hiçbiri `pytest.mark.skip` kullanmaz — Faz 1/2'nin "CI'da skip = başarısızlık"
disiplini (`GEOMETRY_API_REQUIRE_SAMPLE_DATASET=1`) aynen sürdürülür, yeni test dosyaları da bu
ortam değişkeninin etkisi altında **hep** çalışır durumda tasarlanır.

### 16.2 Tam test envanteri — v2'nin tablosu + Z16'nın eklediği dört senaryo

v2'nin §16.1 tablosundaki her satır aynen geçerli; ayrıca:

| Senaryo | Test | Neden ayrı |
|---|---|---|
| Yayın-sonrası mutasyon | `test_ready_detects_post_publish_corruption` (§3.5a) | v2'de hiç yoktu — Z6 |
| Rename-sonrası kesinti (pointer çökmesi) | `test_retry_after_pointer_write_crash_completes_pointer` (§3.2) | kopyalama-sırası kesintiden ayrı bir zaman penceresi — Z2 |
| Budama/istek yarışı | `test_active_lease_defers_pruning` (§3.4) | v2'de hiç yoktu — Z5 |
| Batch sıra permütasyonu | `test_batch_request_order_does_not_change_cache_key_or_toc_order` — `[A,B]` ve `[B,A]` isteklerinin **aynı** cache dosyasına düştüğünü VE ikisinin de id-sıralı TOC döndürdüğünü doğrular | v2'de tutarsızdı — Z9 |

### 16.3 "Bitti sayılır" — nihai

- §16.1'deki komut listesi baştan sona **sıfır hata, sıfır skip** ile tamamlanıyor.
- §16.2'nin tam test tablosu (v2 + 4 yeni senaryo) yeşil.
- `docs/mesh-format.md`'nin TKMB bölümü dolu (offset/sıralama/taşma tanımlarıyla), `docs/api.md`
  gerçek `/v1` + `/health`+`/ready`+`/metrics` yüzeyini yansıtıyor.
- `docs/phases/bench-results.json` var, boş değil, `FAZ-3-RAPOR.md`'de en az cache-hit p50/p95
  sayısı alıntılanmış.
- §1.3'ün devralınan sınırı (Z15) rapora "bilinen eksikler" olarak yazılmış.
- `docs/PROJE-TALIMATI.md` §FAZ 3 "Bitti sayılır" satırı güncellenmiş (Docker kararı b).

---

## 17. Kod/dosya envanteri — güncellendi

**Yeni:** `src/geometry_api/manifest_validation.py` (check_lod_report.py'nin **hem** rapor-bütünü
**hem** rapor↔manifest çapraz kontrolünü taşıyan modül), `src/geometry_api/registry.py` (revizyon
çözümleme + lease + bütünlük önbelleği), `src/geometry_api/routes/*.py`, `src/geometry_api/
tkmb.py`, `src/geometry_api/errors.py`, `src/geometry_api/pagination.py`, `src/geometry_api/
metrics.py`, `scripts/publish_dataset.py`, `scripts/bench_api.py`, `tests/test_publish_
validation.py`, `tests/test_publish_atomicity.py`, `tests/test_no_geometry_imports.py`, `tests/
test_no_geometry_at_startup.py`, `tests/test_revision.py`, `tests/test_error_contract.py`,
`tests/routes/*.py`.

**Değişecek:** `scripts/check_lod_report.py` (çekirdek taşındı, ince CLI kaldı), `main.py`
(`/v1` mount + `/health`/`/ready`/`/metrics` kökte + lifespan + CORS + exception handler'lar),
`config.py` (yeni `Settings` alanları), `cache.py`, `build.py` (**iki** değişiklik: `write_build`
`revisions/` yol koruması — §3.5b — ve `administrativeLevel` manifest alanı — v2 §4), `docs/api.md`,
`docs/mesh-format.md`, `docs/PROJE-TALIMATI.md`, `.github/workflows/ci.yml` (yeni `docker` job'ı).

**Dokunulmayacak:** `loader.py`, `projection.py`, `triangulate.py`, `encoding.py`, `simplify.py`,
`loss.py`, `scripts/build_lod.py` (kendisi değil — yalnız çağırdığı `build.py::write_build`
değişiyor, `build_lod.py`'nin kendi kodu değişmiyor), `scripts/fetch_sample_dataset.py`.

---

## Kararlar — tamamı donduruldu

v2'nin dondurduğu 5 madde + bu turda dondurulanlar:

6. **Publisher iki denetim çalıştırır:** rapor bütünü (`check()`, değişmeden) + rapor↔manifest
   çapraz kontrolü (yeni) — §1.1.
7. **`revisionId` staging'den hesaplanır**, `build_dir`'den değil — §3.1.
8. **Lease mekanizması `cache_dir` altında**, `artifacts_dir` API için katı okuma-only kalıyor —
   §3.4.
9. **`/health`, `/ready`, `/metrics` versiyonsuz, kökte** — §13.0.
10. **TKMB TOC'u her zaman id-sıralı**, istek sırası yok sayılır — §10.2.
11. **Batch cache `cache_dir/batch/{revisionId}/{key}.tkmb`** — §10.3.
12. **Yanlış-sınıflandırma açığı bilinçli olarak devralınıyor**, Faz 3 kapsamı dışı — §1.3.

Açık madde kalmadı. **Bundan sonra uygulamaya geçiliyor; plan tekrar inceletilmeyecek.**
