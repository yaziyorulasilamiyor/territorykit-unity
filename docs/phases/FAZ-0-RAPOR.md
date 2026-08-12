# Faz 0 — İskelet, git akışı ve sözleşme

Tarih: 2026-08-12 · Durum: Kısmi (Docker doğrulaması ortam kaynaklı yapılamadı)
Dal: feat/phase-0-scaffold · Commit sayısı: 10

## Ne yapıldı
- Repo iskeleti: `.gitignore`, MIT `LICENSE`, `README.md`, `CHANGELOG.md`
- `CONTRIBUTING.md` — iki-repo yapısı, dal stratejisi, commit kuralları, faz sonu akışı
- TerritoryKit fork'u `vendor/territorykit` altında submodule olarak eklendi, `main` dalının
  `8ae8e6ba29fda68a79c7cf8dc5ae02eb09976008` commit'ine sabitlendi
- `docs/mesh-format.md` (TKMS v1), `docs/projection.md` (Ankara/İstanbul sayısal örnekli),
  `docs/api.md` (planlanan endpoint listesi)
- `services/geometry-api`: FastAPI iskeleti, `GET /health`, `pyproject.toml` (ruff+mypy),
  Faz 1-3 modülleri için boş yer tutucular (`loader.py`, `projection.py`, vb.)
- `scripts/fetch_sample_dataset.py` — uzak kaynak 404 verdi, fallback fixture'a düştü (bkz. Kararlar)
- Dockerfile, `docker-compose.yml`, `.env.example`
- `.github/workflows/ci.yml` — ruff + mypy + pytest
- Unity UPM paket iskeleti: `package.json`, kök `.asmdef`, boş `Tests/`/`Samples~/`

## Nasıl doğrulandı
| Kontrol | Komut | Sonuç |
|---|---|---|
| Lint | `ruff check .` | Temiz |
| Format | `ruff format --check .` | 11 dosya zaten biçimli |
| Tip kontrolü | `mypy src/` | 10 dosyada hata yok |
| Test | `pytest -q --cov=geometry_api` | 1 geçti, `main.py` %100 kapsam |
| Fallback dataset | `python scripts/fetch_sample_dataset.py` | 3 fixture poligon yazıldı (biri delikli) |
| Docker build/up | `docker compose up -d --build` | **Tamamlanamadı — bkz. Tıkanmalar** |
| Submodule | `git submodule status` | `8ae8e6b ... vendor/territorykit (heads/main)` |

## Kararlar ve gerekçeleri
1. **Örnek dataset uzak kaynağı 404 verdi, elle yazılmış 3 poligonluk fixture'a düşüldü** —
   talimatta öngörülen davranış zaten buydu. Fixture'lardan biri delik içeriyor (Faz 1'in
   delik testi için baştan hazır).
2. **Python 3.12 hedeflendi ama geliştirme ortamında sistemde kurulu Python 3.14 ile venv
   kuruldu** — proje kodu 3.12 uyumlu yazıldı, CI `actions/setup-python@v5` ile 3.12'yi
   pinliyor; yerel doğrulama sonucu etkilemedi.
3. **Unity paket iskeleti sadece `package.json` + boş `.asmdef` + `.gitkeep`'lerle kuruldu** —
   gerçek C# implementasyonu Faz 4'te başlıyor, Faz 0 kapsamı sadece klasör/manifest.

## Bilinen eksikler ve riskler
- Docker build/compose hiç doğrulanamadı (bkz. Tıkanmalar). Dockerfile ve compose dosyası
  gözle incelendi ama çalıştırılmadı.
- CI workflow'u GitHub Actions üzerinde henüz tetiklenmedi (push sonrası kontrol edilmeli).

## Tıkanmalar
- **Docker Desktop bu makinede başlamıyor.** `docker info` bir süre sonra yanıt verse de
  `docker compose up -d --build` ve `docker ps` şu hatayla başarısız oluyor:
  `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified`.
  Kullanıcı, Docker Desktop'ın açılışta şu hatayla kapandığını bildirdi:
  `initializing Inference manager ... dockerInference: The filename, directory name, or
  volume label syntax is incorrect`. Bu bir Docker Desktop kurulum/ortam sorunu, proje
  kodunda bir hata değil. Kullanıcı Docker'ı düzelttikten sonra şu komutlarla doğrulama
  yapılabilir:
  ```bash
  docker compose up -d --build
  curl localhost:8000/health
  docker compose down
  ```

## Sonraki faza hazırlık
- Faz 1 için önkoşul durumu: **hazır** — geometri modülleri (`triangulate.py` vb.) boş
  iskelet halinde mevcut, `pyproject.toml` earcut/shapely/numpy bağımlılıklarını içeriyor.
- Docker doğrulaması tamamlanana kadar `docker compose up` gerektiren adımlar (varsa) manuel
  olarak `uvicorn geometry_api.main:app --reload` ile de test edilebilir.

## Değişen dosyalar
- `.gitignore`, `LICENSE`, `README.md`, `CHANGELOG.md`, `.env.example`
- `CONTRIBUTING.md`
- `.gitmodules`, `vendor/territorykit` (submodule)
- `docs/mesh-format.md`, `docs/projection.md`, `docs/api.md`
- `services/geometry-api/pyproject.toml`, `src/geometry_api/*.py`, `tests/test_health.py`
- `services/geometry-api/Dockerfile`, `docker-compose.yml`
- `scripts/fetch_sample_dataset.py`
- `.github/workflows/ci.yml`
- `packages/com.oguzhanonur.territorykit-unity/*` (package.json, asmdef, boş Tests/Samples~)
