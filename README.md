# FX Volatilite ve Gamma Exposure Paneli

Bloomberg'den Excel ile çekilen opsiyon verisini okuyup, günlük bir volatilite
rejimi okuması ve hisse/endeks bazında gamma exposure grafiği üreten yerel bir
Streamlit uygulaması.

Tamamen **çevrimdışı** çalışır: broker bağlantısı yok, emir iletimi yok, hiçbir
ağ çağrısı yok. Sadece okuma ve analiz.

```bash
python -m streamlit run C:\Users\zorbey.caliskan\fxvol_dashboard\app.py
```

---

## 1. Günlük akış

1. Excel dosyasını aç: `Desktop\FX_Vol_Dashboard_Bloomberg.xlsx`
2. Bloomberg'in hesaplamayı bitirmesini bekle. **Chain** sekmesindeki durum
   hücresi (P sütunu, sağ üst) `READY - safe to export` yazana kadar bekle.
3. **Export** sekmesini CSV olarak kaydet.
4. Uygulamayı çalıştır, sol taraftaki **Upload** alanına CSV'yi bırak.

Tek dosya hem FX anlık görüntüsünü hem de bütün opsiyon zincirlerini taşır.
İki ayrı yükleme gerekmez.

---

## 2. Arayüz

### Sekme 1 — Gamma exposure

Her dayanak varlık için ayrı alt sekme. Sırasıyla:

- **Gamma grafiği**: her kullanım fiyatında (strike) dealer gamması. Mavi
  pozitif, kırmızı negatif. Spot, call wall, put wall ve flip çizgileri işaretli.
- **Call wall** = direnç, **Put wall** = destek, **Gamma flip** = rejimin
  değiştiği fiyat, **Net gamma** = toplam.
- **Call side vs put side**: gamma ve açık pozisyonun hangi tarafta ağır
  olduğunu gösteren tilt (−1 ile +1 arası) ve ikinci bir grafik.
- En altta bütün dayanak varlıkların özet tablosu.

Kapsanan varlıklar: **SPX, NDX, SPY, QQQ, NVDA, META, GOOG, MRVL, SMH, VST,
TSM, SOFI, NVO** (ve zincir çekilebilirse EURUSD).

### Sekme 2 — FX pairs

**EURUSD**, **GBPUSD** ve **EURGBP** için ayrı alt sekmeler. Her biri:

- Risk reversal'dan gelen **pozisyon eğilimi** başlığı (yön tahmini değil).
- **Yüzdelik şeridi**: IV, RV ve RR'nin bugünkü yüzdelik dilimi, düşük/orta/
  yüksek bantları üzerinde.
- **Son 10 gün IV ve RV** aynı grafikte, aradaki fark gölgeli.
- Rejim bandı, günün beklenen aralığı ve stop merdiveni, pozisyon büyüklüğü,
  vade yapısı, geçmiş grafikleri.

---

## 3. Birimler — en kritik konu

| Birim | Anlamı |
|---|---|
| **Yüzde puanı** | Bütün volatilite değerleri. `6.23` = %6.23, 0.0623 değil. |
| **Vol puanı** | İki volatilite arasındaki *fark*. RR ve BF bu birimdedir. |
| **Yüzdelik (percentile)** | 0–1 arası kesir. |
| **Pip** | EURUSD için 0.0001, JPY paritelerinde 0.01. |
| **Gamma** | %1'lik harekete karşılık nominal tutar. |

---

## 4. Kavramlar

**Gamma exposure (GEX).** Dealer'ların hedge işlemlerinin fiyatı hangi yöne
ittiğini ölçer.

- **Long gamma**: yükselişte satar, düşüşte alırlar → fiyat sakinleşir, bantta
  kalır, kırılımlar zayıf olur.
- **Short gamma**: yükselişte alır, düşüşte satarlar → hareketler büyür,
  trendler uzar, stop'lar daha geniş olmalı.
- **Gamma flip**: ikisi arasındaki sınır fiyat. Üstünde long, altında short.

**Yön değil, davranış.** Gamma fiyatın nereye gideceğini söylemez; hareket
başladığında *nasıl* davranacağını söyler.

**Dealer varsayımı.** Tüm hesap, dealer'ların call'larda long ve put'larda short
olduğu varsayımına dayanır. Bu piyasa standardıdır ama kimse gerçek dealer
pozisyonunu göremez. Varsayım ters dönerse destek ve direnç yer değiştirir.
Endeks ürünlerinde (SPX, NDX, SPY) daha güvenilir, yoğun bireysel call alımı
olan tek hisselerde daha az güvenilir.

**Risk reversal (pozisyon eğilimi).** 25 delta call volatilitesi eksi put
volatilitesi. Negatif = put'lar daha pahalı = piyasa düşüş korumasına para
ödüyor. Bu, **zaten alınmış pozisyonu** gösterir, tahmin değildir. Skor %35
seviye + %65 beş günlük değişimden oluşur; ikisi ters yöne işaret ederse
başlık bunu açıkça yazar.

---

## 5. Excel dosyası

| Sayfa | İçerik |
|---|---|
| **Chain** | 14 dayanak varlık için opsiyon zinciri. Sarı hücreler girdidir. Durum hücresi çekimin bitip bitmediğini söyler. |
| **Pairs** | Üç FX paritesi için canlı veri (spot, bid/ask, vade yapısı, yüzdelikler, türetilmiş alanlar). |
| **PairHist** | Yüzdelik ve z-skor hesabı için pariteye özel BDH geçmişi. |
| **Surface** | Tam volatilite yüzeyi (ATM, 25R, 25B, 10R, 10B × 8 vade). |
| **Export** | CSV olarak kaydedilen sayfa: 5–7. satırlar pariteler, 10. satırdan itibaren zincir. |
| Dashboard / History / Notes | Orijinal tek parite ekranı ve ticker referansı. |

### Chain sekmesi girdileri

- **Bloomberg ticker** — örn. `SPX Index`, `SPY US Equity`
- **Contract multiplier** — hisse/ETF 100, CME EUR future 125000
- **Option expiry** — ayın üçüncü cuması. Her vadeden sonra ileri al.
- **Strikes each side** — **adet**, yüzde değil. SPX 5 puan aralıklı olduğu
  için 160; NVDA 5 dolar aralıklı olduğu için 40 yeterli. Hedef: spotun
  en az %5 altı ve üstü.

### Bloomberg alan notları

- `CHAIN_TICKERS` hem future hem hisse için çalışır. `OPT_CHAIN` sadece
  hissede çalışır, future'da `Field Not Applicable` döner.
- `CHAIN_TICKERS` ticker'ları **sarı tuş olmadan** döndürür; sayfa 10. satırda
  türetip ekler (`Equity`, `Index`, `Curncy`).
- Implied vol boş gelirse `IVOL_MID` yerine `OPT_IMPLIED_VOLATILITY_MID` dene.

---

## 6. CSV sözleşmesi

Parser iki ayrı blok okur; başlık satırlarını kendisi bulur, üstteki ve
alttaki açıklama satırlarını sessizce atar.

**Anlık görüntü bloğu** — `date` ile başlayan başlık, parite başına bir satır.
65 sütun: temel alanlar, bid/ask, türetilmiş sigma ve stop'lar, ve 38 sütunluk
volatilite yüzeyi.

**Zincir bloğu** — `strike` ve `option_type` içeren başlık:

| Sütun | Anlamı |
|---|---|
| `underlying` | Varlık etiketi (SPX, NVDA…) |
| `option_ticker` | Bloomberg ticker |
| `strike` | Kullanım fiyatı |
| `option_type` | C / P |
| `open_interest` | Açık pozisyon |
| `iv` | Implied vol, yüzde puanı |
| `days_to_expiry` | Vadeye kalan gün |
| `underlying_price`, `multiplier` | Dayanak fiyatı ve çarpan |

### Dil ve ayraç

Her iki Excel biçimi de otomatik tanınır:
- `;` ayraç + `,` ondalık (Türkçe Windows'ta "Farklı Kaydet → CSV")
- `,` ayraç + `.` ondalık (VBA makro çıktısı)

---

## 7. Otomatik kontroller

Uygulama sessizce yanlış sonuç üretmek yerine uyarır:

| Kontrol | Ne zaman uyarır |
|---|---|
| **Eksik çekim** | Bir varlıkta 5'ten az strike varsa — Bloomberg çekimi bitmemiştir. Grafik çizilmez. |
| **Bayat fiyat** | Fiyat, zincirin strike dağılımının dışındaysa. |
| **Dar pencere** | Strike penceresi spotun %5'inden azını kapsıyorsa. Günler arası karşılaştırma güvenilmez olur. |
| **Excel doğrulaması** | Python yeniden hesaplar; Excel'den %1'den fazla saparsa alan adıyla birlikte uyarır. |
| **Eski veri** | Anlık görüntü bir iş gününden eskiyse. |

---

## 8. Kod yapısı

```
fxvol/
  config.py    sütun sözleşmesi, pip kuralları, yollar
  io.py        CSV ayrıştırma, dil tanıma, Parquet deposu
  vol.py       sigma, beklenen hareket, yüzdelik, smile, vade yapısı
  gamma.py     Black-Scholes gamma, GEX, duvarlar, flip, veri kontrolleri
  signals.py   rejim, pozisyon eğilimi, başlık, pozisyon büyüklüğü
  charts.py    yüzdelik şeridi ve IV/RV grafiği
app.py         sadece arayüz — hesaplama yok
tests/         118 test
data/
  incoming/    günlük CSV'ler buraya
  store/       snapshots.parquet, her çalıştırmada yeniden kurulur
  sample/      test verisi
excel/         Bloomberg çalışma kitabının kopyası
```

`fxvol/` paketi **streamlit import etmez**; hesaplama mantığı başka bir araçtan
da kullanılabilir.

```bash
python -m pytest
```

---

## 9. Sık karşılaşılan durumlar

**Bütün grafikler boş, "incomplete pull" yazıyor.** Excel kaydedilirken
Bloomberg çekimi bitmemiş. Chain sekmesinde durum hücresi READY olana kadar
bekleyip tekrar kaydet.

**Gamma rejimi bir günde long'dan short'a döndü.** Önce strike penceresini
kontrol et. Pencere darsa fiyat hareket ettikçe farklı strike'lar görünür ve
okuma pozisyon değişmeden de değişir.

**Bir hissede put duvarı sıfır görünüyor.** Pencere yukarı kaymıştır; put açık
pozisyonu genelde daha aşağıdaki strike'larda olur. O varlık için strike
sayısını artır.

**EURUSD'de gamma yok.** EURUSD spot tezgahüstüdür, listelenmiş zinciri ve açık
pozisyonu yoktur. Gamma okuması için CME EUR future opsiyonu gerekir; ticker'ı
`OMON` ekranından alıp Chain sekmesine yazmak gerekiyor.

---

## 10. Kapsam dışı

Emir iletimi, broker API'si, canlı veri akışı, backtest motoru, kimlik
doğrulama, sunucuya kurulum. Bunlardan biri gerekiyorsa önce konuşalım.

```bash
python -m streamlit run C:\Users\zorbey.caliskan\fxvol_dashboard\app.py
```
