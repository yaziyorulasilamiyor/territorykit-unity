# Faz 3 — HTTP API ve cache

Tarih: 2026-08-18 · Durum: Tamamlandı (bir inceleme turu dahil)
Dal: feat/phase-3-http-api · Commit sayısı: 21

## Ne yapıldı
- `scripts/publish_dataset.py`: build çıktısını **önce staging'e kopyalar**, dört bağımsız
  kontrolü (rapor bütünü, rapor↔manifest, manifest↔mesh dosya/uzunluk/sha256, kopya bütünlüğü)
  yalnız staging üzerinde çalıştırıp öyle 64-hex içerik-adresli bir revizyona yayınlar
- `/v1/datasets`(+`{id}`), `.../territories`, `.../viewport`, `.../revisions/{revisionId}/
  mesh/{territoryId}` (GET/HEAD, gzip, ETag, 304), `.../mesh/batch` (TKMB v1);
  `/health`+`/ready`+`/metrics` kasıtlı olarak `/v1` dışında
- `manifest_validation.py`: `check_lod_report.py`'nin denetleyicisi paylaşılan modül
- `registry.py`: revizyon çözümleme, yayın-sonrası bütünlük denetimi ve ayrı publisher sürecinin
  görebildiği `cache_dir/leases` altında dosya-tabanlı lease
- Sıfır istek-zamanı geometri hesabı artık **testle kanıtlı**: statik AST taraması + temiz
  subprocess'te gerçek istekler sonrası `sys.modules` denetimi
- CI'a ayrı `docker` job'ı: build + run + `/health` poll + log dökümü + cleanup

## İnceleme turu — üç düzeltme
1. **Doğrulanan veri ile yayınlanan aynı değildi.** Denetimler staging'den *önce*, değişebilir
   build dizininde çalışıyordu; `check_report_matches_build()` de yalnız özet alanları
   karşılaştırıp `territories[].file`'ın gerçek mesh'e karşılığını hiç açmıyordu. Sıra tersine
   çevrildi (kopyala → doğrula), yeni kontrol eklendi: her `.tkms` var mı, uzunluğu ve **sha256'sı**
   (yeni manifest alanı) eşleşiyor mu. Test: silinen/başka bölgenin dosyasıyla değişen `.tkms` → ret.
2. **Zorunlu iki test hiç yazılmamıştı** — `test_no_geometry_imports.py` (AST) ve
   `test_no_geometry_at_startup.py` (temiz subprocess) artık var.
3. **`Accept-Encoding: gzip;q=0`** substring kontrolüyle "kabul" sayılıyordu (RFC 9110 §12.5.3'e
   göre "reddedildi" demek) — `conditional.accepts_gzip()` kalite değerini ayrıştırıyor.

## Nasıl doğrulandı
| Kontrol | Komut | Sonuç |
|---|---|---|
| Lint/tip/test | `ruff`+`mypy`+`pytest -q --cov` | Temiz · **380 geçti, 0 atlandı**, kapsam **%95** |
| lod-chain (değişmeden) | `check_lod_report.py` | 39 test aynen geçiyor |
| Bench (127.0.0.1) | `bench_api.py --requests 30` | mesh cache-hit p50 **14 ms**, p95 **25 ms** |

## Kararlar ve gerekçeleri
1. **Docker: native uvicorn + CI'da gerçek konteyner health-check'i** — Docker Desktop Faz 0'dan
   beri bu makinede çalışmıyor; bitiş kriterini buna bağlamak üçüncü fazı da tıkardı.

## Bilinen eksikler ve riskler
- **Yanlış-sınıflandırma açığı** (Faz 2'den devralındı, FAZ-2-RAPOR.md §75): denetim kaydedilen
  olayların kendi içinde tutarlı olduğunu kanıtlar, doğru sınıflandırıldığını kanıtlamaz.
- **Lease budaması gerçek bir yarış içeriyor**: `deps.py` önce revizyonu çözer, sonra lease
  oluşturur; `publish_dataset.py` "lease yok" dedikten sonra bu aralıkta bir istek lease
  oluşturursa, publisher dizini ve lease'i siler, istek 500'e düşer. Mevcut test lease'i budamadan
  **önce** hazırlıyor, bu dar aralığı ölçmüyor. Ortak kilit protokolü ve gerçek eşzamanlı lease
  testi **Faz 4 backlog'una**.
- **Elle `cp` ile `revisions/` altına yazma engellenmiyor** — yalnız `build_lod.py --output`
  yolu (build.py'nin `revisions` bileşen kontrolü) kapalı, doğrudan dosya kopyalama değil.
- **İlk başarılı çözümlemeden sonraki mutasyon bilerek tekrar denetlenmiyor**
  (`registry.py::_verify_integrity`) — bilinçli performans kararı, yazılı hâle getirildi.
- Docker build/run yerel makinede doğrulanamadı; yalnız CI'da kanıtlanacak.
- Windows'a özgü iki hata testlerde bulunup düzeltildi: batch cache'in geçici dosya adı
  MAX_PATH'i aşıyordu; eşzamanlı `os.replace` Windows paylaşım ihlali fırlatabiliyordu.

## Tıkanmalar
Yok.

## Sonraki faza hazırlık
Faz 4 (Unity temel render) için önkoşul **hazır**.

## Değişen dosyalar
- `scripts/`: `publish_dataset.py`, `bench_api.py` (yeni), `check_lod_report.py` (ince CLI)
- `services/geometry-api/src/geometry_api/`: `manifest_validation.py`, `revisions.py`,
  `registry.py`, `deps.py`, `errors.py`, `pagination.py`, `tkmb.py`, `cache.py`, `metrics.py`,
  `conditional.py` (yeni); `main.py`, `config.py`, `build.py` (değişti — `sha256` alanı)
- `services/geometry-api/src/geometry_api/routes/`: 7 dosya (yeni)
- `services/geometry-api/tests/`: 13 yeni dosya, `publish_fixtures.py`
- `docs/`, `.github/workflows/ci.yml`, `docker-compose.yml`, `.env.example`, `CHANGELOG.md`, `README.md`
