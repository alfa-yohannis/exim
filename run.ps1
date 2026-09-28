<#
.SYNOPSIS
    Menjalankan antarmuka web Exim RAG.

.DESCRIPTION
    Pembungkus tipis atas `main.py serve` yang memakai interpreter dari virtual
    environment proyek, sehingga tidak perlu mengaktifkan environment lebih dulu.

.EXAMPLE
    .\run.ps1
    .\run.ps1 -Port 8080
    .\run.ps1 -Reload
    .\run.ps1 -Ingest
#>

[CmdletBinding()]
param(
    # Porta HTTP. Bila tidak diisi, memakai PORT dari .env.
    [int]$Port,

    # Alamat yang didengarkan. Bila tidak diisi, memakai HOST dari .env
    # (bawaannya 127.0.0.1). Isi 0.0.0.0 agar dapat diakses komputer lain.
    [string]$ServerHost,

    # Jalan pintas untuk -ServerHost 0.0.0.0.
    [switch]$Lan,

    # Muat ulang otomatis saat kode berubah (untuk pengembangan).
    [switch]$Reload,

    # Perbarui indeks lebih dulu, baru jalankan server.
    [switch]$Ingest,

    # Bangun ulang indeks dari nol lebih dulu, baru jalankan server.
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$entryPoint = Join-Path $projectRoot "main.py"

if (-not (Test-Path $python)) {
    Write-Host ""
    Write-Host "  Virtual environment belum ada." -ForegroundColor Red
    Write-Host "  Jalankan .\setup.ps1 lebih dulu." -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}

if ($Rebuild) {
    Write-Host ""
    Write-Host "  Membangun ulang indeks dari nol..." -ForegroundColor Cyan
    & $python $entryPoint ingest --rebuild
} elseif ($Ingest) {
    Write-Host ""
    Write-Host "  Memperbarui indeks..." -ForegroundColor Cyan
    & $python $entryPoint ingest
}

# Host dan port hanya diteruskan bila benar-benar diminta. Meneruskannya tanpa
# syarat akan menimpa HOST/PORT dari .env dengan nilai bawaan skrip ini, yang
# membuat `HOST=0.0.0.0` di .env tampak tidak berpengaruh.
$arguments = @($entryPoint, "serve")

if ($Lan) {
    $arguments += @("--host", "0.0.0.0")
} elseif ($PSBoundParameters.ContainsKey("ServerHost")) {
    $arguments += @("--host", $ServerHost)
}

if ($PSBoundParameters.ContainsKey("Port")) {
    $arguments += @("--port", $Port)
}

if ($Reload) { $arguments += "--reload" }

& $python @arguments
