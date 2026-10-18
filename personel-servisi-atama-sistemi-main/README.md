# Personel Servisi Atama Sistemi

Windows masaüstü personel servisi atama uygulamasının PySide6 ve SQLAlchemy tabanlı
proje iskeletidir.

Bu aşamada aşağıdaki altyapı hazırdır:

- Açılıp kapanabilen temel PySide6 ana penceresi
- AppData altında çalışan SQLite veritabanı
- SQLAlchemy model ve ilişki tanımları
- Ayrı engine, oturum ve repository katmanları
- Uygulama başlangıcında otomatik tablo oluşturma
- Konsol ve dönen dosya logları
- Veritabanı bütünlük testleri
- Personel, servis kapasitesi ve servis güzergâhı sütun eşleştirme altyapısı
- Arayüzden ve veritabanından bağımsız satır normalizasyonu ve doğrulaması
- Normalizasyon ve doğrulama birim testleri
- Excel çalışma kitabı ve dolu/boş sayfa okuma altyapısı
- Dosya türü tahmini ve değiştirilebilir sütun eşleştirme planı
- İş parçacığında çalışan doğrulama ve filtrelenebilir önizleme ekranı
- Servis kapasite kayıtları için aktarım öncesi özet ve çakışma seçimi
- Transaction ile yeni fiziksel servis/servis vardiyası ekleme ve kontrollü güncelleme
- Sicil numarası bazlı kontrollü personel ekleme ve profil güncelleme
- Personel listeleme, sicil arama, manuel kayıt, güncelleme ve aktif/pasif yönetimi
- Fiziksel servis listeleme, servis kodu arama, manuel kayıt ve aktif/pasif yönetimi
- Servis-vardiya listeleme, ekleme, güncelleme ve doluluk korumalı kapasite yönetimi
- Şoför listeleme, ekleme, güncelleme, aktif/pasif ve servis ilişkilendirme işlemleri
- Servis-vardiya bazlı sıralı güzergâh noktası yönetimi ve aktif/pasif işlemleri
- Güvenli servis-vardiya eşleştirmeli, tekrar çalıştırılabilir güzergâh Excel aktarımı
- Nominatim ile kullanıcı onaylı adres çözümleme ve manuel koordinat yönetimi
- Personel ve servis-vardiya güzergâhlarını gösteren OpenStreetMap harita ekranı
- OSRM ile sıralı güzergâh rota, mesafe ve tahmini süre hesaplama
- Haritada manuel veya adreslerden otomatik rota tasarımı ve kayıtlı rota yönetimi

Doğrulanmış Servis Kapasite ve Personel Excel kayıtları kullanıcı onayıyla
veritabanına aktarılabilir. Personel, fiziksel servis, servis-vardiya ve şoför kayıtları
uygulama içinden yönetilebilir. Servis güzergâhı Excel aktarımı da desteklenir. Otomatik
atama algoritması, raporlama, PyInstaller ve Inno Setup özellikleri henüz
geliştirilmemiştir.

## Gereksinimler

- Windows 10 veya Windows 11
- Python 3.11 veya daha yeni bir Python 3 sürümü

## İlk kurulum

VS Code içinde proje klasörünü açın ve PowerShell terminalinde sırayla çalıştırın:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Mevcut `.venv` başka bir Python kurulumuna bağlı olduğu için çalışmıyorsa klasörü
kaldırıp yukarıdaki sanal ortam oluşturma komutunu yeniden çalıştırın.

## Git ve veri güvenliği

Repository yalnızca uygulama kaynak kodunu ve testleri içermelidir. Personel Excel
dosyaları, proje PDF'leri, dışa aktarılan raporlar, SQLite veritabanları, loglar,
yedekler, `.env` dosyaları ve sanal ortam Git'e eklenmemelidir. Bu dosya türleri
`.gitignore` ile kapsam dışı bırakılmıştır.

Gerçek uygulama veritabanı ve loglar proje klasörünün dışında, kullanıcının
`%LOCALAPPDATA%\ServisAtama` klasöründe saklanır. Kaynak kodda parola, API anahtarı
veya kişisel personel verisi tutulmamalıdır.

## Uygulamayı çalıştırma

```powershell
.\.venv\Scripts\python.exe main.py
```

Uygulama açılırken eksik SQLAlchemy tabloları otomatik oluşturulur.

## Servis kapasite aktarımı

Servis Kapasite dosyasını seçip analiz ettikten sonra `Geçerli Servis Kayıtlarını
Aktar` butonunu kullanın. Aktarım öncesi özet penceresi yeni servisleri, yeni vardiya
kayıtlarını, çakışmaları, hataları ve uyarıları gösterir. Çakışmalarda varsayılan
seçenek mevcut kaydı koruyup Excel satırını atlamaktır; istenirse kapasite, rota adı
ve aktiflik bilgileri güncellenebilir. Aktarım tek transaction içinde yürütülür.

## Personel aktarımı

Personel dosyasında `SICIL_NO`, adres, vardiya ve yaka alanları doğrulandıktan sonra
`Geçerli Personel Kayıtlarını Aktar` butonu kullanılabilir. `AD` veya `SOYAD` boşsa
kayıt reddedilmez; uyarılı sayılır ve kullanıcı onayıyla sicil numarası üzerinden aktarılır.
Sicil çakışmasında varsayılan davranış mevcut personeli koruyup satırı atlamaktır;
istenirse iç kimlik ve sicil korunarak profil bilgileri güncellenebilir. Bu işlem
personeli bir servise atamaz ve atama tablosuna kayıt yazmaz.

## Personel yönetimi

`Personel Yönetimi` sekmesinde tüm personeller listelenebilir ve sicil numarasıyla
aranabilir. Yeni kayıtta sicil numarası zorunlu ve benzersizdir. Ad veya soyad boşsa
kullanıcı onayı isteyen bir uyarı gösterilir; onay verilirse kayıt yapılabilir. Mevcut
personelin sicil numarası ve veritabanı kimliği korunarak desteklenen profil bilgileri
güncellenebilir veya personel silinmeden aktif/pasif duruma alınabilir. Servis atamasını
etkileyebilecek alanlar değişirse yalnızca yeniden değerlendirme uyarısı gösterilir;
mevcut atama değiştirilmez.

Manuel kayıtta statü `KADROLU` veya `STAJYER` olarak seçilebilir. Staj başlangıç ve
bitiş tarihi alanları yalnızca `STAJYER` seçildiğinde gösterilir ve iki tarih de zorunlu
olur. Manuel oluşturulan kayıtların aktarım grubu kullanıcıya sorulmadan sistem
tarafından `MANUEL` olarak belirlenir.

## Servis, vardiya ve şoför yönetimi

`Servis Yönetimi` sekmesi dört ayrı bölümden oluşur:

- `Fiziksel Servisler`: Servis koduyla arama, yeni servis ekleme, şoför seçme ve
  aktif/pasif yönetimi sağlar. Servis kodu benzersizdir ve kayıt güncellenirken korunur.
  Yeni servis isteğe bağlı olarak ilk servis-vardiya kaydıyla aynı transaction içinde
  oluşturulur; iki kayıttan biri başarısız olursa işlem tamamen geri alınır.
- `Servis Vardiyaları`: Fiziksel servisten ayrı vardiya kayıtlarını yönetir. Aynı servis,
  vardiya ve yaka birleşimi ikinci kez oluşturulamaz. Kapasite pozitif tam sayıdır ve
  aktif personel doluluğunun altına indirilemez. Güncelleme mevcut atamaları değiştirmez.
- `Şoförler`: Ad, soyad, telefon, aktiflik ve fiziksel servis ilişkilerini yönetir. Kayıtlar
  silinmez; aktif/pasif yapılır. Aynı şoföre aynı vardiyada birden fazla aktif fiziksel
  servis görevi verilmesi engellenir.
- `Güzergâhlar`: Önce fiziksel servis, ardından servis-vardiya seçilir. Her servis-vardiya
  için il, ilçe, mahalle, nokta/durak adı, açık adres, açıklama ve isteğe bağlı koordinat
  bilgileri sıra numarasıyla tutulur. Noktalar silinmeden aktif/pasif yapılabilir; yukarı ve
  aşağı taşıma işlemleri sıraları yeniden 1'den başlayarak kesintisiz düzenler. İlk ve son
  kayıtlar başlangıç/bitiş olarak listede görünür. Bu ayrıntılar servis-vardiyanın kısa
  `rota_adi` bilgisini otomatik değiştirmez.

Bu ekranlar personeli servise atamaz, otomatik atama algoritmasını çalıştırmaz ve
`atama` tablosundaki mevcut kayıtları değiştirmez.

## Rota Tasarımı ve kayıtlı rota yönetimi

`Rota Tasarımı` ana sekmesi, mevcut `Harita ve Rota` ekranından bağımsız çalışır.
Manuel yöntemde haritaya tıklanarak sıralı ve sürüklenebilir kontrol noktaları eklenir;
ilk nokta kullanıcı tarafından belirlenen başlangıçtır. `Çizerek Oluştur` aracında sol
fare tuşu basılı tutularak yaklaşık iz çizilir. İz bırakıldığında OSRM Match ile yakındaki
sürülebilir yollara oturtulur; yola oturmayan veya kesintisiz eşleşmeyen çizim
reddedilir ve önceki rota korunur. Bir işaret `Nokta Sil` aracıyla tıklanarak veya her
modda sağ tıklanarak kaldırılabilir. `Yeni Rota` boş haritayı Mersin genelinde açar.
Otomatik yöntemde `acik_adres`,
`mahalle`, `sokak`, `cadde`, `ilce`, `il`, `enlem` ve `boylam` alanlarıyla sıralı
adres noktaları girilir. Koordinatı eksik adresler Aşama 10 Nominatim doğrulama
altyapısıyla çözülür. Uyuşmazlık sessizce kabul edilmez; kullanıcı onayı gerekir.

Her iki yöntemde de kontrol noktaları OSRM ile yollar üzerinden birleştirilir. Harita
noktaları sürüklenebilir, araya nokta eklenebilir, silinebilir ve yukarı/aşağı taşınabilir.
Noktalar değişince önceki hesap geçersiz olur ve kullanıcı rotayı yeniden hesaplar.
OSRM veya internet hatasında mevcut noktalar ve önceki çizim korunur. Bitiş noktası
`ayar` tablosundaki sabit Seçil Kauçuk varış noktasıdır.

Kaydetmeden önce rota adı, servis-vardiya, kapasite, mesafe, tahmini süre ve nokta
sayısını içeren özet onayı gösterilir. Rota GeoJSON geometrisi ve sıralı kontrol
noktalarıyla saklanır. `rota`, `rota_kontrol_noktasi` ve `servis_vardiya_rota`
tabloları eski güzergâh tablolarını değiştirmeden ayrı bir üst model oluşturur. Böylece
aynı fiziksel servisin farklı vardiyalarında farklı rotalar, aynı kayıtlı rotada ise
birden fazla servis-vardiya ilişkisi kullanılabilir.

Kullanıcı mevcut servis-vardiyaları seçebilir veya önerilen servis kodunu ayrıca
onaylayarak yeni fiziksel servis ve ilk vardiyasını oluşturabilir. Yeni servis,
servis-vardiya, rota, bağlantılar, kontrol noktaları ve geometri tek transaction içinde
kaydedilir; herhangi bir hata bütün işlemi geri alır. Kayıtlı rotalar aranabilir,
haritada açılabilir, güncellenebilir ve aktif/pasif yapılabilir. Atama geçmişi olmayan
rota veya servis kontrollü biçimde fiziksel silinir. Atama/geçmiş ilişkisi bulunan rota
silinmez ve pasif yapılır; servis silinmez, servis ile vardiyaları pasif yapılır.

Bu aşama personel ataması yapmaz, doluluk değiştirmez, personel biniş noktası veya
rotaya uzaklık hesaplamaz ve otomatik servis seçimi başlatmaz.

## Servis güzergâhı aktarımı

Servis Mahalle/Güzergâh dosyasını seçip analiz ettikten sonra `Geçerli Güzergâh
Kayıtlarını Aktar` butonunu kullanın. Servis kodu mevcut bir fiziksel servisle eşleşmelidir;
aktarırken yeni servis oluşturulmaz. Excel satırında vardiya/yaka belirtilmemişse güzergâh
noktası servisin bütün servis-vardiyalarına ayrı ayrı eklenir; belirtilmişse yalnızca bu
bilgilerle eşleşen vardiyalara eklenir. Her servis-vardiya aynı rota içinde birden fazla ilçe,
mahalle ve nokta içerebilir. Aynı servis-vardiya ve aynı konum bilgileri yeniden aktarılmaz.
Tüm eklemeler tek transaction içinde yapılır; bir hata olursa o aktarımda eklenen noktaların
tamamı geri alınır.

## Harita, adres çözümleme ve rota hesaplama

`Harita ve Rota` sekmesinde seçilen personel veya servis-vardiya güzergâhı haritada
gösterilebilir. Başlangıç, ara ve bitiş noktaları farklı renklerle işaretlenir. Harita
OpenStreetMap verisini kullanır ve harita üzerinde gerekli kaynak atfını gösterir.

Personel ve güzergâh noktalarının adres yapısı `acik_adres`, `mahalle`, `sokak`, `cadde`,
`ilce`, `il`, `enlem` ve `boylam` alanlarını destekler. Sokak ve cadde ayrı sütunlarda
saklanır; Excel'de bu sütunlar yoksa uygun değerler açık adresten ayrıştırılır. Eski birleşik
`sokak_cadde` alanı bulunan veritabanlarında kayıt silinmeden yeni alanlara güvenli aktarım
yapılır.

`Adresi Çözümle` ve `Seçili Noktayı Çözümle` işlemleri sorguları ayrıntılıdan genele doğru
tam adres ve yol alanları, sokak/cadde ve idari alanlar, mahalle-ilçe-il ve son olarak
kontrollü ilçe-il düzeyinde dener. Boş alanlar ve aynı ifadeler sorguda tekrarlanmaz. İlk
sonuç doğrudan kabul edilmez; farklı sorgulardan gelen adaylar adres bileşeni uyumu, sorgu
ayrıntısı ve sonuç hassasiyetine göre puanlanıp sıralanır. En iyi adayda alanlar eksikse
koordinat ters adres çözümlemesiyle yeniden kontrol edilir. Mahalle yine belirtilmiyorsa
mahalle ayrıca aranır ve koordinatın bulunan mahalle poligonu veya sınır kutusu içinde olup
olmadığı doğrulanır. Sonuç ekranında doğrulama durumu, uyum puanı, eşleşen, eksik ve
uyuşmayan alanlar açıkça gösterilir. İl, ilçe, mahalle, sokak veya cadde uyuşmazlığı ya da
eksikliği varsa alanlar sessizce değiştirilmez; kullanıcı mevcut bilgileri koruyabilir veya
bulunan alanları ayrıca kabul edebilir. Mevcut koordinatlar yalnızca kullanıcı yeniden
çözümlemeyi onaylarsa sorgulanır. Manuel enlem ve boylam girişi de desteklenir.

Personel adres alanlarından biri değiştiğinde mevcut koordinat `GUNCEL_DEGIL` durumuna
alınır. Güzergâh adresi, noktası, sırası, aktifliği veya koordinatı değiştiğinde hem nokta
koordinatı hem de servis-vardiya için saklanan rota uygun biçimde güncel değil olarak
işaretlenir. Excel'deki veya ilk aktarımdaki `sira_no` değerleri rota sırası kabul edilmez.
`Rotayı Hesapla`, yalnızca aktif ve güncel koordinatlı toplama noktaları ile sabit Seçil
Kauçuk fabrika varış noktasını OSRM süre matrisine gönderir. Uygulama küçük rotalarda kesin,
daha büyük rotalarda kontrollü sezgisel yöntemle serbest başlangıçlı ve sabit fabrika bitişli
toplama sırasını hesaplar. Optimize edilen sıra güzergâh noktalarının yeni `sira_no` sırası
olarak kaydedilir; ardından gerçek yol geometrisi alınır. Mesafe, tahmini süre ve GeoJSON
rota çizgisi servis-vardiya bazında transaction ile saklanır.

Her rotanın son noktası `Seçil Kauçuk`tur. Varsayılan merkez adresi Yunusoğlu Mahallesi,
Yunusoğlu Sokak No:177/A-177/B Tarsus/Mersin ve işletme pini `36.945107, 34.979814` olarak
`ayar` tablosuna yalnızca ayar eksikse eklenir. Harita ve Rota ekranındaki `Fabrika
Koordinatını Düzenle` işlemiyle koordinat düzeltilebilir; bu işlem mevcut rota geometrilerini
yeniden hesaplanmak üzere güncel değil durumuna getirir.

Toplu koordinat çözümleme tek worker ve tek istek akışıyla çalışır, istekleri saniyede en
fazla bir kez gönderir ve kullanıcı tarafından iptal edilebilir. Tek ve idari alanlarla
uyumlu sonuçlar kaydedilir; çoklu veya uyumsuz sonuçlar kullanıcı seçimi gerektiren uyarı
olarak bırakılır. Bir kaydın başarısız olması sonraki kayıtları durdurmaz. Nominatim'in
[kullanım politikası](https://operations.osmfoundation.org/policies/nominatim/) gereği
adres gönderiminden önce kullanıcı onayı alınır ve onaylanan sonuçlar yerel
`adres_onbellek` tablosunda önbelleğe alınır.

Servis adresleri kurulum yapılmadan ortam değişkenleriyle değiştirilebilir:

```powershell
$env:SERVIS_ATAMA_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
$env:SERVIS_ATAMA_OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
$env:SERVIS_ATAMA_OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/driving"
$env:SERVIS_ATAMA_OSRM_MATCH_URL = "https://router.project-osrm.org/match/v1/driving"
$env:SERVIS_ATAMA_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
$env:SERVIS_ATAMA_USER_AGENT = "PersonelServisiAtamaSistemi/1.0"
```

İnternet bağlantısı, timeout veya geçersiz servis cevabı uygulamayı kapatmaz. Kullanıcıya
Türkçe hata gösterilir; teknik ayrıntı log dosyasına yazılır ve loglara tam adres eklenmez.

## Personel-rota uygunluğu ve atama önizleme (Aşama 12)

`Personel Atama` sekmesi tek sicil, tek/çoklu personel seçimi veya aktif ataması olmayan
tüm aktif personel için önce salt-okunur bir atama önizlemesi üretir. Değerlendirme
düğmeleri tek başına atama yapmaz. Kalıcı kayıt yalnızca `Seçili Öneriyi Atamayı Onayla`
veya `Tüm Uygun Önerileri Atamayı Onayla` düğmesinden sonra ikinci onay verilirse oluşur.

Değerlendirme yalnızca aktif servis, aktif servis-vardiya ve aktif kayıtlı rota
bağlantılarını kullanır. Vardiya ve yaka türü birebir eşleşir. Stajyerin başlangıç ve
bitiş tarihleri değerlendirme gününde geçerli olmalıdır. Personel koordinatı ile kayıtlı
rotanın bütün `LineString` geometrisi arasındaki en kısa mesafe hesaplanır; yalnızca rota
kontrol noktalarına uzaklık ölçülmez. Çizgi üzerindeki en yakın koordinat önerilen biniş
noktasıdır. Varsayılan yakınlık sınırı 1500 metredir, `ayar` tablosunda saklanır ve ekrandan
değiştirilebilir.

Toplu önizlemede mevcut aktif doluluk ile önizleme içi koltuk rezervasyonları birlikte
izlenir; kapasite hiçbir zaman aşılmaz ve aynı koltuk iki personele önerilmez. Kadrolu
personel stajyerden önce değerlendirilir. Uygun adaylar mesafe, kapasite, kalan kapasite ve
servis koduyla kararlı biçimde sıralanır. Sonuç tablosu önceki/sonraki doluluğu, kalan
kapasiteyi, uzaklığı, biniş noktasını, atanamama nedenini ve alternatif rotaları gösterir.
Seçili öneri haritada rota, personel, biniş noktası ve aradaki kesikli bağlantıyla açılır.
Kapasite yetersizliği ayrı özetlenir; otomatik servis oluşturulmaz.

Onaylanan atama `atama` tablosunda personel, servis-vardiya, seçilen kayıtlı rota,
rotaya uzaklık ve en yakın biniş koordinatıyla tek transaction içinde saklanır. Kayıt
anında aktiflik, vardiya/yaka, rota geometrisi, yakınlık sınırı, mevcut atama ve kapasite
yeniden doğrulanır; herhangi bir hata bütün onay grubunu geri alır. Servis Yönetimi >
Servis Vardiyaları ekranında doluluk ve seçili servis-vardiyaya atanmış personeller görünür.

Uygulama adı `Seçil Kauçuk Personel Servisi Atama Sistemi`dir. Sağ üst marka alanı,
pencere ikonu ve Windows görev çubuğu ikonu `assets/secil_kaucuk.jpeg` dosyasını kullanır.
Verilen SVG dosyası JPEG verisini sarmaladığı ve Qt'de güvenilir çizilmediği için doğrudan
görüntülenebilen JPEG tercih edilmiştir.

## Testleri çalıştırma

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Testler geçici bir SQLite veritabanı kullanır; gerçek uygulama veritabanına örnek
personel veya servis kaydı eklemez.

## Yerel veri dosyaları

```text
%LOCALAPPDATA%\ServisAtama\
|-- data\
|   `-- servis_atama.db
`-- logs\
    `-- servis_atama.log
```

## Veritabanı tabloları

- `personel`
- `sofor`
- `servis`
- `servis_vardiya`
- `servis_guzergah_noktasi`
- `servis_guzergah_geometrisi`
- `rota`
- `rota_kontrol_noktasi`
- `servis_vardiya_rota`
- `atama`
- `adres_onbellek`
- `ayar`

## Proje yapısı

```text
personel_servisi_atama_sistemi/
|-- main.py
|-- requirements.txt
|-- README.md
|-- app/
|-- database/
|   |-- connection.py
|   |-- session.py
|   |-- init_db.py
|   |-- models/
|   `-- repositories/
|-- excel_import/
|-- normalization/
|-- services/
|-- tests/
`-- ui/
```
