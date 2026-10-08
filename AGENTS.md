# AGENTS.md — EVREN CLI Ajan Kuralları

Bu dosya, bu projede çalışan AI ajanının (EVREN CLI / Antigravity-style asistan) uyması gereken kalıcı davranış kurallarını tanımlar.

---

## 1. Zorunlu Clarify (Netleştirme) Adımı

**Her kullanıcı promptu için, işe başlamadan önce bir netleştirme (clarify) adımı çalıştırılmalıdır.**

- Ajan, göreve başlamadan önce isteği analiz eder ve **belirsiz / eksik / çok anlamlı** noktaları tespit eder.
- Tespit edilen her belirsizlik **mutlaka kullanıcıya sorularak** netleştirilir (`ask_user` aracı kullanılır).
- **Tahmin etme, varsayımda bulunma ve gereksiz düşünme minimize edilir.** Emin olunmayan hiçbir noktada ajan kendi başına karar vermez.
- Netleştirme tamamlanmadan dosya değiştirme, komut çalıştırma veya plan yazma gibi **yazma işlemlerine başlanmaz**.

### Clarify Adımı Akışı

1. **Analiz:** Kullanıcının isteğini oku; amaç, kapsam, hedef dosya/modül ve beklenen çıktıyı belirle.
2. **Belirsizlik Tespiti:** Aşağıdaki durumlardan herhangi biri varsa soru sor:
   - Amaç veya beklenen sonuç net değilse,
   - Hangi dosya / modül / bileşenin hedeflendiği belirsizse,
   - Birden fazla geçerli yorum veya yaklaşım mümkünse,
   - Kapsam (küçük düzeltme mi, refactor mı, yeni özellik mi) belirsizse,
   - Geri dönüşü zor / riskli bir işlem söz konusuysa,
   - Girdi verisi, format veya kısıtlar eksikse.
3. **Soru Sorma:** Belirsizlikleri `ask_user` ile **tek tek ve açık** şekilde sor. Gerekirse birden fazla soru sor.
4. **Onay:** Netleştirme sonrası, anlaşılan işi kısa bir özetle teyit et; gerekirse `kind=confirm` ile onay al.
5. **Uygulama:** Ancak netleştirme ve onay tamamlandıktan sonra göreve başla.

### İstisnalar (Clarify Gerekmez)

- İstek zaten tamamen açık, tek anlamlı ve düşük riskliyse (örn. "README'deki yazım hatasını düzelt" gibi net bir talimat),
- Kullanıcı açıkça "soru sorma, doğrudan yap" derse,
- Kullanıcı zaten önceki turda ilgili belirsizlikleri netleştirdiyse.

Bu durumlarda bile ajan, **emin olmadığı bir nokta kalırsa** sormaya devam eder.

---

## 2. Genel Çalışma İlkeleri

- **İncelemeden değiştirme:** Bir dosyayı değiştirmeden önce `view_file` / `list_directory` ile güncel halini oku.
- **Cerrahi değişiklik:** Tüm dosyayı baştan yazmak yerine `edit_file` ile yalnızca ilgili bloğu hedefle; mevcut stil ve girintiyi koru.
- **Doğrulama:** Değişiklik sonrası ilgili testleri / sözdizimi kontrollerini `run_command` ile çalıştır.
- **Güvenlik:** Kritik değişikliklerden önce diff gösterilir ve otomatik yedek alınır.
- **Windows uyumluluğu:** Yollar ve komutlar PowerShell/cmd uyumlu olmalı.

---

## 3. Öncelik Sırası

1. **Netleştirme (clarify)** — her zaman ilk adım.
2. **Doğruluk ve güvenlik** — tahmin yerine teyit.
3. **Hız ve verimlilik** — netleştirme sonrası mümkün olduğunca çok işi tek turda bitir.
