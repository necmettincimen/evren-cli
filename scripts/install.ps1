<#
.SYNOPSIS
    EVREN CLI (Antigravity-Style Assistant) Otomatik Kurulum Betiği
.DESCRIPTION
    Bu betik Python sanal ortamını (.venv) kurar, gerekli paketleri yükler,
    .env dosyasını hazırlar ve evren komutunu sisteme bağlar.
#>

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  EVREN CLI Kurulum Başlatılıyor (Windows PowerShell)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Python kontrolü
Write-Host "[1/5] Python sürümü kontrol ediliyor..." -ForegroundColor Yellow
$pyVersion = python --version 2>&1
Write-Host "Bulunan Python: $pyVersion" -ForegroundColor Green

# 2. Virtual Environment (.venv) oluşturma
Write-Host "[2/5] Sanal ortam (.venv) oluşturuluyor..." -ForegroundColor Yellow
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-Host "Sanal ortam başarıyla oluşturuldu." -ForegroundColor Green
} else {
    Write-Host "Mevcut .venv bulundu, kullanılıyor." -ForegroundColor Green
}

# 3. Bağımlılıkları yükleme
Write-Host "[3/5] Paketler yükleniyor (openai, httpx, rich, prompt_toolkit)..." -ForegroundColor Yellow
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m pip install -e .

# 4. .env dosyası hazırlığı
Write-Host "[4/5] Çevre değişkenleri (.env) denetleniyor..." -ForegroundColor Yellow
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host ".env dosyası oluşturuldu. Lütfen EVREN_API_KEY anahtarınızı .env içine ekleyin!" -ForegroundColor Magenta
} else {
    Write-Host ".env dosyası zaten mevcut." -ForegroundColor Green
}

# 5. Tamamlandı ve çalıştırma talimatı
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Kurulum Başarıyla Tamamlandı!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Kullanmak için:" -ForegroundColor White
Write-Host "  1. .env dosyasını açıp EVREN_API_KEY anahtarınızı girin:" -ForegroundColor White
Write-Host "     notepad .env" -ForegroundColor Gray
Write-Host "  2. Sanal ortamı aktif edin ve EVREN CLI'ı başlatın:" -ForegroundColor White
Write-Host "     .\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
Write-Host "     evren" -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Cyan
