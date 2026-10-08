<#
.SYNOPSIS
    EVREN CLI - Sistem Geneli (Global) Kurulum Betiği
.DESCRIPTION
    Bu betik EVREN CLI'ı kullanıcı hesabına global olarak kurar. Kurulumdan sonra
    terminali hangi dizinde açarsanız açın, sadece 'evren' yazarak o dizinde
    otonom ajanı başlatabilirsiniz.

    Yaptığı işler:
      1. Python sürümünü doğrular.
      2. Paketi global Python ortamına (editable) kurar -> 'evren' komutu oluşur.
      3. Komutun PATH üzerinde erişilebilir olduğunu doğrular.
      4. Gerekirse PATH'e Python Scripts klasörünü ekler.
      5. .env dosyası yoksa oluşturur.

.PARAMETER Venv
    Bu anahtar verilirse global kurulum yerine proje içindeki .venv'e kurar.

.PARAMETER Uninstall
    Kurulu global 'evren' komutunu kaldırır.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\install-global.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\install-global.ps1 -Uninstall
#>

[CmdletBinding()]
param(
    [switch]$Venv,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

function Write-Header($text) {
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan
}

function Write-Step($text) {
    Write-Host $text -ForegroundColor Yellow
}

function Write-Ok($text) {
    Write-Host $text -ForegroundColor Green
}

function Write-Warn($text) {
    Write-Host $text -ForegroundColor Magenta
}

# ------------------------------------------------------------------
# KALDIRMA MODU
# ------------------------------------------------------------------
if ($Uninstall) {
    Write-Header "EVREN CLI Global Kaldırma"
    Write-Step "[1/2] Global 'evren' komutu kaldırılıyor..."
    python -m pip uninstall -y evren-cli
    Write-Ok "Paket kaldırıldı."
    Write-Step "[2/2] Doğrulama..."
    $stillThere = Get-Command evren -ErrorAction SilentlyContinue
    if ($stillThere) {
        Write-Warn "UYARI: 'evren' hala bulunuyor: $($stillThere.Source)"
        Write-Warn "Bu, farklı bir Python kurulumundan geliyor olabilir."
    } else {
        Write-Ok "'evren' komutu başarıyla kaldırıldı."
    }
    return
}

# ------------------------------------------------------------------
# KURULUM MODU
# ------------------------------------------------------------------
Write-Header "EVREN CLI Global Kurulum Başlatılıyor"

# 1. Python kontrolü
Write-Step "[1/5] Python sürümü kontrol ediliyor..."

# Global kurulumda 'py' launcher'ı tercih et: aktif .venv olsa bile
# gerçek sistem Python'unu bulur. Yoksa 'python' komutuna düşer.
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    $pyVersion = (py -3 --version 2>&1)
    $pyExe = (py -3 -c "import sys; print(sys.executable)" 2>&1).Trim()
} else {
    $pyVersion = (python --version 2>&1)
    $pyExe = (Get-Command python).Source
}
Write-Ok "Bulunan Python: $pyVersion"
Write-Host "Python yolu: $pyExe" -ForegroundColor Gray

# Python Scripts klasörünü tespit et (evren.exe buraya kurulur)
$pyDir = Split-Path -Parent $pyExe
$scriptsDir = Join-Path $pyDir "Scripts"
if (-not (Test-Path $scriptsDir)) {
    $scriptsDir = $pyDir  # Bazı kurulumlarda Scripts yoktur
}
Write-Host "Python Scripts dizini: $scriptsDir" -ForegroundColor Gray

# 2. Kurulum hedefini belirle
if ($Venv) {
    Write-Step "[2/5] Sanal ortam (.venv) modu seçildi..."
    $venvPath = Join-Path $ProjectRoot ".venv"
    if (-not (Test-Path $venvPath)) {
        python -m venv $venvPath
        Write-Ok "Sanal ortam oluşturuldu: $venvPath"
    }
    $targetPython = Join-Path $venvPath "Scripts\python.exe"
    $scriptsDir = Join-Path $venvPath "Scripts"
} else {
    Write-Step "[2/5] Global Python ortamı kullanılacak..."
    $targetPython = $pyExe
}

# Çalışan 'evren' süreçleri dosyayı kilitleyebilir; uyar.
$running = Get-Process -Name "evren" -ErrorAction SilentlyContinue
if ($running) {
    Write-Warn "UYARI: Çalışan 'evren' süreci bulundu (PID: $($running.Id -join ', '))."
    Write-Warn "Kurulumun başarılı olması için lütfen tüm 'evren' oturumlarını kapatın."
}

# 3. Bağımlılıkları ve paketi kur
Write-Step "[3/5] Bağımlılıklar ve paket kuruluyor..."
& $targetPython -m pip install --upgrade pip --quiet
& $targetPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt") --quiet
Push-Location $ProjectRoot
try {
    & $targetPython -m pip install -e . --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "HATA: Paket kurulumu başarısız oldu (çıkış kodu: $LASTEXITCODE)." -ForegroundColor Red
        Write-Warn "Olası neden: 'evren.exe' başka bir terminalde çalışıyor ve dosyayı kilitliyor."
        Write-Warn "Tüm 'evren' oturumlarını kapatıp betiği yeniden çalıştırın."
        $installFailed = $true
    }
} finally {
    Pop-Location
}
if ($installFailed) { exit 1 }
Write-Ok "Paket başarıyla kuruldu."

# 4. .env dosyası hazırlığı
Write-Step "[4/5] Çevre değişkenleri (.env) denetleniyor..."
$envFile = Join-Path $ProjectRoot ".env"
$envExample = Join-Path $ProjectRoot ".env.example"
if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Warn ".env dosyası oluşturuldu. Lütfen EVREN_API_KEY anahtarınızı girin!"
    }
} else {
    Write-Ok ".env dosyası zaten mevcut."
}

# 5. PATH kontrolü ve doğrulama
Write-Step "[5/5] PATH ve komut doğrulaması yapılıyor..."

# Kullanıcı PATH'inde scriptsDir var mı? (sondaki '\' ve büyük/küçük harf farkını yok say)
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$pathParts = $userPath -split ";" | Where-Object { $_ -ne "" }
$normalizedTarget = $scriptsDir.TrimEnd("\").ToLower()
$inPath = $false
foreach ($p in $pathParts) {
    if ($p.TrimEnd("\").ToLower() -eq $normalizedTarget) { $inPath = $true; break }
}

if (-not $inPath) {
    Write-Warn "'$scriptsDir' kullanıcı PATH'inde değil. Ekleniyor..."
    $newPath = ($userPath.TrimEnd(";") + ";" + $scriptsDir)
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    $env:Path = $env:Path + ";" + $scriptsDir
    Write-Ok "PATH güncellendi. (Yeni terminallerde geçerli olacak)"
} else {
    Write-Ok "'$scriptsDir' zaten PATH içinde."
}

# Komut gerçekten kuruldu mu? (hedef Scripts klasörünü kontrol et)
$evrenExe = Join-Path $scriptsDir "evren.exe"
if (Test-Path $evrenExe) {
    Write-Ok "'evren' komutu kuruldu: $evrenExe"
} else {
    Write-Warn "'evren.exe' beklenen konumda bulunamadı: $evrenExe"
}

# Bu oturumda hangi 'evren' önce çözümleniyor? (bilgi amaçlı)
$evrenCmd = Get-Command evren -ErrorAction SilentlyContinue
if ($evrenCmd) {
    Write-Host "Bu oturumda çözümlenen 'evren': $($evrenCmd.Source)" -ForegroundColor Gray
    if ($evrenCmd.Source -ne $evrenExe) {
        Write-Warn "Not: Bu oturumda farklı bir 'evren' öncelikli. Yeni terminalde doğru sürüm kullanılacak."
    }
} else {
    Write-Warn "'evren' komutu bu oturumda henüz görünmüyor."
    Write-Warn "Yeni bir terminal açın veya şu komutu çalıştırın:"
    Write-Host "  `$env:Path += `";$scriptsDir`"" -ForegroundColor Gray
}

# ------------------------------------------------------------------
# ÖZET
# ------------------------------------------------------------------
Write-Header "Kurulum Tamamlandı!"
Write-Host "Kullanım:" -ForegroundColor White
Write-Host "  1. .env dosyasına API anahtarınızı girin (yoksa):" -ForegroundColor White
Write-Host "     notepad `"$envFile`"" -ForegroundColor Gray
Write-Host ""
Write-Host "  2. Herhangi bir proje dizinine gidin ve 'evren' yazın:" -ForegroundColor White
Write-Host "     cd C:\Projeler\BenimProjem" -ForegroundColor Yellow
Write-Host "     evren" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Ajan otomatik olarak bulunduğunuz dizini çalışma alanı kabul eder." -ForegroundColor White
Write-Host "  Belirli bir dizini hedeflemek için: evren --workspace C:\Baska\Dizin" -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan
