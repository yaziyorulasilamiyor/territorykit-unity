# Faz 3 — HTTP API ve cache

Tarih: 2026-08-17 · Durum: Tamamlandı
Dal: feat/phase-3-http-api · Commit sayısı: 17

## Ne yapıldı
- `scripts/publish_dataset.py`: build çıktısını `manifest_validation.check()` +
  `check_report_matches_build()` ile doğrulayıp staging→verify→atomik rename ile 64-hex
  içerik-adresli bir revizyona yayınlar; tombstone + lease-korumalı budama
- `/v1/datasets`(+`{id}`), `.../territories`, `.../viewport` (cursor sayfalama, `?revision=`),
  `.../revisions/{revisionId}/mesh/{territoryId}` (GET/HEAD, gzip, ETag, 304),
  `.../mesh/batch` (TKMB v1); `/health`+`/ready`+`/metrics` kasıtlı olarak `/v1` dışında
- `manifest_validation.py`: `check_lod_report.py`'nin denetleyicisi artık paylaşılan modül —
  CI ve publisher aynı fail-closed kontrolü çalıştırır, iki kopya değil
- `registry.py`: revizyon çözümleme, yayın-sonrası bütünlük denetimi, bellek-içi lease
- `tkmb.py`/`cache.py`: TOC her zaman id-sıralı, batch cache revizyona göre dizinli
- CI'a ayrı `docker` job'ı: build + run + `/health` poll + log dökümü + cleanup

## Nasıl doğrulandı
| Kontrol | Komut | Sonuç |
|---|---|---|
| Lint/format | `ruff check . && ruff format --check .` | Temiz |
| Tip kontrolü | `mypy src/` | 28 dosyada hata yok |
| Test | `pytest -q --cov=geometry_api` | **363 geçti, 0 atlandı**, kapsam **%95** |
| lod-chain (değişmeden) | `check_lod_report.py` | 39 test aynen geçiyor |
| Bench (127.0.0.1, gerçek gecikme) | `bench_api.py --requests 30` | mesh cache-hit p50 **14 ms**, p95 **25 ms** |

## Kararlar ve gerekçeleri
1. **Doğrulama yayınlama zamanında, istekte değil** — `publish_dataset.py` iki bağımsız kontrol
   (rapor bütünü + rapor↔manifest çapraz) geçmeden hiçbir dosya `revisions/` altına yazılmaz;
   API artık geçerliliği varsayar, yeniden hesaplamaz. Alternatif: API'de yeniden doğrulama —
   reddedildi, madde 1'i (istek başına ağır iş yok) ihlal eder.
2. **`revisionId` staging snapshot'ından hesaplanır, kaynaktan değil** — hesaplama ile kopyalama
   arasında kaynağın değişebileceği pencereyi kapatır; aynı fonksiyon yayın-sonrası bütünlük
   denetimi için de kullanılır (iki ayrı checksum şeması değil).
3. **Docker: native uvicorn + CI'da gerçek konteyner health-check'i** — Docker Desktop Faz 0'dan
   beri bu makinede çalışmıyor; bitiş kriterini buna bağlamak üçüncü fazı da tıkardı.

## Bilinen eksikler ve riskler
- **Yanlış-sınıflandırma açığı devralındı, kapatılmadı** (Faz 2'den, FAZ-2-RAPOR.md §75): denetim
  kaydedilen olayların kendi içinde tutarlı olduğunu kanıtlar, olayların gerçek geometriyi doğru
  sınıflandırdığını kanıtlamaz. Doğal veride gözlenmedi, teorik risk.
- Docker build/run **yerel makinede doğrulanamadı** (Faz 0-2 ile aynı ortam sorunu); yalnız
  CI'da `docker` job'ı üzerinden kanıtlanacak, henüz tetiklenmedi.
- Metrikler süreç-içi, kalıcı değil, çoklu-worker'da toplanmıyor — bilinçli, belgelenmiş sınır.
- Bench sayıları tek makine/tek koşu; eşik değil, yalnız ölçüm.
- Windows'a özgü iki gerçek hata testler sırasında bulunup düzeltildi: batch cache'in geçici
  dosya adı 64+64 hex karakterle MAX_PATH'i (260) aşıyordu; eşzamanlı `os.replace` aynı hedefe
  yazınca Windows paylaşım ihlali fırlatabiliyordu (POSIX rename'de karşılığı yok) — ikisi de
  testle sabitlendi (`test_cache.py`).

## Tıkanmalar
Yok.

## Sonraki faza hazırlık
Faz 4 (Unity temel render) için önkoşul **hazır**: `/v1/datasets/{id}` seviye bayraklarını
indirmeden önce veriyor, mesh/batch uç noktaları revizyon-pinli ve test edilmiş durumda.

## Değişen dosyalar
- `scripts/`: `publish_dataset.py` (yeni), `bench_api.py` (yeni), `check_lod_report.py`
  (ince CLI'a indirgendi)
- `services/geometry-api/src/geometry_api/`: `manifest_validation.py`, `revisions.py`,
  `registry.py`, `deps.py`, `errors.py`, `pagination.py`, `tkmb.py`, `cache.py`, `metrics.py`,
  `conditional.py` (hepsi yeni); `main.py`, `config.py`, `build.py` (değişti)
- `services/geometry-api/src/geometry_api/routes/`: `ops.py`, `datasets.py`, `territories.py`,
  `viewport.py`, `mesh.py`, `batch.py`, `common.py` (hepsi yeni)
- `services/geometry-api/tests/`: 11 yeni test dosyası, `publish_fixtures.py`
- `docs/`: `api.md`, `mesh-format.md` (TKMB), `PROJE-TALIMATI.md`, `phases/FAZ-3-PLAN.md`
- `.github/workflows/ci.yml`, `docker-compose.yml`, `.env.example`, `CHANGELOG.md`, `README.md`
