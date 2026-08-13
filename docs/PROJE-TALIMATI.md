# TerritoryKit.Unity — Proje Talimatı

Sen bu projenin baş geliştiricisisin. Aşağıdaki talimatı baştan sona oku, sonra **sadece Faz 0'ı** uygula.

---

## 1. Misyon

TerritoryKit (https://github.com/mberatkaya/TerritoryKit) açık kaynak bir TypeScript geospatial SDK'sıdır. Hiyerarşik, düzensiz poligon "bölgeler" (ülke → il → ilçe → mahalle) için lookup, hiyerarşi, komşuluk ve viewport tabanlı yükleme sağlar.

Mevcut renderer adaptörleri: **MapLibre, Leaflet, OpenLayers** — üçü de web. **Hiçbir oyun motoru adaptörü yok.**

Bu proje o boşluğu dolduruyor:

| Bileşen | Ne yapar |
|---|---|
| **Geometry API** (Python/FastAPI) | Bölge poligonlarını sunucu tarafında üçgenler (triangulation), LOD üretir, binary mesh olarak servis eder |
| **Unity paketi** (C#) | Bu mesh'leri indirir, Unity `Mesh` nesnesine çevirir, havuzlar (pooling), viewport'a göre yükler/boşaltır, tıklamayı bölgeye eşler |

**Neden ağır iş sunucuda?** Triangülasyon ve topoloji-güvenli basitleştirme CPU-yoğun işlerdir. Mobil cihazda her açılışta yapmak pil ve süre israfıdır. Sunucuda bir kez hesaplanır, sonsuza kadar cache'lenir.

---

## 2. Değişmez kurallar

1. **Her fazın sonunda DUR.** Rapor dosyasını yaz, kısa bir özet ver, sonraki faza **geçme**. Kullanıcı "Faz N'e geç" diyene kadar bekle.
2. **Faz atlama, birleştirme, sıra değiştirme yok.** Bir fazın "Bitti sayılır" maddelerinin hepsi yeşil olmadan faz bitmez.
3. **Kapsam genişletme yok.** Fazın kapsamında olmayan bir şey iyi fikir gibi görünüyorsa yapma — rapordaki "Öneriler" bölümüne yaz.
4. **Test yazmadan özellik bitmez.** Her faz kendi testlerini içerir. Testler geçmeden faz kapanmaz.
5. **Tahmin etme, doğrula.** "Muhtemelen çalışır" yok. Komutu çalıştır, çıktıyı gör.
6. **Tıkandığında dur ve sor.** 2 kez denedin ve olmadıysa uydurma; raporda "Tıkanma" olarak yaz ve kullanıcıya sor.
7. **Kod ve tanımlayıcılar İngilizce**, dokümanlar ve raporlar **Türkçe**, **commit mesajları İngilizce**.
8. **Sır yok.** API anahtarı, token, kişisel veri repoya girmez. `.env.example` kullan.

---

## 3. Git ve GitHub akışı

Bu bölüm en az kod kadar önemli. Amaç: **okunduğunda projenin nasıl geliştiğini anlatan bir commit geçmişi.**

### 3.1 Repo yapısı — iki repo

| Repo | Rolü | Kural |
|---|---|---|
| `yaziyorulasilamiyor/TerritoryKit` (fork) | Referans ve veri kaynağı | **Asla değiştirilmez.** Upstream ile senkron kalır |
| `yaziyorulasilamiyor/territorykit-unity` | Asıl proje | Tüm geliştirme burada |

Fork, asıl repoya **git submodule** olarak bağlanır ve **belirli bir commit'e sabitlenir**:

```bash
git submodule add https://github.com/yaziyorulasilamiyor/TerritoryKit.git vendor/territorykit
cd vendor/territorykit
git checkout <sabit-commit-veya-tag>    # hangi commit olduğunu Faz 0 raporuna yaz
cd ../..
git add .gitmodules vendor/territorykit
```

**Neden submodule?** TerritoryKit hâlâ geliştiriliyor. Sabitlenmezse bir sabah kalkıp "dün çalışıyordu" dersin. Ayrıca dataset şeması ve `territory` CLI'ı buradan gelir.

**Yasak:** `vendor/territorykit` içinde dosya değiştirmek. Orada bir şeye ihtiyacın varsa kendi repona kopyala ve kaynağını belirt.

### 3.2 Dal (branch) stratejisi

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

- Her faz **kendi dalında** geliştirilir
- `main`'e **doğrudan commit atılmaz**
- Faz bitince PR açılır, kullanıcı onaylar, `--no-ff` ile merge edilir
- İnceleme sonrası düzeltmeler **aynı dalda** yapılır (yeni faz dalı açma)

### 3.3 Commit kuralları

**Conventional Commits** kullan. Format:

```
<tip>(<kapsam>): <özet>

<gövde — neden, ne değil>
```

**Tipler:** `feat` `fix` `docs` `test` `refactor` `perf` `build` `ci` `chore`

**Kapsamlar:** `api` `geometry` `encoding` `simplify` `unity` `pool` `docs` `ci`

**İyi örnekler**
```
feat(geometry): add earcut triangulation with hole support

Flattens polygon rings into a single vertex buffer and passes hole
start indices to earcut. MultiPolygon parts are triangulated
independently and merged with index offsets.

test(geometry): verify triangulated area matches polygon area

Adds a 0.1% tolerance check across all fixture polygons. Catches
winding and hole-handling regressions.

docs: add phase 1 report
```

**Yasak commit mesajları:** `wip`, `fix`, `update`, `asdf`, `son hali`, `çalışıyor artık`

**Atomik commit kuralı:** Bir commit = bir mantıksal değişiklik. Triangülasyon kodu ve HTTP endpoint aynı commit'te olmaz. Bir faz tipik olarak **5-15 commit** üretir. Tek dev commit atma.

**Commit atmadan önce her seferinde:**
```bash
ruff check . && ruff format --check .
mypy src/
pytest
```
Üçü de geçmeden commit atma.

### 3.4 Faz sonu akışı

Faz bitince sırayla:

1. Rapor dosyasını yaz → `docs: add phase N report` (ayrı commit)
2. Dalı push et
3. PR aç — `gh` CLI varsa:
   ```bash
   gh pr create --title "Phase N: <başlık>" --body-file docs/phases/FAZ-N-RAPOR.md
   ```
   Yoksa push çıktısındaki PR URL'ini kullanıcıya ver
4. **DUR.** Kullanıcı incelemeyi yaptırıp onay verene kadar merge etme
5. Onay gelince: `git merge --no-ff` ile `main`'e al, `v0.N.0` tag'i at

**PR açıklaması** faz raporunun kendisidir. Ayrıca yazma.

### 3.5 Diğer kurallar

- `git push --force` **yasak** (yayınlanmış dallarda)
- `main` üzerinde rebase **yok**
- Örnek dataset dosyaları **commit edilmez** — `scripts/fetch_sample_dataset.py` indirir, `.gitignore`'a eklenir
- Unity `Library/`, `Temp/`, `obj/`, `*.csproj`, `*.sln` **commit edilmez**
- Python `__pycache__/`, `.venv/`, `.pytest_cache/`, `.mypy_cache/` **commit edilmez**
- `.env` **asla** commit edilmez, `.env.example` commit edilir
- Her commit öncesi `git status` ile ne eklediğini kontrol et; `git add .` yerine dosyaları açıkça ekle

### 3.6 Bunu Faz 0'da kur

Faz 0'da `CONTRIBUTING.md` yaz ve yukarıdaki commit/dal kurallarını oraya koy. Sonraki fazlarda kendi yazdığın kurallara uy.

---

## 4. Teknoloji kararları (bunlar sabit)

**Python tarafı**
- Python 3.12
- FastAPI + Uvicorn
- Pydantic v2
- `shapely` (2.x) — geometri
- `mapbox-earcut` — triangülasyon
- `numpy` — vertex/index buffer
- `pytest` + `pytest-cov` — test
- `ruff` — lint + format
- `mypy` — tip kontrolü (strict değil, `--ignore-missing-imports` yeterli)
- Docker + Docker Compose

**Unity tarafı**
- Unity **2022.3 LTS veya daha yeni**
- UPM (Unity Package Manager) paket düzeni
- `UnityWebRequest` — HTTP
- `Unity.Collections` (`NativeArray`) — GC baskısını azaltmak için
- Unity Test Framework — EditMode + PlayMode testleri
- Harici bağımlılık **yok** (Newtonsoft hariç, gerekirse)

**Yasaklar**
- Kimlik doğrulama, kullanıcı hesabı, veritabanı **yok** (bu bir kütüphane, uygulama değil)
- Oyun mantığı **yok** (sahiplik, skor, fetih — hepsi kapsam dışı)
- Web frontend **yok**

---

## 5. Repo düzeni

```
territorykit-unity/
├── README.md
├── LICENSE                      # MIT
├── CONTRIBUTING.md              # Bölüm 3'teki git kuralları
├── CHANGELOG.md
├── .gitignore
├── .gitmodules
├── .env.example
├── docker-compose.yml
│
├── vendor/
│   └── territorykit/            # SUBMODULE — fork, sabit commit, dokunma
│
├── docs/
│   ├── mesh-format.md           # binary format spesifikasyonu
│   ├── projection.md            # koordinat dönüşümü
│   ├── api.md                   # HTTP API sözleşmesi
│   ├── REVIEWER-BRIEF.md        # (kullanıcı sağlayacak, dokunma)
│   └── phases/
│       ├── FAZ-0-RAPOR.md
│       ├── FAZ-1-RAPOR.md
│       └── ...
│
├── services/geometry-api/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── src/geometry_api/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app
│   │   ├── config.py
│   │   ├── loader.py            # dataset okuma
│   │   ├── projection.py        # WGS84 → local meters
│   │   ├── triangulate.py       # earcut sarmalayıcı
│   │   ├── simplify.py          # topoloji-güvenli LOD
│   │   ├── encoding.py          # binary encoder/decoder
│   │   ├── cache.py
│   │   └── routes/
│   └── tests/
│
├── packages/com.oguzhanonur.territorykit-unity/
│   ├── package.json
│   ├── README.md
│   ├── Runtime/
│   │   ├── TerritoryKit.Unity.asmdef
│   │   ├── TerritoryClient.cs
│   │   ├── MeshDecoder.cs
│   │   ├── TerritoryPool.cs
│   │   ├── ViewportStreamer.cs
│   │   └── TerritoryPicker.cs
│   ├── Tests/
│   │   ├── Editor/
│   │   └── Runtime/
│   └── Samples~/BasicMap/
│
├── scripts/
│   └── fetch_sample_dataset.py
│
└── .github/workflows/ci.yml
```

---

## 6. Binary format — TKMS v1

Bu spesifikasyon **sabit**. Değiştirme, `docs/mesh-format.md`'ye aynen yaz.

```
TKMS (TerritoryKit Mesh Stream) v1 — little-endian

Header — 32 byte:
  offset  tip        alan
  0       char[4]    magic = "TKMS"
  4       uint16     version = 1
  6       uint16     flags   (bit0: 1 ise index'ler uint32, 0 ise uint16)
  8       uint32     vertexCount
  12      uint32     indexCount
  16      float32    minX      (yerel metre)
  20      float32    minY
  24      float32    maxX
  28      float32    maxY

Body:
  float32[vertexCount * 2]                 vertices — yerel metre, XY sırası
  uint16[indexCount] | uint32[indexCount]  indices  — üçgen listesi
```

**Kurallar**
- `vertexCount > 65535` ise flags bit0 = 1 olmak zorunda
- `indexCount % 3 == 0` olmak zorunda
- Üçgen sarım yönü (winding): **saat yönü (clockwise)** — Unity'de ön yüz budur
- Boş geometri geçersizdir; en az 1 üçgen olmalı

---

## 7. Koordinat dönüşümü

`float32` hassasiyeti ile enlem/boylam derecelerini doğrudan kullanmak **kabul edilemez** — Türkiye ölçeğinde metrelerce hata verir.

**Zorunlu akış:**

```
WGS84 (lon, lat) derece
   → Web Mercator (EPSG:3857) metre
   → origin çıkarılır  (dataset merkezinin Mercator karşılığı)
   → ölçek düzeltmesi: cos(originLatitude) ile çarp
   → Unity'ye float32 XY olarak gider
```

- `origin` **dataset seviyesinde** tanımlanır, mesh içinde değil
- `/datasets/{id}` endpoint'i `originLon`, `originLat`, `projection` alanlarını döner
- Unity tarafı bu origin'i bir kez alır ve tüm mesh'lere aynı uzayda davranır
- Bunu `docs/projection.md`'de örnek sayılarla anlat

---

## 8. Bilinen tuzaklar (bunları baştan bil)

| Tuzak | Ne olur | Ne yapmalısın |
|---|---|---|
| Delikli poligon (enclave) | earcut yanlış üçgenler üretir | Ring'leri düzleştir, hole index'lerini doğru ver |
| MultiPolygon | Parçalar birbirine bağlanır | Her parçayı ayrı üçgenle, index offset ile birleştir |
| Sarım yönü | Yüzeyler ters, görünmez | Encoder'da normalize et, testle doğrula |
| `float32` hassasiyeti | Sınırlar kayar | Origin çıkarma zorunlu (Bölüm 7) |
| 65535 vertex limiti | Mesh bozulur | `IndexFormat.UInt32` + flags biti |
| Bağımsız basitleştirme | Komşu sınırlar arasında **çatlak** oluşur | Paylaşılan kenarı bir kez basitleştir (Faz 2) |
| Her bölge = 1 GameObject | 900 draw call, telefonda ölür | Pooling + batching (Faz 5) |
| `new Mesh()` her karede | GC spike, takılma | Havuzdan al, `mesh.Clear()` ile yeniden kullan |
| Dataset lisansı | Hukuki sorun | Kaynağı ve lisansı README'de belirt (geoBoundaries/OSM CC BY-SA atıf ister) |
| Antimeridyen / kutuplar | Poligon ters çevrilir, dev üçgen | TKMS v1 bunları desteklemiyor. `docs/mesh-format.md`'ye **bilinen sınır** olarak yaz; Türkiye kapsamında sorun değil |
| CRS belirsizliği | İstemci yanlış uzayda çizer | Projeksiyon ve origin dataset seviyesinde tanımlı, mesh dosyasında değil. Bu bilinçli; `docs/mesh-format.md`'de gerekçesiyle yazılı olsun |

---

## 9. FAZLAR

---

### FAZ 0 — İskelet, git akışı ve sözleşme

**Amaç:** Kod yazmadan önce sözleşmeyi yazıya dök, boru hattını ve git disiplinini kur.

**Dal:** `feat/phase-0-scaffold`

**Yapılacaklar**
- [ ] Git repo başlat, ilk commit `chore: initial repository scaffold`
- [ ] `.gitignore` (Python + Unity + IDE), MIT LICENSE, README taslağı
- [ ] `CONTRIBUTING.md` — Bölüm 3'teki git ve commit kurallarını yaz
- [ ] Fork'u submodule olarak ekle (Bölüm 3.1), sabitlenen commit'i rapora yaz
- [ ] Bölüm 5'teki klasör yapısını oluştur (boş dosyalar olabilir)
- [ ] `docs/mesh-format.md` — Bölüm 6'yı aynen yaz
- [ ] `docs/projection.md` — Bölüm 7'yi örnek sayılarla yaz
- [ ] `docs/api.md` — planlanan endpoint listesi (henüz uygulanmamış olabilir)
- [ ] `pyproject.toml`, bağımlılıklar, `ruff` + `mypy` yapılandırması
- [ ] `main.py` — sadece `GET /health` → `{"status":"ok","version":"0.1.0"}`
- [ ] `tests/test_health.py` — health endpoint testi
- [ ] Dockerfile + docker-compose.yml
- [ ] `scripts/fetch_sample_dataset.py` — küçük bir örnek GeoJSON indirir (Türkiye il sınırları, ~81 poligon). Kaynak bulunamazsa 3 basit poligonluk elle yazılmış bir fixture üret ve bunu raporda belirt
- [ ] `.github/workflows/ci.yml` — lint + test

**Bitti sayılır**
- `docker compose up` çalışıyor, `curl localhost:8000/health` cevap veriyor
- `pytest` geçiyor, `ruff check .` temiz
- CI dosyası var ve mantıklı
- Submodule sabitlenmiş, `git submodule status` doğru commit'i gösteriyor
- Commit geçmişi Conventional Commits'e uyuyor, en az 4 anlamlı commit var

---

### FAZ 1 — Geometri motoru (Unity yok, saf Python)

**Amaç:** Poligondan doğru mesh üretmek. Görsel yok, sadece kanıtlanmış matematik.

**Dal:** `feat/phase-1-geometry-engine`

**Yapılacaklar**
- [ ] `loader.py` — hem TerritoryKit `dataset.json` hem düz GeoJSON `FeatureCollection` okuyabilsin. Hangi formatı okuduğunu otomatik algılasın
- [ ] `projection.py` — Bölüm 7'deki dönüşüm, ters dönüşüm de olsun
- [ ] `triangulate.py` — earcut sarmalayıcı: delik desteği, MultiPolygon desteği, winding normalizasyonu
- [ ] `encoding.py` — TKMS v1 encoder + decoder
- [ ] CLI: `python -m geometry_api.build --input <dataset> --output <dir>` — tüm bölgeler için mesh üretir

**Testler (bunlar pazarlık konusu değil)**
- [ ] **Alan korunumu:** üçgenlerin alan toplamı, poligon alanına %0.1 tolerans içinde eşit
- [ ] **Dejenere üçgen yok:** sıfır alanlı üçgen üretilmiyor
- [ ] **Winding:** tüm üçgenler saat yönü
- [ ] **Delik testi:** içinde delik olan poligonda, deliğin merkezini içeren üçgen yok
- [ ] **MultiPolygon testi:** parça sayısı korunuyor, parçalar arası üçgen yok
- [ ] **Round-trip:** encode → decode → aynı vertex ve index dizileri
- [ ] **Hassasiyet:** projeksiyon sonrası geri dönüşte hata < 1 metre

**Bitti sayılır**
- Örnek dataset'teki tüm bölgeler için mesh üretiliyor, hiçbiri hata vermiyor
- Yukarıdaki 7 test grubu geçiyor
- Test kapsamı (coverage) %80+

---

### FAZ 2 — LOD üretimi (TerritoryKit sadeleştirmesi üzerinden)

**Dal:** `feat/phase-2-lod-topology`

> ⚠️ **BU FAZ YENİDEN TANIMLANDI. Eski planı uygulama.**
>
> TerritoryKit'te **zaten topoloji-güvenli sadeleştirme var**:
> ```
> territory geometry simplify <dataset.json> --strategy topology-safe --detail high,medium,low
> ```
> Kendi Douglas-Peucker / arc grafiği implementasyonunu **YAZMA**. İki farklı sadeleştirme politikası zamanla ayrışır ve bakım yükü olur. Adapte ettiğin kütüphanenin çıktısını girdi olarak kullan.

**Amaç:** 3 detay seviyesi üretmek. Sadeleştirmeyi TerritoryKit yapar; **sen üçgenler ve çatlak kalmadığını kanıtlarsın.**

**Yeni akış**

```
geoBoundaries GeoJSON
   ↓  territory import geojson         (vendor/territorykit CLI)
dataset.json
   ↓  territory geometry simplify --strategy topology-safe --detail high,medium,low
high / medium / low dataset
   ↓  senin Python servisin (Faz 1 kodu)
TKMS mesh dosyaları × 3 seviye
```

**Yapılacaklar**
- [ ] `vendor/territorykit` içinde `pnpm install && pnpm build` çalıştır, `territory` CLI'ını kullanılabilir hale getir
- [ ] `scripts/build_lod.py` — yukarıdaki zinciri otomatikleştir, deterministik, tekrar çalıştırılabilir
- [ ] Node/pnpm sürüm gereksinimlerini `README.md`'ye ve CI'ya yaz
- [ ] `loader.py` üç seviyeyi de okuyabilsin (Faz 1'de dataset.json desteği zaten var)
- [ ] Build CLI'a `--lod high|medium|low` seçeneği ekle, her seviye ayrı çıktı dizinine
- [ ] `simplify.py` **yer tutucu olarak kalır** — içine "TerritoryKit CLI kullanılıyor, bkz. scripts/build_lod.py" notu yaz

**Testler**
- [ ] **Çatlak testi (en kritik):** her LOD seviyesinde, **üçgenleme ve float32 yuvarlaması SONRASI** komşu bölgeler arasında boşluk veya çakışma yok. TerritoryKit temiz çıktı verse bile senin boru hattın bozabilir — asıl kanıtlanacak olan bu
- [ ] **Paylaşılan vertex testi:** Faz 1'deki komşu-vertex eşitlik testini üç seviyenin her birinde tekrarla
- [ ] **Kapsama testi:** Faz 1'in nokta-üçgen-içinde testini her seviyede tekrarla
- [ ] **Vertex azalması:** seviye başına tablo; `low`, `high`'ın en fazla %25'i
- [ ] **Topoloji korunumu:** bölge sayısı, parça sayısı, delik sayısı seviyeler arasında tutarlı
- [ ] **Determinizm:** aynı girdi → byte-identik çıktı (her seviyede)

**Tıkanma kuralı**
TerritoryKit CLI çalışmazsa (Node sürümü, submodule pin uyumsuzluğu, build hatası) **DUR ve sor.** Kendi implementasyonuna geçme — bu bilinçli bir mimari karardır.

**Bitti sayılır**
- Üç seviye üretiliyor
- Çatlak testi üç seviyede de geçiyor
- Vertex azalma tablosu raporda var
- `scripts/build_lod.py` sıfırdan çalıştırılabiliyor ve belgelenmiş

---

### FAZ 3 — HTTP API ve cache

**Amaç:** Unity'nin konuşabileceği gerçek bir servis.

**Dal:** `feat/phase-3-http-api`

> ⚠️ **Mimari kural:** FastAPI **istek başına geometri hesaplamaz.** Mesh'ler Faz 1/2'nin build CLI'ı tarafından önceden üretilir; servis yalnızca manifest, viewport seçimi ve hazır artifact sunar. Bu, quantized-mesh / 3D Tiles / I3S gibi yerleşik geospatial streaming modelleriyle aynı yaklaşımdır. Artifact'lar sürümlü ve değişmez (immutable) olmalı ki CDN ve HTTP cache doğru çalışsın.

**Endpoint'ler**
- [ ] `GET /health`
- [ ] `GET /datasets` — mevcut dataset listesi
- [ ] `GET /datasets/{id}` — metadata: origin, projection, seviye listesi, bölge sayısı, bbox
- [ ] `GET /datasets/{id}/territories` — bölge listesi: id, ad, parent, bbox, komşular
- [ ] `GET /datasets/{id}/mesh/{territory_id}?lod=medium` — TKMS binary
- [ ] `GET /datasets/{id}/viewport?bbox=x1,y1,x2,y2&lod=medium` — görünür bölge id listesi
- [ ] `POST /datasets/{id}/mesh/batch` — çoklu mesh tek istekte (TKMB konteyner formatı; formatı `docs/mesh-format.md`'ye ekle)

**Yapılacaklar**
- [ ] Disk tabanlı, içerik-adresli cache (aynı girdi → aynı çıktı, bir kez hesapla)
- [ ] `ETag` + `Cache-Control` başlıkları, `304 Not Modified` desteği
- [ ] Binary yanıtlarda gzip
- [ ] Hatalar için tutarlı JSON şeması (`{"error": {"code": ..., "message": ...}}`)
- [ ] OpenAPI dokümanı anlamlı (açıklamalar, örnekler)

**Testler**
- [ ] Her endpoint için mutlu yol + hata yolu testi
- [ ] Cache hit/miss testi
- [ ] ETag / 304 testi
- [ ] Viewport doğruluğu: bbox dışındaki bölge dönmüyor, kesişen dönüyor
- [ ] Basit yük ölçümü: cache hit p95 gecikmesi raporda sayı olarak yazılı

**Bitti sayılır**
- `docker compose up` → Swagger'da tüm endpoint'ler denenebiliyor
- Testler geçiyor, yük ölçümü raporda var

---

### FAZ 4 — Unity paketi: temel render

**Amaç:** Ekranda harita görmek.

**Dal:** `feat/phase-4-unity-render`

**Yapılacaklar**
- [ ] UPM paket iskeleti: `package.json`, asmdef'ler, Runtime/Tests/Samples~
- [ ] `TerritoryClient.cs` — async HTTP (`UnityWebRequest`), dataset metadata + mesh indirme, iptal (cancellation) desteği
- [ ] `MeshDecoder.cs` — TKMS byte dizisi → Unity `Mesh`
  - `NativeArray` + `Mesh.SetVertexBufferData` / `SetIndexBufferData` kullan
  - `IndexFormat.UInt32` desteği
  - Ana thread'i kilitleme: parse işini mümkün olduğunca thread dışına al
- [ ] `Samples~/BasicMap` — örnek sahne: dataset yükler, tüm bölgeleri çizer, kamera üstten bakar
- [ ] Paket README'si: kurulum (git URL ile UPM), hızlı başlangıç

**Testler**
- [ ] EditMode: TKMS decoder testi (bilinen byte dizisi → beklenen mesh)
- [ ] EditMode: bozuk/eksik veri → düzgün hata, çökme yok
- [ ] PlayMode: sahte (mock) sunucudan mesh yükleme testi

**Bitti sayılır**
- Örnek sahne çalışıyor, Türkiye il sınırları Unity'de görünüyor
- **Ekran görüntüsü** `docs/phases/` altına kaydedilmiş ve raporda referans verilmiş

---

### FAZ 5 — Pooling, viewport streaming, seçim

**Amaç:** Akıcı hale getirmek ve etkileşim eklemek.

**Dal:** `feat/phase-5-streaming-pooling`

**Yapılacaklar**
- [ ] `TerritoryPool.cs` — GameObject + Mesh havuzu. Viewport dışına çıkan bölge havuza döner, `mesh.Clear()` ile yeniden kullanılır. **Steady state'te sıfır tahsis (allocation) hedefi**
- [ ] `ViewportStreamer.cs` — kamera bbox'ını hesaplar, `/viewport` çağırır, eksikleri yükler, fazlaları boşaltır. Gereksiz istek atmamak için debounce
- [ ] LOD geçişi: kamera mesafesine/zoom'a göre seviye seçimi, histerezis ile titreme önleme
- [ ] `TerritoryPicker.cs` — ekran tıklaması → bölge id. Raycast + MeshCollider, ya da CPU tarafında nokta-içinde-üçgen testi (hangisini seçtiğini gerekçelendir)
- [ ] Renklendirme API'si: `SetTerritoryColor(id, color)` — `MaterialPropertyBlock` ile, materyal çoğaltmadan
- [ ] Örnek sahneyi güncelle: pan/zoom, tıklayınca bölge vurgulanır, rastgele renkler

**Testler**
- [ ] PlayMode: havuz — 100 yükle/boşalt döngüsünden sonra GameObject sayısı sabit
- [ ] PlayMode: viewport — kamera hareketinde doğru bölgeler yükleniyor/boşalıyor
- [ ] EditMode: picking — bilinen koordinat → beklenen bölge id
- [ ] Profiler ölçümü: steady state GC tahsisi ve draw call sayısı raporda **sayı olarak**

**Bitti sayılır**
- Pan/zoom akıcı (hedef: 60 FPS, editörde ölçülmüş)
- Steady state GC tahsisi ≈ 0
- Tıklama doğru bölgeyi seçiyor

---

### FAZ 6 — Sağlamlaştırma ve yayın

**Amaç:** Başkasının kurup kullanabileceği hale getirmek.

**Dal:** `feat/phase-6-hardening-release`

**Yapılacaklar**
- [ ] Hata yönetimi gözden geçirmesi: sunucu kapalı, ağ koptu, bozuk veri, iptal — hepsi düzgün davranmalı
- [ ] Performans profili: 81 il + (varsa) 900+ ilçe ile ölçüm, sonuçlar tablo
- [ ] `README.md` — ne olduğu, neden var olduğu, kurulum, 10 satırlık hızlı başlangıç, mimari şeması, **GIF veya ekran görüntüsü**
- [ ] `README.md`'ye **"Alternatifler"** bölümü — dürüst karşılaştırma, savunmacı olmadan:
  - **Cesium for Unity** (Apache-2.0): v1.23'ten beri GeoJSON okuyup terrain/3D Tiles üzerine raster overlay olarak drape edebiliyor. Çıktısı raster katman; bölge başına ayrı `Mesh`, collider, havuzlama ve üçgen-bazlı kesin bölge seçimi vermiyor
  - **ArcGIS Maps SDK for Unity**: kapsamlı ama Esri lisansı/hesabı gerektiriyor, ~538 MB, URP/HDRP zorunlu
  - **Mapbox Unity SDK**: vector tile → mesh dönüşümü yapıyor (en yakın örtüşme), ama Mapbox hesabı/TOS'una ve iOS/Android hedeflerine bağlı
  - **MapLibre**: resmi Unity SDK'sı yok
  - Sonuç cümlesi: bu paket, TerritoryKit'in kimlik/hiyerarşi/komşuluk modelinden **self-hosted, bölge başına Mesh** üreten dar bir ihtiyaca hizmet eder; yukarıdakilerin yerini almaz
- [ ] `CHANGELOG.md` — semver, 0.1.0 girişi
- [ ] Lisans ve veri kaynağı atıfları
- [ ] UPM git URL ile kurulabilir olduğunu **temiz bir Unity projesinde doğrula**
- [ ] Geometry API için Docker image build eder ve çalışır
- [ ] CI: Python testleri + Unity testleri (Unity CI zorsa, sebebini yaz ve manuel adımı belgele)
- [ ] `docs/` içindeki her şey güncel ve uygulamayla tutarlı
- [ ] `v0.1.0` release tag'i ve GitHub Release notu

**Bitti sayılır**
- Temiz bir Unity projesine paket kurulabiliyor ve örnek sahne çalışıyor
- README'de görsel var
- Tüm testler geçiyor, CI yeşil

---

## 10. Faz raporu formatı

Her fazın sonunda `docs/phases/FAZ-{N}-RAPOR.md` oluştur.

**Sert kurallar:**
- **En fazla 70 satır.** Bu bir sınır, hedef değil — kısa tut
- Kod bloğu en fazla 15 satır, sadece kritik kararı gösteriyorsa
- Dosya içeriği yapıştırma, dosya listesi 15 kalemi geçmesin
- "Ne yaptım" değil, "**ne kanıtladım**" yaz

**Şablon:**

```markdown
# Faz {N} — {Başlık}

Tarih: YYYY-AA-GG · Durum: Tamamlandı / Kısmi
Dal: feat/phase-{N}-{slug} · Commit sayısı: {n}

## Ne yapıldı
- (madde madde, en fazla 8 madde)

## Nasıl doğrulandı
| Kontrol | Komut | Sonuç |
|---|---|---|
| ... | `pytest tests/...` | 23 geçti |
(ölçüm varsa sayı yaz: gecikme, vertex sayısı, FPS, GC byte)

## Kararlar ve gerekçeleri
1. **{Karar}** — {tek cümle gerekçe}. Alternatif: {ne reddedildi ve neden}
(en fazla 3 karar)

## Bilinen eksikler ve riskler
- (dürüst ol; "yok" diyorsan gerçekten yok olmalı)

## Tıkanmalar
- (yoksa "Yok")

## Sonraki faza hazırlık
- Faz {N+1} için önkoşul durumu: hazır / şu eksik

## Değişen dosyalar
(en fazla 15 kalem, gruplayarak)
```

---

## 11. Şimdi ne yapacaksın

1. Bu talimatı okuduğunu ve anladığını **3-4 cümleyle** teyit et
2. Kullanıcıya sor: GitHub'da `territorykit-unity` reposu ve TerritoryKit fork'u hazır mı? Hazır değilse hangi adımları atması gerektiğini söyle ve bekle
3. Hazırsa `feat/phase-0-scaffold` dalını aç ve **Faz 0'ı** uygula
4. `docs/phases/FAZ-0-RAPOR.md` yaz, ayrı commit at
5. Dalı push et, PR aç
6. **DUR.** Kullanıcıya kısa bir özet ver ve PR linkini paylaş

Başla.
