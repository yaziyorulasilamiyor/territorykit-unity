# Faz 0 — İskelet, git akışı ve sözleşme

Tarih: 2026-08-12 · Durum: Kısmi (Docker doğrulaması ortam kaynaklı yapılamadı)
Dal: feat/phase-0-scaffold · Commit sayısı: 18

## Ne yapıldı
- Repo iskeleti (`.gitignore`, `LICENSE`, `README`, `CHANGELOG`), `CONTRIBUTING.md`
- TerritoryKit fork'u `vendor/territorykit` submodule, `main`'de `8ae8e6b`'ye sabit
- `docs/mesh-format.md`, `docs/projection.md`, `docs/api.md`
- `services/geometry-api`: FastAPI `GET /health`, `pyproject.toml` (ruff+mypy), Faz 1-3
  modülleri için boş yer tutucular
- `scripts/fetch_sample_dataset.py`, Dockerfile, `docker-compose.yml`, `.env.example`
- `.github/workflows/ci.yml`, Unity UPM paket iskeleti (`package.json`, `.asmdef`)

## Nasıl doğrulandı
| Kontrol | Komut | Sonuç |
|---|---|---|
| Lint/format | `ruff check . && ruff format --check .` | Temiz |
| Tip kontrolü | `mypy src/` | 10 dosyada hata yok |
| Test | `pytest -q --cov=geometry_api` | 1 geçti, `main.py` %100 |
| Fallback dataset | `python scripts/fetch_sample_dataset.py` | 3 fixture poligon (biri delikli) → `data/datasets/` |
| Docker build/up | `docker compose up -d --build` | **Tamamlanamadı — bkz. Tıkanmalar** |
| Submodule | `git submodule status` | `8ae8e6b ... (heads/main)` |

## Kararlar ve gerekçeleri
1. **Uzak dataset kaynağı 404 verdi, fallback fixture'a düşüldü** — talimatta öngörülen davranış.
2. **Yerel venv Python 3.14 ile kuruldu, hedef 3.12** — CI 3.12'yi pinliyor, sonucu etkilemedi.
3. **Unity paket iskeleti sadece manifest/asmdef** — gerçek kod Faz 4'te.

## İnceleme sonrası düzeltmeler (6 madde)
1. `projection.md`: `projection.py` uygulanmadı, iddia kaldırıldı → Faz 1'e ertelendi.
2. `projection.md`: "metrelerce hata" iddiası düzeltildi → sub-metre kuantalama (~0.4-0.5 m) +
   Unity transform/depth birikimi gerekçesi.
3. `projection.md`: `cos(originLat)` yaklaşıklık olarak işaretlendi, Türkiye'de ~%8 sapma
   sayısal olarak yazıldı, Faz 1'e ölçek hatası testi notu eklendi.
4. Veri yolu birleştirildi: script artık `data/datasets/` yazıyor (config ile aynı),
   `docker-compose.yml`'e `./services/geometry-api/data:/app/data` mount eklendi.
5. `mesh-format.md`: gövde little-endian, bilinmeyen flag bitleri yok sayılır,
   `index < vertexCount` zorunlu, NaN/Infinity yasak, payload uzunluğu formülü + trailing-byte
   davranışı eklendi. `api.md`: tüm bbox'ların yerel metre uzayında olduğu belirtildi.
6. Fallback fixture'daki delik ring'i CW'ye çevrildi (RFC 7946). `Settings`'e `env_file`
   desteği eklendi. Fixture'a `source`/`license`/`attribution` (CC0) eklendi. Bu raporun
   commit sayısı 10'dan 18'e düzeltildi.

## Bilinen eksikler ve riskler (ertelenenler)
- Docker build/compose doğrulanamadı (bkz. Tıkanmalar); konteynerden `/health` Docker
  çalışır hale gelince test edilecek.
- Unity `.meta` dosyaları yok — Faz 4'te Unity Editor otomatik üretecek.
- Python 3.12 hedefinin gerçek doğrulaması CI'da yapılacak, henüz tetiklenmedi.
- CI workflow'u GitHub Actions üzerinde henüz tetiklenmedi.

## Tıkanmalar
- **Docker Desktop bu makinede başlamıyor**: `docker compose up`/`docker ps`,
  `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified` hatası
  veriyor. Kullanıcıya göre Docker Desktop açılışta `initializing Inference manager ...
  dockerInference: The filename, directory name, or volume label syntax is incorrect`
  hatasıyla kapanıyor — proje kodundan bağımsız bir ortam sorunu. Düzeltildikten sonra:
  `docker compose up -d --build && curl localhost:8000/health && docker compose down`

## Sonraki faza hazırlık
- Faz 1 için önkoşul durumu: **hazır** — geometri modülleri iskelet halinde,
  `pyproject.toml` earcut/shapely/numpy bağımlılıklarını içeriyor.

## Değişen dosyalar
- `.gitignore`, `LICENSE`, `README.md`, `CHANGELOG.md`, `.env.example`, `CONTRIBUTING.md`
- `.gitmodules`, `vendor/territorykit` (submodule)
- `docs/mesh-format.md`, `docs/projection.md`, `docs/api.md`
- `services/geometry-api/pyproject.toml`, `src/geometry_api/*.py`, `tests/test_health.py`
- `services/geometry-api/Dockerfile`, `docker-compose.yml`, `scripts/fetch_sample_dataset.py`
- `.github/workflows/ci.yml`, `packages/com.oguzhanonur.territorykit-unity/*`
