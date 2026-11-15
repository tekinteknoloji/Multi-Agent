"""Project team branches and optional role templates."""

BRANCHES = {
    "management": ("Yönetim", "İsteği netleştirir, yapılacak işleri belirler ve ekip çıktısını birleştirir."),
    "design": ("UI/UX", "Kullanıcı akışını, bileşen yapısını ve görsel sistemi tasarlar."),
    "engineering": ("Engineering", "Arayüz, servis ve veri katmanlarında kod yazar."),
    "quality": ("QA & Review", "Kod, test kapsamı ve güvenlik bulgularını değerlendirir."),
    "devops": ("DevOps", "İzole çalışma ortamını, derlemeyi ve dağıtımı hazırlar."),
    "context": ("RAG & Context", "Proje hafızasını, kaynak araştırmasını ve dokümantasyonu destekler."),
}

ROLES = {
    "supervisor": ("management", "Supervisor / Orchestrator", "member",
                   "Kullanıcının isteğini alt işlere ayır. Hangi dalın ne zaman çalışacağını belirle, bağımlılıkları belirt ve ekip çıktılarını birleştir."),
    "product": ("management", "Product Manager / Analist", "member",
                "İsteği netleştir. Kullanıcı hikâyelerini, öncelikleri ve ölçülebilir kabul kriterlerini yaz. Belirsizlikleri açıkça belirt."),
    "architect": ("management", "Sistem Mimarı", "plan",
                  "Proje geçmişini ve kapsamı incele. Klasör yapısını, teknik yaklaşımı, uygun kütüphaneleri ve veri modellerini belirle."),
    "wireframe": ("design", "UX Wireframe", "member",
                  "Sayfa akışlarını, etkileşim durumlarını ve bileşen hiyerarşisini çıkar. Kullanıcı yolculuklarını ve erişilebilirliği değerlendir."),
    "visual": ("design", "UI / Stil", "member",
               "Renk paletini, tipografiyi ve bileşen kurallarını belirle. Projenin tasarım sistemini ve varsa Tailwind kurallarını geliştir."),
    "frontend": ("engineering", "Frontend", "code",
                 "Arayüz kodunu ve durum yönetimini yaz. Mobil görünümü, klavye kullanımını ve hata durumlarını doğrula."),
    "backend": ("engineering", "Backend", "code",
                "API uç noktalarını, iş mantığını ve servis entegrasyonlarını yaz. Girdi doğrulamasını ve hata yönetimini test et."),
    "database": ("engineering", "Veritabanı", "code",
                 "SQL veya NoSQL şemalarını, migration dosyalarını ve sorguları hazırla. Veri bütünlüğünü ve geri alma adımlarını doğrula."),
    "reviewer": ("quality", "Code Reviewer", "review",
                 "Git farkını mimari standartlar, temiz kod ve kabul kriterlerine göre incele. Kanıtlı bulguları düzeltme için geliştiriciye ilet."),
    "testing": ("quality", "Test Mühendisi", "member",
                "Birim ve entegrasyon testlerini hazırla ve izin verilen araçlarla çalıştır. Hata günlüklerini analiz et ve kanıtın kapsamını belirt."),
    "security": ("quality", "Güvenlik Denetçisi", "member",
                 "SQL injection, sızmış anahtarlar ve yetki açıklarını incele. Kanıt, etkisi ve somut düzeltme önerisi ver."),
    "docker": ("devops", "Docker / Ortam", "member",
               "Dockerfile, Compose ve bağımlılık dosyalarını hazırla. Uygulamanın izole ortamda nasıl çalışacağını doğrula."),
    "deployment": ("devops", "CI/CD ve Deployment", "member",
                   "Derleme, test ve dağıtım adımlarını tasarla. Uygun erişim ve kullanıcı onayı varsa hedef ortama dağıt ve geri alma adımlarını kaydet."),
    "research": ("context", "Dokümantasyon Araştırmacısı", "member",
                 "Proje hafızası ve erişilebilir web veya vektör kaynaklarından güncel API dokümanını ve sürüm bilgisini bul. Kaynakları Engineering dalına ilet."),
    "writer": ("context", "Teknik Yazar", "member",
               "Proje ilerledikçe README, API dokümantasyonu ve kullanım kılavuzlarını güncelle. Onaylı kararları, mevcut davranışı ve test kapsamını ayır."),
}

LEGACY_BRANCH = {"plan": "management", "code": "engineering", "review": "quality", "member": "management"}


def assignment(member):
    branch = member.get("branch")
    if branch not in BRANCHES:
        branch = LEGACY_BRANCH.get(member.get("slot"), "management")
    role = member.get("role") or ""
    return {"branch": branch, "role": role, "branch_label": BRANCHES[branch][0],
            "role_label": ROLES[role][1] if role in ROLES else ""}


def catalog():
    return {
        "branches": [{"id": key, "label": label, "description": description}
                     for key, (label, description) in BRANCHES.items()],
        "roles": [{"id": key, "branch": branch, "label": label, "slot": slot, "duty": duty}
                  for key, (branch, label, slot, duty) in ROLES.items()],
    }
