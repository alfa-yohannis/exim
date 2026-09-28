# Operasional

Panduan menjalankan sistem sehari-hari: pemasangan, indexing, penyiapan
backend, dan pemeliharaan.

---

## Pemasangan awal

```powershell
cd d:\ALFA\exim

# 1. Lingkungan virtual (bila belum ada)
python -m venv .venv

# 2. PyTorch dengan dukungan CUDA — WAJIB dari indeks khusus ini, jika tidak
#    yang terpasang adalah versi CPU dan embedding akan berjalan sangat lambat.
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128

# 3. Sisa dependensi
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 4. Verifikasi GPU terdeteksi
.\.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Keluaran langkah 4 seharusnya `True NVIDIA GeForce RTX 4060`.

---

## Menyiapkan Ollama

```powershell
winget install --id Ollama.Ollama -e
ollama pull qwen3:8b
ollama list
```

Ollama berjalan sebagai layanan latar belakang di `127.0.0.1:11434`. Periksa
kesiapannya kapan saja dengan:

```powershell
.\.venv\Scripts\python.exe main.py status
```

---

## Backend CLI (opsional)

Ketiganya memakai akun yang sudah Anda masuki pada perkakas masing-masing, jadi
tidak perlu kunci API.

| Backend | Perintah | Verifikasi |
|---|---|---|
| Claude Code | `claude` | `claude --version` |
| Codex | `codex` | `codex --version` |
| Antigravity | `agy` | `agy --version` |

Bila salah satu tidak berada di `PATH`, isikan jalur lengkapnya pada `.env`,
misalnya:

```
ANTIGRAVITY_CLI_PATH=C:\Users\Pradita\AppData\Local\agy\bin\agy.exe
```

---

## Indexing

### Proses pertama

```powershell
.\.venv\Scripts\python.exe main.py ingest
```

Proses pertama mengunduh model embedding (beberapa gigabita) lalu membaca
seluruh korpus. Untuk 707 berkas, perkirakan **20–40 menit**, hampir seluruhnya
untuk embedding di GPU.

Menjalankannya dari terminal — bukan dari tombol di antarmuka web — lebih
disarankan untuk proses pertama: kemajuannya tercetak per dokumen dan tidak
terpengaruh bila tab peramban ditutup.

### Pembaruan rutin

```powershell
.\.venv\Scripts\python.exe main.py ingest
```

Hanya berkas yang benar-benar berubah yang dibaca ulang. Pada korpus yang tidak
berubah, proses ini selesai dalam hitungan detik. Jalankan setiap kali ada
dokumen baru di shared drive.

### Bangun ulang penuh

```powershell
.\.venv\Scripts\python.exe main.py ingest --rebuild
```

Diperlukan setelah mengubah model embedding, parameter pemotongan, atau setelah
mengaktifkan OCR. Lihat [konfigurasi.md](konfigurasi.md#pengaturan-yang-memerlukan-rebuild).

### Bila indexing terputus

Tidak perlu tindakan khusus. Jalankan ulang `main.py ingest`; sistem
membandingkan katalog SQLite dengan arsip vektor, mendeteksi dokumen yang
kehilangan vektor, dan membacanya ulang secara otomatis.

---

## Menjalankan antarmuka web

```powershell
.\.venv\Scripts\python.exe main.py serve
```

Buka **http://127.0.0.1:8029**.

Selama pengembangan:

```powershell
.\.venv\Scripts\python.exe main.py serve --reload
```

### Diakses dari komputer lain

Secara bawaan server hanya mendengarkan di `127.0.0.1`, sehingga tidak dapat
dijangkau dari komputer lain. Untuk membukanya ke jaringan lokal:

```powershell
.\.venv\Scripts\python.exe main.py serve --host 0.0.0.0
# atau
.\run.ps1 -ServerHost 0.0.0.0
```

Saat dijalankan dengan `0.0.0.0`, program mencetak alamat IP komputer ini
sehingga rekan kerja tinggal membuka `http://<alamat-itu>:8029`.

Windows Firewall biasanya masih memblokir. Izinkan porta tersebut sekali saja
(jalankan PowerShell **sebagai Administrator**):

```powershell
New-NetFirewallRule -DisplayName "Exim RAG" -Direction Inbound `
  -LocalPort 8029 -Protocol TCP -Action Allow -Profile Private
```

Agar permanen, setel pada `.env`:

```
HOST=0.0.0.0
```

> **Setiap pengunjung wajib login** sebagai `admin` atau `guest`. Siapa pun yang
> memegang kata sandi `guest` dapat membaca seluruh dokumen akreditasi yang
> terindeks, jadi bagikan hanya ke tim. Gunakan `-Profile Private` seperti di atas agar aturan
> firewall tidak berlaku pada jaringan publik, dan matikan kembali ke
> `127.0.0.1` bila sedang tidak diperlukan.

### Bagian-bagian antarmuka

| Tab | Fungsi |
|---|---|
| **Tanya Jawab** | Pertanyaan dan jawaban mengalir, dengan sitasi yang dapat diklik |
| **Telusuri Potongan** | Melihat potongan yang ditemukan tanpa membuat jawaban — berguna untuk memeriksa mutu pencarian (admin saja) |
| **Daftar Dokumen** | Seluruh dokumen terindeks beserta statusnya (admin saja) |

Panel samping memuat status indeks, tombol indexing, pemilih backend, penyaring
folder, dan pengatur jumlah sumber. Panel **Perangkat** hanya tampil untuk
admin. Tombol **☰** di kiri baris tab menyembunyikan panel samping agar
percakapan memakai seluruh lebar layar; di ponsel panel itu menjadi laci yang
tertutup secara bawaan.

Bila koneksi terputus saat jawaban sedang dibuat, antarmuka menyambung kembali
dan melanjutkan jawaban dari bagian terakhir yang diterima. Lihat
[arsitektur.md](arsitektur.md#mengapa-jawaban-tidak-dibuat-di-dalam-respons-http).

Pada setiap kartu sumber tersedia **Tampilkan di folder**, **Buka dokumen**, dan
**Salin lokasi**. "Tampilkan di folder" adalah tindakan bawaan karena tidak
mungkin mengubah dokumen.

---

## Penggunaan dari terminal

```powershell
# Satu pertanyaan
.\.venv\Scripts\python.exe main.py ask "Berapa rata-rata beban EWMP DTPR pada TS?"

# Dibatasi ke satu folder
.\.venv\Scripts\python.exe main.py ask "Sebutkan publikasi 2025" --folder "3. Relevansi Penelitian"

# Memakai backend tertentu
.\.venv\Scripts\python.exe main.py ask "Ringkas kriteria 1" --backend claude_cli
```

---

## Pengujian

```powershell
# Seluruh suite
.\.venv\Scripts\python.exe -m pytest tests -v

# Dengan laporan cakupan
.\.venv\Scripts\python.exe -m pytest tests --cov=app --cov-report=term-missing

# Hanya pengujian jaminan baca-saja
.\.venv\Scripts\python.exe -m pytest tests/test_safety.py -v
```

Suite tidak memerlukan jaringan maupun unduhan model, dan selesai dalam beberapa
detik.

---

## Pemeliharaan

### Ukuran direktori data

```powershell
Get-ChildItem data -Recurse | Measure-Object -Property Length -Sum
```

Sebagian besar adalah cache model (beberapa gigabita). Indeks dan vektornya
sendiri hanya puluhan megabita.

### Mengosongkan dan memulai ulang

```powershell
Remove-Item data\index.db*, data\vectors.npz -Force
.\.venv\Scripts\python.exe main.py ingest
```

Cache model di `data\models` sengaja tidak dihapus agar tidak perlu mengunduh
ulang.

### Memperbarui salinan lokal

`corpus-lokal\data_dosen_sinta.xlsx` adalah salinan dari
`zdev\data_pendukung\` di shared drive. Bila berkas aslinya diperbarui, salin
ulang secara manual lalu jalankan `main.py ingest`.

---

## Keamanan

- Server hanya mendengarkan di `127.0.0.1` secara bawaan dan mewajibkan login
  (`admin` atau `guest`). Mengubah `HOST` berarti membuka korpus internal ke
  siapa pun di jaringan yang memegang kata sandi `guest`.
- `.env` dapat memuat kredensial dan sudah dikecualikan oleh `.gitignore`.
  Jangan menyimpan kata sandi dalam teks biasa bila dapat dihindari.
- Backend CLI mengirimkan kutipan dokumen ke penyedia layanannya. Untuk materi
  yang sensitif, gunakan backend `ollama`.
- Dokumen sumber tidak pernah diubah; lihat `tests/test_safety.py`.
