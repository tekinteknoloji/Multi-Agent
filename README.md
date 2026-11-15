# Multi-Agent

Multi-Agent, bir yazılım projesinde yerel modelleri ve kodlama ajanlarını aynı web panelinden yönetir. Panoda ekip görüşlerini alabilir, bir planı düzenleyip denetletebilir ve onayladığın kapsamı geliştirmeye gönderebilirsin. Panel kod değişikliğini ayrı bir Git kopyasında üretir, bağımsız testi ve denetimi çalıştırır, ardından kaynak projeye uygulama kararını sana bırakır.

## Hızlı başlangıç

1. Python ve Git kur. Yerel model kullanacaksan Ollama'yı çalıştır ve en az bir metin modeli yükle. Örnek yerel model: `ollama pull qwen3:8b`.
2. Windows'ta [Multi-Agent-Ac.bat](Multi-Agent-Ac.bat) dosyasını aç. Komut satırıyla başlatmak için proje klasöründe `python web_app.py --open` çalıştır. Panel varsayılan olarak `http://127.0.0.1:18765` adresini kullanır.
3. Panelde **Proje klasörü** alanına çalıştıracağın projenin tam yolunu yaz. Var olan projede **Projeyi aç**, boş klasörde **Boş proje hazırla** seç. Boş proje oluştururken üst klasörün var olması gerekir.
4. **+ Ajan ekle** ile ekip kur. Bir ajanı otomatik **Geliştirme**, bir diğerini **Denetim** adımına ata. Plan oluşturmak veya tam akışı kullanmak için ayrıca **Planlama** ataması yap.
5. Görevi yaz, gerekiyorsa test komutunu **Test ayarları** altında gir ve **Doğrudan geliştir**, **Plan oluştur** veya **Tüm akışı başlat** seçeneklerinden birini kullan.

### Codex ve yerel denetçiyle örnek

| Ayar | Geliştirici | Denetçi |
| --- | --- | --- |
| Sağlayıcı | Codex CLI | Ollama |
| Dal | Engineering | QA & Review |
| Hazır rol | Backend | Code Reviewer |
| Otomatik akış ataması | Geliştirme | Denetim |
| Model | CLI varsayılanı için boş | Kurulu bir yerel metin modeli |

Codex CLI'nin çalıştığı kullanıcı hesabında oturum açık olmalı. Kurulumu `codex login status` ile kontrol edebilirsin. Windows'ta panel Codex'i MXC sandbox ile çalıştırır. Ollama'daki modelleri panelde **Model listesini yenile** ile görebilirsin.

Boş bir örnek projede şu görevi verebilirsin:

> hesap.py dosyasına iki sayıyı çarpan carp(a, b) işlevini ekle. Yalnız bu dosyayı değiştir.

**Test ayarları** alanına komutu ve her argümanı ayrı satırda gir:

```text
python
-B
-c
from hesap import carp; assert carp(3, 4) == 12; assert carp(-3, 4) == -12; assert carp(0, 4) == 0
```

**Doğrudan geliştir** ile başlat. **İncelemeye hazır** sonucunda Git farkını ve test çıktısını incele. Kaynak klasöre yazmak için **İncele ve uygula → Onayla ve uygula** seç.

## Ekip ve pano

| Dal | Hazır roller |
| --- | --- |
| Yönetim | Supervisor / Orchestrator, Product Manager / Analist, Sistem Mimarı |
| UI/UX | UX Wireframe, UI / Stil |
| Engineering | Frontend, Backend, Veritabanı |
| QA & Review | Code Reviewer, Test Mühendisi, Güvenlik Denetçisi |
| DevOps | Docker / Ortam, CI/CD ve Deployment |
| RAG & Context | Dokümantasyon Araştırmacısı, Teknik Yazar |

Her ajanın adı, çağrı etiketi, rol metni, sağlayıcısı ve modeli ayrı ayarlanır. Hazır görev metnini düzenleyebilirsin. Panel Ollama, Codex CLI, Claude Code CLI, OpenAI uyumlu API ve Anthropic API sağlayıcılarını destekler. API bağlantıları için panelde anahtarın kendisi yerine ortam değişkeninin adını gir. Rol şablonlarının yapabileceği işlemler seçilen sağlayıcının araçlarına ve erişimine bağlıdır.

- **Panoya yaz** ile not veya ek bırak. **Yaz ve ajanlara ilet** ile yanıt iste. Bir ajanı `@etiket` ile çağırabilir veya **Yanıtla** ile bir mesajı alıntılayabilirsin.
- Etiketsiz isteklerde etkin bir **Supervisor / Orchestrator** varsa yönlendirmeyi o üstlenir. “Önce UI/UX, ardından QA & Review” gibi açıkça yazılan dal sırası korunur. Yanıtlar geldikten sonra **Ekibi özetle** aynı isteğe verilen katkıları birleştirir.
- **Taslağa al** bir pano mesajını görev alanına taşır. **Kod turlarında Git farkını ve test kanıtını izle** seçeneği, ajanı kod ve test çıktısına ek gözlemci olarak çağırır.
- Üst çubuktan **Açık**, **Koyu** veya **Sistem** temasını seçebilirsin.

## Proje hafızası ve test kanıtı

- Projeye bağlı sohbetler, ekler, ekip ayarları ve raporlar kaynak klasördeki `Proje-Hafizasi/` altında saklanır. Klasörü projeyle birlikte taşıyabilir veya seçili konuyu **Hafızayı indir** ile ZIP olarak alabilirsin.
- Ajanın önerisi, sen **Kararı onayla** dediğinde geçerli proje kararına dönüşür. Yeni bir karar öncekinin yerine geçecekse onay sırasında eski kaydı seç. Pano güncel ve değiştirilmiş kararları kaynak mesajlarıyla gösterir.
- Ajan çağrısına ilgili kısa geçmiş, onaylı kararlar ve test kanıtı eklenir. Panel test çıktısının yanında komutu, çıkış kodunu, başlangıç commit’ini ve Git farkının özetini saklar. Test raporu sorgusunda ajan kayıtları seçer, panel kapsamı kayıttan gösterir.

## Uygulama, geri alma ve kesintiden devam

Panel geliştirmeye kaynak projenin commit edilmiş `HEAD` sürümünden başlar. Test veya denetim düzeltme isterse aynı Git kopyasında düzeltme turu açılır. İlk turun ardından en fazla iki otomatik düzeltme yapılır. Git farkı, test sonucu ve denetim kararı panelde görülebilir.

Onaylanan fark kaynak çalışma ağacına yazılır. **Geri al** son uygulamayı tersine çevirir. **Yeniden uygula** geri alınan farkı tekrar yazar. Panel kaynak dalı, commit’i ve bekleyen dosya değişikliklerini kontrol eder. Commit ve push ayrı Git işlemleridir.

Paneli aynı portta yeniden açtığında son görev geri gelir. Geliştirme ve test işlemleri panelden bağımsız çalışabilir. Kesilen denetim, kayıtlı Git farkı ve test sonucuyla devam eder. Uygulama, geri alma veya yeniden uygulama kesilirse panel işlem kaydını ve kaynak dosyaların durumunu karşılaştırır. Tamamlanmış Git işlemi ikinci kez yürütülmez.

## Geliştirme ve doğrulama

```sh
python -m unittest discover -s tests -v
```

- `web_app.py` yerel paneli ve görev durumunu yönetir.
- `team.py` Git, geliştirme, test ve denetim akışını yürütür.
- `discussion.py`, `evidence.py` ve `agent_roles.py` pano, kanıt ve ekip kayıtlarını işler.
- `providers.py` model sağlayıcılarını bağlar. `web/` tek sayfalık arayüzü içerir.

Panel yerel kullanım için tasarlandı. Harici sağlayıcı seçersen görev ve ilgili bağlam o sağlayıcı tarafından işlenebilir. Test komutları Git kopyasında, paneli başlatan kullanıcının işletim sistemi yetkileriyle çalışır. `runs/`, proje hafızası ve yerel ortam dosyaları yayınlanan kaynak koddan ayrı tutulur.
