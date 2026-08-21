# Faz 6 — Sağlamlaştırma ve yayın

Tarih: 2026-08-21 · Durum: İnceleme düzeltmeleri tamamlandı; F1 manuel Unity kabulü bekliyor ·
Dal: feat/phase-6-hardening-release · Commit: 14

## Son incelemenin sonucu
Temiz proje testi, Unity 6 New Input System'de eski `UnityEngine.Input` kullanımının Play'de
fırlattığını buldu. `BasicMapCameraController` artık Old/New/Both koşullu yollarına sahip; hiçbiri
yoksa tek uyarı verip kendini kapatıyor. New Input scroll'u varsayılan normalize ±1 ile Windows'un
isteğe bağlı ham ±120 davranışını eşitliyor; kullanıcı bu hissi Unity'de elle doğrulayacak.

## Ne yapıldı
- Sunucu kapalı ve aktarım sırasında kopma test edildi; bozuk veri ve iptal önceki fazlarda kanıtlı
- `~110 KB/bölge` geçici tahmin TKMS decode, mesh upload, URL, istek nesnesi, disk kopyası ve
  viewport JSON kalemlerine ayrıldı
- Unity CI job'ı `if: false` taslaklandı; hiç çalıştırılmadı ve bu gerçek açıkça kaydedildi
- README clone→submodule→venv→fetch→LOD→publish→API→Unity tek akışına çevrildi
- Alternatif iddiaları resmi kaynaklara göre daraltıldı; CHANGELOG düzeltildi, paket `0.6.0` oldu
- 365.481 ham kapanışsız TKMS vertex ile normalizasyon sonrası kapanışlı 366.157 girdi ayrıştırıldı

## Doğrulama
| Kontrol | Sonuç |
|---|---|
| Her inceleme commit'i | `ruff check`, `ruff format --check`, `mypy`, **385 pytest geçti** |
| Temiz sunucu akışı | 81 bölge → `tr-adm1`; `/health` ve `/v1/datasets` başarılı |
| EditMode/PlayMode | Önceki Both koşusunda **77/49 geçti**; bu incelemede Unity çalıştırılmadı |
| New-only | Yalnız temiz derlendi; test koşusu ve manuel davranış doğrulaması yapılmadı |

**Tahsis ölçümü** (2.967 vertex / 29.702 bayt TKMS): decode **0 B**, mesh upload **0 B**,
URL **156 B**, `UnityWebRequest.Get` **143 B**, disk kopyası **32.768 B**, 50-id JSON
**1.188 B**; toplam ~**33 KB**, kalan ~77 KB Task/async ve gerçek ağ yolunda ölçülmedi.

## Kararlar
1. **Unity tabanı 6000.1** — manifest ve doğrulanmış ortam bu; 2022.3 desteği iddia edilmiyor
2. **Batching uygulanmadı** — 81 bölgede 83 draw call; chunk `CombineMeshes` V2 seçeneği
3. **Unity CI devre dışı** — lisans secret'ları olmadan çalıştırılmış gibi gösterilmiyor

## Bilinen sınırlar
- Disk cache toplam boyut/tahliye sınırı yok; istemci TKMB `entryEncoding: gzip` okumuyor
- Tekrarlanan cursor koruması yok; gerçek ADM2/ADM3 ve toplam Unity/GPU belleği ölçülmedi
- 2022.3 doğrulanmadı; Unity CI job'ı hiç çalıştırılmadı
- `TerritoryMapRenderer` 200+ id'yi tek batch'e gönderip API sınırını aşar; `ViewportStreamer`
  istekleri 200 benzersiz id'de parçalar
- Streaming tahsisinin kalan ~77 KB'ı ölçülmedi; upstream sadeleştirme issue'ları açılmadı

## Kabul ve yayın
Tek açık kabul F1'dir: kullanıcı New/Old backend zoom hissini Unity'de doğrulayacak. Sonrasında
`main`e merge, `v0.6.0` tag ve `[Unreleased]` içerikli GitHub Release yapılabilir.

## Değişen alanlar
README/CHANGELOG/faz raporları; Geometry API sözleşmesi; BasicMap input ve örnek kurulum;
paket sürümü, hata/tahsis testleri ve devre dışı Unity CI taslağı.
