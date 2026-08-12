# Katkı Kılavuzu

Bu doküman, TerritoryKit.Unity'nin git ve GitHub akışını tanımlar. Amaç: okunduğunda
projenin nasıl geliştiğini anlatan bir commit geçmişi üretmek.

## Repo yapısı

| Repo | Rolü | Kural |
|---|---|---|
| `yaziyorulasilamiyor/TerritoryKit` (fork) | Referans ve veri kaynağı | **Asla değiştirilmez.** Upstream ile senkron kalır |
| `yaziyorulasilamiyor/territorykit-unity` | Asıl proje | Tüm geliştirme burada |

Fork, `vendor/territorykit` altında **git submodule** olarak bağlanır ve belirli bir commit'e
sabitlenir. Hangi commit'e sabitlendiği ilgili faz raporunda belirtilir.

**Yasak:** `vendor/territorykit` içinde dosya değiştirmek. İhtiyaç varsa kendi repona kopyala
ve kaynağını belirt.

## Dal (branch) stratejisi

```
main                    ← her zaman çalışır durumda, korumalı
 └── feat/phase-0-scaffold
 └── feat/phase-1-geometry-engine
 └── feat/phase-2-lod-topology
 └── feat/phase-3-http-api
 └── feat/phase-4-unity-render
 └── feat/phase-5-streaming-pooling
 └── feat/phase-6-hardening-release
```

- Her faz kendi dalında geliştirilir.
- `main`'e doğrudan commit atılmaz.
- Faz bitince PR açılır, onaylanır, `--no-ff` ile merge edilir.
- İnceleme sonrası düzeltmeler aynı dalda yapılır (yeni faz dalı açılmaz).

## Commit kuralları

[Conventional Commits](https://www.conventionalcommits.org/) kullanılır:

```
<tip>(<kapsam>): <özet>

<gövde — neden, ne değil>
```

**Tipler:** `feat` `fix` `docs` `test` `refactor` `perf` `build` `ci` `chore`

**Kapsamlar:** `api` `geometry` `encoding` `simplify` `unity` `pool` `docs` `ci`

**İyi örnek:**

```
feat(geometry): add earcut triangulation with hole support

Flattens polygon rings into a single vertex buffer and passes hole
start indices to earcut. MultiPolygon parts are triangulated
independently and merged with index offsets.
```

**Yasak commit mesajları:** `wip`, `fix`, `update`, `asdf`, `son hali`, `çalışıyor artık`.

**Atomik commit kuralı:** Bir commit = bir mantıksal değişiklik. Bir faz tipik olarak 5-15
commit üretir.

### Commit atmadan önce (Python değişikliklerinde)

```bash
ruff check . && ruff format --check .
mypy src/
pytest
```

Üçü de geçmeden commit atılmaz.

## Faz sonu akışı

1. Rapor dosyasını yaz → `docs: add phase N report` (ayrı commit)
2. Dalı push et
3. PR aç (varsa `gh pr create --title "Phase N: <başlık>" --body-file docs/phases/FAZ-N-RAPOR.md`)
4. **Dur.** Kullanıcı onay verene kadar merge etme
5. Onay gelince `git merge --no-ff` ile `main`'e al, `v0.N.0` tag'i at

PR açıklaması faz raporunun kendisidir, ayrıca yazılmaz.

## Diğer kurallar

- `git push --force` yasak (yayınlanmış dallarda).
- `main` üzerinde rebase yok.
- Örnek dataset dosyaları commit edilmez — `scripts/fetch_sample_dataset.py` indirir, `.gitignore`'dadır.
- Unity `Library/`, `Temp/`, `obj/`, `*.csproj`, `*.sln` commit edilmez.
- Python `__pycache__/`, `.venv/`, `.pytest_cache/`, `.mypy_cache/` commit edilmez.
- `.env` asla commit edilmez, `.env.example` commit edilir.
- Her commit öncesi `git status` ile ne eklendiği kontrol edilir; `git add .` yerine dosyalar
  açıkça eklenir.
