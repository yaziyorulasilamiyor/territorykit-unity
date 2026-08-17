# Bağımsız İnceleme Briefi — TerritoryKit.Unity

Bu belge sana **projenin ne olduğunu** anlatır. Ne düşünmen gerektiğini anlatmaz.

---

## 1. Proje ne?

TerritoryKit, hiyerarşik ve düzensiz poligon bölgeler (ülke → il → ilçe → mahalle) için yazılmış açık kaynak bir TypeScript geospatial SDK'sıdır. Koordinattan bölge bulma, ebeveyn/çocuk hiyerarşisi, komşuluk grafiği ve görünür alan (viewport) tabanlı yükleme sağlar. Mevcut renderer adaptörleri MapLibre, Leaflet ve OpenLayers'dır — üçü de web tarayıcısı hedefler.

Bu proje, ekosistemde bulunmayan **oyun motoru adaptörünü** yazma girişimidir. İki parçadan oluşur:

**Geometry API (Python / FastAPI)**
Bölge poligonlarını okur, sunucu tarafında üçgenlere böler (triangulation), farklı detay seviyeleri (LOD) üretir ve sonucu ikili (binary) mesh formatında servis eder.

**Unity paketi (C#)**
Bu mesh'leri indirir, Unity `Mesh` nesnelerine dönüştürür, nesne havuzlaması yapar, kameranın gördüğü alana göre bölgeleri yükleyip boşaltır ve ekran tıklamasını bölge kimliğine eşler.

Yazarın belirttiği temel varsayım: triangülasyon ve topoloji korumalı basitleştirme CPU-yoğun işlerdir, bu nedenle mobil istemci yerine sunucuda bir kez yapılıp önbelleğe alınmalıdır.

---

## 2. Proje ne değil?

Kapsam dışı bırakıldığı beyan edilen şeyler:

- Kimlik doğrulama, kullanıcı hesabı, veritabanı
- Oyun mantığı (sahiplik, skor, fetih mekanikleri)
- Web arayüzü
- TerritoryKit'in kendisini değiştirmek

Bu bir **uygulama değil, kütüphane + servis**tir. Hedef kullanıcı: TerritoryKit verisini Unity'de göstermek isteyen bir oyun geliştiricisi.

---

## 3. Yazar kim, hedefi ne?

Junior seviyede bir geliştirici. Unity/C#, Kotlin/Android ve Java/Spring Boot geçmişi var; Python ve geometri algoritmaları onun için yeni alan. Projenin hedefleri: gerçek bir ekosistem boşluğunu doldurmak, açık kaynak katkı üretmek ve kütüphane tasarımı öğrenmek.

Bunu, kalite beklentisini düşürmen için değil, **geri bildirimini işe yarar bir seviyeye ayarlayabilmen** için yazıyorum. Yanlış olan şeye yanlış de.

---

## 4. Sana ne veriliyor?

Her fazın sonunda şunları alacaksın:

1. **Faz raporu** (`docs/phases/FAZ-N-RAPOR.md`) — o fazda ne yapıldığı, nasıl doğrulandığı, hangi kararların alındığı
2. **Kod tabanının ilgili kısmı** veya deposun tamamı

Fazlar sırasıyla: iskelet ve sözleşme → geometri motoru → LOD ve topoloji → HTTP API → Unity render → havuzlama ve akış → sağlamlaştırma ve yayın.

---

## 5. Senden ne isteniyor?

**Bağımsız, dürüst bir teknik inceleme.**

Yazarın kararlarını savunmak, gerekçelerini kabul etmek veya raporun çerçevesi içinde kalmak zorunda değilsin. Rapor bir iddiadır; senin işin onu doğrulamak ya da çürütmek.

### Bakabileceğin açılardan bazıları

Bu bir kontrol listesi **değil**. Başlangıç noktası. Kendi açını getir, buradakileri görmezden gelebilirsin.

- **Doğruluk** — geometri matematiği doğru mu? Testler gerçekten iddia ettikleri şeyi ölçüyor mu, yoksa sadece kodu çalıştırıyor mu?
- **Kenar durumları** — hangi girdiler bu kodu kırar? Yazar hangi durumu hiç düşünmemiş?
- **Sözleşme tasarımı** — binary format, API şekli, isimlendirme; bir yıl sonra geriye dönük uyumluluğu bozmadan genişletilebilir mi?
- **Performans** — iddia edilen sayılar anlamlı mı? Doğru şeyi mi ölçmüşler? Darboğaz sandıkları yerde mi?
- **Hata davranışı** — bir şey ters gittiğinde ne oluyor? Sessizce yanlış sonuç mu, yoksa net hata mı?
- **Karmaşıklık dengesi** — nerede gereğinden fazla mühendislik var, nerede yetersiz?
- **Bağımlılık ve taşınabilirlik** — seçilen kütüphaneler makul mü? Kilitlenme riski var mı?
- **Güvenlik** — girdi doğrulama, kaynak tüketimi, servis reddi (DoS) yüzeyi
- **Geliştirici deneyimi** — bu paketi ilk kez kuran biri 10 dakikada çalıştırabilir mi?
- **Belgelendirme dürüstlüğü** — dokümanlar kodun gerçekte yaptığı şeyi mi anlatıyor?
- **Yapılmayan seçimler** — hangi alternatif yaklaşım hiç değerlendirilmemiş ve neden daha iyi olabilirdi?

### Çıktı biçimi (öneri, zorunlu değil)

- **Kritik** — yanlış, bozuk veya tehlikeli olan şeyler
- **Önemli** — şimdi ucuz, sonra pahalı olacak şeyler
- **Küçük** — iyileştirme fırsatları
- **İyi yapılmış** — gerçekten iyiyse söyle; her şeye kusur bulmak zorunda değilsin
- **Emin olmadıkların** — neyi göremediğini, hangi bilgi eksik olduğu için yargı veremediğini belirt

---

## 6. İnceleme tarzı

- **Yağcılık yapma.** "Harika bir iş çıkarmışsınız" cümlesi bir bilgi taşımıyor
- **Ama gereksiz sertlik de yapma.** Amaç projeyi iyileştirmek, hata sayısı yarıştırmak değil
- **İddialarını gerekçelendir.** "Bu yanlış" değil, "bu yanlış çünkü X girdisinde Y olur"
- **Emin olmadığında emin olmadığını söyle.** Uydurulmuş kesinlik, en zararlı geri bildirim türü
- **Fazın kapsamına dikkat et** — ama kapsam dışı bir şey ciddi bir risk taşıyorsa yine de söyle, sadece "bu kapsam dışı ama önemli" diye işaretle

---

## 7. Bilmen gereken teknik bağlam

Yargını etkilememesi için minimum tutuldu; sadece kodu okuyabilmen için gerekenler:

- **Koordinatlar** enlem/boylam derecesinden düzlemsel metreye dönüştürülüyor, `float32` hassasiyeti nedeniyle bir başlangıç noktası (origin) çıkarılıyor
- **Binary format** `TKMS` adında özel bir formattır; spesifikasyonu `docs/mesh-format.md` içindedir
- **Komşu bölgeler sınır paylaşır.** Poligonları bağımsız basitleştirmek aralarında çatlak oluşturur; Faz 2 bu problemi ele alır
- **Unity mesh'lerinde** 65535 vertex sınırı vardır, bunun üstü 32-bit index gerektirir
- **Kaynak veri** kamuya açık idari sınır veri setlerinden gelir ve atıf gerektiren lisanslara tabidir

---

## 8. Tek cümlelik özet

*Bir geospatial SDK'nın eksik oyun motoru adaptörü yazılıyor: sunucu poligonları mesh'e çeviriyor, Unity paketi onları akıtıp gösteriyor — senden istenen, bunun gerçekten çalışıp çalışmadığına kendi gözünle bakman.*
