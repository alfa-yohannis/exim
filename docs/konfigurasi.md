# Konfigurasi

Seluruh pengaturan dibaca dari variabel lingkungan atau dari berkas `.env` di
akar proyek. Setiap nilai memiliki bawaan yang masuk akal, sehingga `.env`
kosong pun berjalan benar. Salin `.env.example` sebagai titik awal.

Nilai kosong dianggap **tidak diisi** dan jatuh kembali ke bawaan — jadi baris
`TOP_K=` tidak akan membuat pengaturan bernilai kosong.

---

## Sumber dokumen

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `SOURCE_DIR` | `G:\Shared drives\Tim Borang M.TI\Akreditasi\2026` | Korpus utama. Selalu dibuka baca-saja. |
| `EXTRA_SOURCE_DIRS` | `corpus-lokal` | Direktori tambahan, dipisahkan **titik-koma**. |
| `DATA_DIR` | `data` | Semua artefak yang dihasilkan. |

Titik-koma dipakai sebagai pemisah, bukan koma, karena nama folder Windows lazim
mengandung koma (`1. Budaya Mutu, dst`) tetapi tidak pernah titik-koma.

> `DATA_DIR` **wajib** berada di luar `SOURCE_DIR`. Server menolak berjalan bila
> tidak, karena menulis indeks ke dalam korpus berarti mengubah shared drive.

---

## Penyaringan berkas

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `EXCLUDE_DIRS` | `zdev,__pycache__,.git,...` | Nama direktori yang dilewati di mana pun |
| `TEXT_EXTENSIONS` | `.pdf,.docx,.xlsx,...` | Format yang dapat diekstrak |
| `IMAGE_EXTENSIONS` | `.jpg,.png,...` | Hanya diindeks bila OCR aktif |
| `SKIP_EXTENSIONS` | `.mov,.lnk,.gsheet,...` | Tidak pernah diindeks |
| `MAX_FILE_MB` | `200` | Berkas lebih besar dilewati |

`zdev` dikecualikan karena berisi skrip pengembangan. Materi yang berguna di
dalamnya — pemetaan dosen ke SINTA dan Google Scholar — sudah disalin ke
`corpus-lokal` dan diindeks dari sana.

`.gsheet`, `.gdoc`, dan `.gslides` bukan dokumen: keduanya hanya berkas penunjuk
milik Google Drive yang berisi URL, sehingga tidak ada gunanya diindeks.

---

## Pemotongan teks

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `CHUNK_TOKENS` | `450` | Panjang target satu potongan |
| `CHUNK_OVERLAP_TOKENS` | `80` | Token yang diulang antar potongan |
| `MIN_CHUNK_CHARS` | `80` | Potongan lebih pendek dibuang |

**Penyetelan.** Potongan yang lebih besar menjaga konteks tetap utuh tetapi
membuat embedding menjadi rata-rata beberapa topik sekaligus; potongan yang
lebih kecil menajamkan pencarian tetapi memutus fakta dari penjelasannya. Untuk
tabel dan daftar yang padat, `350` sering lebih baik. Untuk narasi panjang
seperti LED, `600` bisa dicoba.

Mengubah nilai-nilai ini mengharuskan `ingest --rebuild`, karena potongan lama
tidak lagi sebanding dengan yang baru.

---

## Model embedding

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `EMBED_MODEL` | `BAAI/bge-m3` | Multibahasa, kuat untuk Bahasa Indonesia |
| `EMBED_DEVICE` | `auto` | `auto` / `cuda` / `cpu` |
| `EMBED_BATCH_SIZE` | `8` | Turunkan bila VRAM sempit |
| `EMBED_DIM` | `1024` | Lebar vektor BGE-M3 |

Mengganti `EMBED_MODEL` **wajib** diikuti `ingest --rebuild`. Vektor dari dua
model berbeda tidak sebanding; bila dimensinya juga berbeda, pencarian akan
mengembalikan hasil kosong disertai peringatan di log.

---

## Reranking

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `RERANK_ENABLED` | `1` | Matikan bila ingin jawaban lebih cepat |
| `RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Cross-encoder multibahasa |
| `RERANK_DEVICE` | `cpu` | Sengaja CPU — lihat catatan di bawah |

Reranker sengaja tidak di GPU. Bersama Qwen3 8B (±5 GB) dan BGE-M3 (±1,2 GB),
menambahkan ±1,1 GB lagi menyisakan terlalu sedikit VRAM untuk KV cache, dan
kegagalan memori akan muncul di tengah jawaban. Setel `RERANK_DEVICE=cuda` hanya
bila Anda memakai model bahasa yang jauh lebih kecil.

---

## Pencarian

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `DENSE_CANDIDATES` | `40` | Kandidat dari indeks vektor |
| `SPARSE_CANDIDATES` | `40` | Kandidat dari indeks kata kunci |
| `RERANK_CANDIDATES` | `24` | Kandidat yang dinilai cross-encoder |
| `TOP_K` | `6` | Sumber yang masuk jawaban dan disitasi |
| `RRF_K` | `60` | Konstanta peredam penggabungan peringkat |
| `MAX_CONTEXT_CHARS` | `14000` | Batas atas konteks ke model |

**Penyetelan.** Naikkan `TOP_K` untuk pertanyaan yang perlu merangkum banyak
dokumen sekaligus (mis. daftar seluruh publikasi); turunkan untuk pertanyaan
faktual tunggal agar model tidak terganggu konteks yang tidak relevan.

`MAX_CONTEXT_CHARS` harus tetap nyaman di bawah `OLLAMA_NUM_CTX` dikalikan
sekitar 3,6 karakter per token, dikurangi ruang untuk jawaban. Pada `8192`
token, angka 14.000 karakter menyisakan ruang yang memadai.

---

## OCR

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `OCR_ENABLED` | `0` | Aktifkan setelah memasang RapidOCR |
| `OCR_PAGE_CHAR_THRESHOLD` | `60` | Di bawah ini dianggap hasil pindai |
| `OCR_DPI` | `200` | Resolusi render halaman |
| `OCR_MAX_PAGES_PER_DOC` | `80` | Pengaman agar satu PDF tidak menyandera proses |

Prasyarat:

```powershell
.\.venv\Scripts\python.exe -m pip install rapidocr-onnxruntime
```

---

## Model bahasa

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `LLM_BACKEND` | `ollama` | `ollama` / `claude_cli` / `codex_cli` / `antigravity_cli` |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Alamat layanan Ollama |
| `OLLAMA_MODEL` | `qwen3:8b` | Tag model |
| `OLLAMA_NUM_CTX` | `8192` | Jendela konteks |
| `OLLAMA_KEEP_ALIVE` | `10m` | Lama model tetap dimuat |
| `TEMPERATURE` | `0.2` | Rendah agar jawaban dekat ke teks sumber |
| `MAX_OUTPUT_TOKENS` | `1536` | Panjang maksimum jawaban |

CLI agen:

| Variabel | Bawaan |
|---|---|
| `CLAUDE_CLI_PATH` / `CLAUDE_CLI_MODEL` / `CLAUDE_CLI_TIMEOUT` | `claude` / (kosong) / `240` |
| `CODEX_CLI_PATH` / `CODEX_CLI_MODEL` / `CODEX_CLI_TIMEOUT` | `codex` / (kosong) / `240` |
| `ANTIGRAVITY_CLI_PATH` / `ANTIGRAVITY_CLI_MODEL` / `ANTIGRAVITY_CLI_TIMEOUT` | `agy` / (kosong) / `240` |

Model yang dikosongkan berarti "ikuti pengaturan CLI itu sendiri", yang biasanya
merupakan pilihan paling tepat untuk perkakas yang sudah Anda konfigurasikan.

> Hanya `ollama` yang menjaga teks dokumen tetap berada di komputer ini. Ketiga
> backend CLI mengirimkan kutipan yang ditemukan ke penyedia layanannya
> masing-masing.

---

## Server web

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `HOST` | `127.0.0.1` | Hanya loopback |
| `PORT` | `8029` | Porta HTTP |
| `ALLOW_OPEN_FILE` | `1` | Izinkan antarmuka membuka berkas yang disitasi |

Mengubah `HOST` menjadi `0.0.0.0` membuka korpus akreditasi internal ke seluruh
jaringan lokal. Pengunjung tetap wajib login, tetapi pemegang kata sandi `guest`
dapat membaca seluruh dokumen. Jangan lakukan kecuali Anda memang memahami
konsekuensinya.

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | Nama pengguna admin |
| `ADMIN_PASSWORD_HASH` | *(hash bawaan)* | Hash PBKDF2 kata sandi admin |
| `GUEST_USERNAME` | `guest` | Nama pengguna guest |
| `GUEST_PASSWORD_HASH` | *(hash bawaan)* | Hash PBKDF2 kata sandi guest |
| `SESSION_SECRET` | *(acak, disimpan di `data/`)* | Kunci penanda tangan cookie sesi |
| `SESSION_HOURS` | `12` | Lama sesi login berlaku |

Buat hash kata sandi baru dengan `python main.py hash-password`.

---

## Pengaturan yang memerlukan rebuild

Setelah mengubah salah satu dari berikut, jalankan `main.py ingest --rebuild`:

- `EMBED_MODEL`, `EMBED_DIM`
- `CHUNK_TOKENS`, `CHUNK_OVERLAP_TOKENS`, `MIN_CHUNK_CHARS`
- `OCR_ENABLED` (bila ingin halaman pindai ikut terbaca)

Selebihnya cukup dengan `main.py ingest` biasa, atau bahkan langsung berlaku
tanpa indexing ulang.
