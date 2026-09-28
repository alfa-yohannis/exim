<#
.SYNOPSIS
    Menyiapkan Exim RAG dari nol: virtual environment, PyTorch CUDA,
    dependensi, Ollama, dan model bahasa.

.DESCRIPTION
    Skrip ini aman dijalankan berulang kali. Langkah yang sudah selesai akan
    dilewati, sehingga dapat dipakai juga untuk memperbaiki pemasangan yang
    setengah jalan.

    Tidak ada satu pun langkah di sini yang menyentuh direktori sumber dokumen.

.EXAMPLE
    .\setup.ps1
    .\setup.ps1 -SkipOllama
#>

[CmdletBinding()]
param(
    # Lewati pemasangan Ollama dan pengunduhan model (mis. bila hanya ingin
    # memakai backend CLI).
    [switch]$SkipOllama,

    # Versi CUDA untuk wheel PyTorch. Ubah bila driver Anda lebih lama.
    [string]$CudaIndex = "cu128",

    # Model Ollama yang diunduh.
    [string]$OllamaModel = "qwen3:8b"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host ("  " + ("-" * $Message.Length)) -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  Exim RAG — penyiapan" -ForegroundColor White
Write-Host "  Direktori proyek: $projectRoot" -ForegroundColor DarkGray

# --- 1. Virtual environment --------------------------------------------------
Write-Step "1/5  Virtual environment"
if (Test-Path $python) {
    Write-Host "       sudah ada, dilewati"
} else {
    python -m venv (Join-Path $projectRoot ".venv")
    Write-Host "       dibuat di .venv"
}
& $python -m pip install --upgrade pip setuptools wheel --quiet
Write-Host "       pip diperbarui"

# --- 2. PyTorch dengan CUDA --------------------------------------------------
# Harus dari indeks khusus PyTorch. Bila dipasang dari PyPI biasa, yang didapat
# adalah versi CPU dan embedding akan berjalan berkali-kali lipat lebih lambat.
Write-Step "2/5  PyTorch ($CudaIndex)"
$torchReady = $false
try {
    $probe = & $python -c "import torch; print(torch.cuda.is_available())" 2>$null
    if ($probe -match "True") { $torchReady = $true }
} catch { }

if ($torchReady) {
    Write-Host "       sudah terpasang dengan dukungan CUDA, dilewati"
} else {
    Write-Host "       mengunduh (beberapa ratus MB, mohon tunggu)..."
    & $python -m pip install torch --index-url "https://download.pytorch.org/whl/$CudaIndex"
}

# --- 3. Dependensi lain ------------------------------------------------------
Write-Step "3/5  Dependensi Python"
& $python -m pip install -r (Join-Path $projectRoot "requirements.txt") --quiet
Write-Host "       selesai"

# --- 4. Ollama ---------------------------------------------------------------
Write-Step "4/5  Ollama"
if ($SkipOllama) {
    Write-Host "       dilewati atas permintaan (-SkipOllama)"
} else {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollama) {
        Write-Host "       memasang lewat winget..."
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
        $env:Path = "$env:LOCALAPPDATA\Programs\Ollama;" + $env:Path
    } else {
        Write-Host "       sudah terpasang di $($ollama.Source)"
    }

    $installed = (ollama list 2>$null | Out-String)
    if ($installed -match [regex]::Escape($OllamaModel.Split(":")[0])) {
        Write-Host "       model $OllamaModel sudah ada"
    } else {
        Write-Host "       mengunduh model $OllamaModel (~5 GB)..."
        ollama pull $OllamaModel
    }
}

# --- 5. Verifikasi -----------------------------------------------------------
Write-Step "5/5  Verifikasi"
& $python -c "import torch; print('       CUDA tersedia :', torch.cuda.is_available()); print('       GPU          :', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'tidak ada')"
& $python (Join-Path $projectRoot "main.py") status

Write-Host ""
Write-Host "  Selesai. Langkah berikutnya:" -ForegroundColor Green
Write-Host "    .\.venv\Scripts\python.exe main.py ingest     # bangun indeks" -ForegroundColor DarkGray
Write-Host "    .\run.ps1                                     # jalankan antarmuka web" -ForegroundColor DarkGray
Write-Host ""
