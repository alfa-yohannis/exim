# Exim RAG — Ekspor Impor

Sistem tanya-jawab berbasis web atas regulasi dan informasi publik ekspor-impor
Indonesia: kepabeanan, tarif, larangan dan pembatasan, perizinan, serta
perjanjian dagang.

Ajukan pertanyaan dalam Bahasa Indonesia, dapatkan jawaban yang berlandaskan
dokumen sebenarnya — setiap pernyataan disertai sitasi bernomor yang menunjuk ke
berkas dan halaman sumbernya.

> **Dokumen sumber tidak pernah diubah.** Salinan situs sumber dibuka dalam mode
> baca-saja, dan aturan itu ditegakkan di dalam kode, bukan sekadar kesepakatan.
> Lihat [Jaminan baca-saja](#jaminan-baca-saja).

---

## Ringkasan

| | |
|---|---|
| **Sumber** | Salinan bertanggal situs pemerintah terkait ekspor-impor (baca-saja) — lihat [Sumber yang di-crawl](#sumber-yang-di-crawl) |
| **Format** | PDF, DOCX, XLSX/XLSM, PPTX, HTML, CSV, JSON, TXT/MD; gambar melalui OCR opsional |
| **Pencarian** | Hibrida — vektor BGE-M3 + BM25 SQLite FTS5, digabung dengan reciprocal rank fusion, lalu dipertajam cross-encoder |
| **Penjawab** | Qwen3 8B lokal via Ollama, atau CLI Claude Code / Codex / Antigravity |
| **Sumber eksternal** | Salinan bertanggal SINTA (dengan login & paginasi), Google Scholar, PDDikti, dan situs kampus |
| **Antarmuka** | Satu halaman: jawaban Markdown mengalir, sitasi dapat diklik, tautan ke sumber asli, salin ke papan klip, unduh dokumen, percakapan tersimpan. Tiga bahasa — Bahasa Indonesia, English, Tiếng Việt — dan jawaban ditulis dalam bahasa yang dipilih |
| **Skala saat ini** | Indonesia: ±2.500 berkas dari 7 situs (Bea Cukai, JDIH Kemenkeu, e-SKA, LPEI, Bank Indonesia, Ditjen Daglu, Ditjen PEN), ±59.000 potongan teks. Vietnam: Công báo sedang di-crawl |
| **Pengujian** | 552 pengujian unit, selesai dalam ±16 detik tanpa jaringan |

---

## Sumber yang di-crawl

Diurutkan menurut **pengaruh**: seberapa kuat dasar hukumnya dan seberapa
banyak pertanyaan ekspor-impor yang lazim dapat dijawab darinya; sumber di
bawah menambah cakupan per komoditas. Urutan pengunduhan berbeda: setelah Bea
Cukai dan JDIH Kemenkeu, pengunduhan dimulai dari sumber dengan perkiraan
berkas paling sedikit.

Kolom **Perkiraan berkas** menghitung halaman dan lampiran yang akan diambil,
bukan seluruh situs; berita tidak dihitung, kecuali bila beritanya sendiri
yang diambil (edaran e-SKA, siaran pers LPEI). Angka bertanda "sudah" adalah
hasil crawl; yang lain berasal dari sitemap atau sampel. Yang bertanda
*(kasar)* masih tebakan dari struktur situs dan diperbarui saat situsnya
dipetakan.

Kolom **Cara** menentukan pembacanya. **HTTP** berarti halaman dapat diambil
langsung. **Chrome** berarti isinya baru muncul setelah JavaScript berjalan,
sehingga dibaca lewat Chrome lokal di mesin ini yang dilekati lewat DevTools —
jalur yang sama dengan `app/browser_sources.py`. Tutup Chrome selama crawl
berjalan, karena porta debug tidak dapat dipakai bersamaan.

URL dan robots.txt diperiksa pada 28–29 September 2026. Crawler-nya
`app/crawler.py`, dengan rencana per situs di `app/crawl_sites.py`. Sudah
di-crawl: Bea Cukai (`--site beacukai`), JDIH Kemenkeu (`jdih-kemenkeu`),
Ditjen Daglu (`daglu`), e-SKA (`e-ska`), Ditjen PEN (`pen`), Bank Indonesia
(`bi`), LPEI (`lpei`), dan BPOM (`bpom`, `e-bpom`); situs lain menyusul.
Hasilnya masuk ke `corpus-exim\<Negara>\<Situs>\<Bagian>\`, sehingga filter
folder di antarmuka menjadi filter per negara. Lihat
[Berjalan di latar belakang](#berjalan-di-latar-belakang) dan
[Korpus Vietnam](#korpus-vietnam).

### Tier 1 — Inti

| # | Situs | Titik awal | Yang diambil | Perkiraan berkas | Cara |
|---|---|---|---|---|---|
| 1 | Bea Cukai | https://www.beacukai.go.id/peta-situs | Prosedur ekspor dan impor, larangan dan pembatasan, BTKI dan tarif, FAQ, barang kiriman dan penumpang, fasilitas (KITE, Kawasan Berikat, AEO, MITA), FTZ, KEK, keberatan dan banding. Halaman: `/impor-untuk-dipakai`, `/klinik-ekspor`, `/larangan-pembatasan`, `/btki-dan-tarif`, `/faq`, `/barangkiriman`, `/barang-penumpang`, `/registrasi-kepabeanan`, `/pemeriksaan-pabean`, `/impor-sementara`, `/keberatan-banding`, `/fasilitas-kite`, `/fasilitas-tempat-penimbunan-berikat`, `/fasilitas-aeo`, `/fasilitas-mita`, `/fta`, `/free-trade-zone`, `/kawasan-ekonomi-khusus` | ±460 — sudah (97 halaman + 359 lampiran) | HTTP |
| 2 | INSW — Indonesia National Trade Repository | https://insw.go.id/intr | Per pos tarif (HS): tarif bea masuk dan keluar, ketentuan lartas, instansi penerbit izin. Jenis pertanyaan yang paling sering muncul: "HS sekian kena apa saja?" | ±11.000 pos tarif HS | Chrome |
| 3 | JDIH Kemenkeu | https://jdih.kemenkeu.go.id/sitemap.xml | PMK.04 (Bea Cukai) seluruhnya; PMK.010/.011 (BKF) dan PMK sejak 2023 hanya yang judulnya menyangkut kepabeanan atau perdagangan. Tiap PMK: halaman metadata (status, riwayat pencabutan dan perubahan) dan PDF teks lengkap, diunduh dari yang terkecil, tanpa abstrak. Hindari `/pdfjs` dan `/api/auth` (robots.txt) | 1.539 — sudah (898 halaman PMK + 641 PDF; 20 PDF di atas 30 MB dilewati) | HTTP |
| 4 | JDIH BPK | https://peraturan.bpk.go.id/Search?keywords=kepabeanan · `/sitemap.xml` | UU Kepabeanan (10/1995 jo. 17/2006), UU Perdagangan (7/2014), PP 29/2021, PP DHE (36/2023), serta Permendag kebijakan ekspor dan impor — pengganti jdih.kemendag.go.id yang menolak crawler AI | ±150 peraturan + PDF | HTTP |
| 5 | Kemendag — Informasi Mekanisme Ekspor Impor | https://exim.kemendag.go.id | Mekanisme ekspor-impor per komoditas, persyaratan perizinan, ketentuan asal barang | ±300 *(kasar)* | Chrome |
| 6 | Ditjen Perdagangan Luar Negeri | https://ditjendaglu.kemendag.go.id/faq | FAQ kebijakan dan perizinan ekspor-impor per komoditas dan ketentuan asal barang (6 halaman `?page=`). Hanya HTML: robots.txt melarang PDF, ZIP, DOCX, dan PPTX, sehingga publikasi, infografis, dan himpunan peraturan yang berupa PDF tidak diambil | 6 — sudah | HTTP |

### Tier 2 — Lartas dan perizinan sektoral

| # | Situs | Titik awal | Yang diambil | Perkiraan berkas | Cara |
|---|---|---|---|---|---|
| 7 | Badan Karantina Indonesia | https://karantinaindonesia.go.id/sitemap.xml · https://jdih.karantinaindonesia.go.id | Persyaratan karantina hewan, ikan, dan tumbuhan untuk ekspor dan impor. Halaman `/tanya-karantina` dirender JavaScript | ±100 *(kasar)* | HTTP, Chrome untuk Tanya Karantina |
| 8 | BPOM | https://jdih.pom.go.id (pencarian perihal "pemasukan", "impor", "ekspor") · https://e-bpom.pom.go.id | Khusus ekspor-impor. JDIH: peraturan pengawasan pemasukan obat dan makanan, SKI, jalur khusus (SAS), daftar barang yang dibatasi pemasukannya, SKE pangan olahan, impor-ekspor narkotika dan prekursor; tiap peraturan dengan halaman metadata (status berlaku) dan PDF-nya, tanpa terjemahan Inggris dan naskah urgensi. Server PDF-nya tidak menyebut tipe berkas, jadi PDF dikenali dari bita awalnya. e-BPOM: FAQ SKI, SKE, rekomendasi impor, INSW, dan layanan post border, serta standar pelayanan, formulir, dan FAQ SAS; daftar perusahaan prioritas dan materi usang dilewati. Registrasi, label, dan iklan tidak diambil. Export Consultation Desk (kerjasamahumas.pom.go.id/ecd) memasang pemeriksaan bot, tidak diambil | 179 — sudah (85 halaman + 94 PDF) | HTTP |
| 9 | BSN | https://www.bsn.go.id | Daftar SNI wajib dan penilaian kesesuaian | ±30 *(kasar)* | HTTP |
| 10 | Kemenperin | https://jdih.kemenperin.go.id · https://kemenperin.go.id | Pertimbangan teknis impor, pemberlakuan SNI wajib | ±150 *(kasar)* | HTTP |
| 11 | Kementan | https://jdih.pertanian.go.id | RIPH dan rekomendasi impor produk pertanian | ±50 *(kasar)* | HTTP |
| 12 | KKP · ESDM | https://kkp.go.id · https://www.esdm.go.id | Ekspor dan impor hasil perikanan; ekspor mineral dan batubara | ±60 *(kasar)* | HTTP |

### Tier 3 — Perjanjian dagang, devisa, pembiayaan

| # | Situs | Titik awal | Yang diambil | Perkiraan berkas | Cara |
|---|---|---|---|---|---|
| 13 | Ditjen Perundingan Perdagangan Internasional | https://ditjenppi.kemendag.go.id | Daftar dan isi perjanjian dagang (FTA, CEPA). Ditunda: factsheet dan naskah perjanjian berupa PDF yang dilarang robots.txt, dan halaman perundingan (ASEAN, bilateral, WTO) mengisi tabnya lewat JavaScript; HTML-nya hanya profil direktorat | — ditunda | Chrome, bila PDF tetap dilarang |
| 14 | e-SKA Kemendag | https://e-ska.kemendag.go.id/cms.php | Informasi SKA (tentang SKA, peraturan, tutorial, tarif preferensi negara tujuan, FAQ) beserta buku panduan dan alur pengajuannya; ±110 edaran (Form E, D, AK, REX/GSP, ketentuan asal barang, notifikasi negara mitra) beserta lampirannya; kegiatan. Pengumuman pemeliharaan sistem dan dua unggahan uji dilewati | 265 — sudah (153 halaman + 112 lampiran) | HTTP |
| 15 | Ditjen Pengembangan Ekspor Nasional | https://ditjenpen.kemendag.go.id/faq | FAQ cara memulai ekspor, informasi pasar, dan program promosi (4 halaman `?page=`). Majalah Warta Ekspor tampil sebagai flipbook JavaScript, belum diambil | 4 — sudah | HTTP |
| 16 | Bank Indonesia | https://www.bi.go.id/id/publikasi/peraturan | Devisa hasil ekspor (DHE) dan devisa pembayaran impor: PBI 7/2023 beserta perubahannya (PBI 3/2025, 5/2026), PADG 4/2023 beserta perubahannya (PADG 6/2024, 4/2025, 16/2026), PBI 21/14/PBI/2019 yang digantikannya, dan dua siaran pers. Tiap peraturan: ringkasan, PDF peraturan, dan PDF tanya jawab. Situsnya tanpa sitemap, jadi halamannya disebut satu per satu | 30 — sudah (10 halaman + 20 PDF) | HTTP |
| 17 | LPEI (Indonesia Eximbank) | https://www.indonesiaeximbank.go.id/product-services · `/sitemap.xml` | 18 produk (pembiayaan modal kerja dan investasi ekspor, buyers credit, penjaminan termasuk penjaminan kepabeanan, asuransi piutang dagang, jasa konsultasi dan Rintisan Eksportir Baru), FAQ, serta ±190 siaran pers dan artikel | 212 — sudah (201 halaman + 11 lampiran) | HTTP |
| 18 | Salinan pajak.go.id | `D:\ALFA\djp\corpus-pajak` (proyek djp) | PPN impor, PPh 22 impor, fasilitas pajak Kawasan Berikat dan KITE — cukup disaring, tidak perlu crawl ulang | ±100 dari 10.250 berkas *(kasar)* | — |

### Tidak di-crawl

| Situs | Alasan |
|---|---|
| https://jdih.kemendag.go.id | robots.txt secara eksplisit menolak crawler AI (ClaudeBot, GPTBot, dan lainnya). Membacanya lewat Chrome tidak mengubah penolakan itu. Permendag diambil dari JDIH BPK |
| https://ftacenter.kemendag.go.id | Dialihkan ke halaman login intranet Kemendag |
| https://inatrade.kemendag.go.id · https://oss.go.id · CEISA | Aplikasi perizinan yang memerlukan akun; berisi data usaha, bukan informasi rujukan |
| https://www.bps.go.id | Menolak permintaan skrip (HTTP 403). Statistik perdagangan pengaruhnya rendah untuk tanya-jawab regulasi; bila diperlukan, pakai WebAPI resmi BPS |
| https://lnsw.go.id | Tidak merespons saat diperiksa |
| https://inatrims.kemendag.go.id | Kini dialihkan ke LAMANSITU (informasi mutu), bukan informasi ekspor-impor |
| Incoterms (ICC) · HS Explanatory Notes (WCO) | Berhak cipta dan berbayar |

---

## Korpus Vietnam

Tujuannya menjawab pertanyaan lintas batas, misalnya "saya mau ekspor kopi
ke Vietnam, apa langkahnya, dokumennya, dan biayanya?": sisi Indonesia sudah
ada di korpus, sisi Vietnam (bea masuk, karantina, keamanan pangan, asal
barang, prosedur) ditambahkan di sini. URL dan robots.txt diperiksa pada 29
September 2026. Công báo (`--site cong-bao`) sedang di-crawl; situs lain
menyusul.

### Struktur folder

Negara menjadi tingkat pertama, sehingga filter folder di antarmuka menjadi
filter per negara dan negara ASEAN berikutnya tinggal menambah satu folder:

```
corpus-exim\
├── Indonesia\                        tujuh situs Indonesia
│   ├── Bea Cukai\<Bagian>\
│   ├── JDIH Kemenkeu\<Bagian>\
│   └── ...
└── Vietnam\
    ├── Hai Quan\                     customs.gov.vn — Cục Hải quan (Bộ Tài chính)
    │   ├── Thu tuc\                  prosedur kepabeanan per jenis ekspor/impor
    │   ├── Hoi dap\                  tanya jawab kebijakan dan prosedur
    │   └── Bieu thue\                tarif
    ├── Cong Bao\                     congbao.chinhphu.vn — Lembaran Negara (naskah resmi)
    │   ├── Quoc hoi\                 undang-undang (Luật)
    │   ├── Chinh phu\                peraturan pemerintah (Nghị định)
    │   ├── Bo Tai chinh\             thông tư kepabeanan, tarif, asal barang
    │   ├── Bo Cong Thuong\           thông tư izin ekspor-impor, C/O
    │   ├── Bo Nong nghiep va Moi truong\   karantina hewan dan tumbuhan
    │   └── Bo Y te\                  keamanan pangan impor
    ├── Dich Vu Cong\                 dichvucong.gov.vn — langkah, dokumen, biaya, lama proses
    ├── Bo Cong Thuong\               moit.gov.vn — văn bản, C/O, perdagangan luar negeri
    ├── Kiem Dich\                    ppd.gov.vn, cucthuy.gov.vn — karantina
    ├── An Toan Thuc Pham\            vfa.gov.vn — keamanan pangan
    ├── Trung Tam WTO\                trungtamwto.vn — FTA, asal barang, profil pasar
    └── Ngan Hang Nha Nuoc\           sbv.gov.vn — devisa
```

Nama folder memakai bahasa Vietnam tanpa diakritik, sesuai nama lembaganya,
agar aman di semua alat; nama bagian mengikuti isi situsnya.

Yang sudah disiapkan di aplikasi:

1. `CrawlSite` punya `country`, dan penulis korpus menulis ke
   `corpus-exim\<Negara>\<Situs>\<Bagian>\`.
2. Tujuh folder Indonesia dipindah ke `corpus-exim\Indonesia\` pada 29
   September 2026. ID dokumen mengikuti path, jadi path dan ID di indeks,
   manifest `data\crawl-*.json`, dan sitasi percakapan tersimpan ikut
   diganti bersamaan; tidak ada yang dibaca ulang. Cadangannya di
   `data\backup-sebelum-negara-*`.
3. Filter folder menjadi filter per negara; filter per sumber
   (`Vietnam\Cong Bao`) bisa menyusul.
4. Prompt penjawab di `app/rag.py` mengenal kedua negara, memisahkan
   ketentuan negara asal dan tujuan, dan menulis jawaban dalam bahasa
   antarmuka: Bahasa Indonesia, English, atau Tiếng Việt.
5. Crawler mendapat tiga kemampuan untuk situs Vietnam: nomor halaman di path
   (`page_url_template`, `…/trang-{number}.htm`), saringan judul pada daftar
   sebelum halaman diambil (`link_text_pattern`), dan unduhan yang menamai
   berkasnya di query (`…/api/download/stream?…&file_name=…`), dengan jenis
   berkas yang diutamakan (`attachment_preference`: DOCX, lalu PDF bila hanya
   ada DOC lama). Server berkas pemerintah Vietnam tidak mengirim sertifikat
   perantaranya; sertifikat publik itu ada di
   `app/certificates/intermediates.pem`.

**Bahasa dan pencarian.** Sebagian besar dokumen Vietnam berbahasa Vietnam.
BGE-M3 dan reranker-nya multibahasa, dan pencarian tidak menyaring bahasa:
pertanyaan dalam bahasa apa pun menelusuri seluruh korpus, kecuali negara
dipilih di filter. Uji 29 September 2026:

| Pertanyaan | Hasil teratas | Skor |
|---|---|---|
| Indonesia → aturan Indonesia | tepat | 0,99 |
| English → aturan Indonesia | tepat | 0,97 |
| Tiếng Việt → aturan Vietnam | tepat | 1,00 |
| English → aturan Vietnam | dokumen tepat | 0,36 |
| Tiếng Việt → aturan Indonesia | dokumen tepat | 0,34 |
| Indonesia → aturan Vietnam | aturan Indonesia, bukan Vietnam | 0,85 |

Bahasa yang sama hampir selalu tepat; lintas bahasa menemukan dokumen yang
benar dengan skor rendah, dan pertanyaan berbahasa Indonesia tentang Vietnam
tertarik ke dokumen Indonesia. Sampai pencarian mengenali negara dalam
pertanyaan, pilih "Vietnam" di filter negara.

### Sumber, menurut pengaruh

| # | Situs | Titik awal | Yang diambil | Perkiraan berkas | Cara |
|---|---|---|---|---|---|
| 1 | Hải quan Việt Nam | https://www.customs.gov.vn | Prosedur kepabeanan ekspor dan impor, tanya jawab "Chính sách và thủ tục" (`index.jsp?pageId=14&cid=948`), tarif, dan văn bản. robots.txt mengizinkan semua; isinya diisi JavaScript | ±2.000 *(kasar)* | Chrome |
| 2 | Công báo (Lembaran Negara) | https://congbao.chinhphu.vn/van-ban-dang-cong-bao/chinh-phu-c1.htm · `quoc-hoi-c31` · `bo-tai-chinh-c10` · `bo-cong-thuong-c3` · `bo-nong-nghiep-va-moi-truong-c167` · `bo-y-te-c25` | Naskah resmi Luật Hải quan, Luật Quản lý ngoại thương, Luật Thuế XK-NK, dan Nghị định serta Thông tư tentang prosedur kepabeanan, pengelolaan perdagangan luar negeri, asal barang, tabel tarif, karantina, dan keamanan pangan impor. Disaring dengan kata kunci judul: hải quan, xuất khẩu, nhập khẩu, xuất xứ, biểu thuế, kiểm dịch, phòng vệ thương mại. Halaman dokumen memuat tanggal dan judul; naskahnya DOCX atau PDF di g7.cdnchinhphu.vn. Bagian: Quốc hội, Chính phủ, Thủ tướng, Bộ Tài chính, Bộ Công Thương, Bộ Nông nghiệp và Môi trường, Bộ Y tế | ±1.500 — sedang di-crawl | HTTP |
| 3 | Dịch vụ công quốc gia | https://dichvucong.gov.vn/p/home/dvc-chi-tiet-thu-tuc-hanh-chinh.html?ma_thu_tuc=1.000533 | Prosedur administratif bidang ekspor-impor, kepabeanan, karantina, dan keamanan pangan: langkah, dokumen, biaya, lama proses (mis. C/O preferensi `1.000533`, C/O Form E `1.000695`). Bahan utama rekomendasi proses. Tidak merespons dari jaringan ini saat diperiksa | ±300 prosedur *(kasar)* | Chrome |
| 4 | Bộ Công Thương (MOIT) | https://moit.gov.vn/van-ban-phap-luat | Văn bản pháp quy dan hợp nhất bidang ekspor-impor, daftar barang per HS, ketentuan C/O dan perdagangan luar negeri | ±200 *(kasar)* | HTTP |
| 5 | Cục Bảo vệ thực vật | https://www.ppd.gov.vn | Persyaratan fitosanitari ekspor-impor, daftar organisme pengganggu, izin impor | ±100 *(kasar)* | HTTP |
| 6 | Cục Thú y | https://cucthuy.gov.vn | Karantina hewan dan produk hewan | ±100 *(kasar)* | HTTP |
| 7 | Cục An toàn thực phẩm | https://vfa.gov.vn | Pemeriksaan keamanan pangan impor dan pernyataan mandiri produk. Tidak merespons saat diperiksa | ±100 *(kasar)* | HTTP |
| 8 | SPS Vietnam · TBT Vietnam | https://www.spsvietnam.gov.vn · https://tbtvn.org | Notifikasi SPS dan TBT, regulasi teknis (QCVN). Rantai sertifikat SSL-nya tidak lengkap, jadi perlu sertifikat perantara | ±200 *(kasar)* | HTTP |
| 9 | Trung tâm WTO và Hội nhập (VCCI) | https://trungtamwto.vn/fta · `/an-pham` · `/market` · https://wtocenter.vn | Naskah dan komitmen FTA (ATIGA, RCEP, CPTPP, EVFTA), buku panduan asal barang, alat cek tarif, profil pasar; wtocenter.vn versi Inggris | ±500 *(kasar)* | HTTP |
| 10 | Ngân hàng Nhà nước | https://sbv.gov.vn | Aturan devisa untuk pembayaran ekspor-impor | ±30 *(kasar)* | HTTP |
| 11 | Bộ Tài chính | https://mof.gov.vn | Kebijakan tarif dan kepabeanan | ±50 *(kasar)* | HTTP |
| 12 | Perwakilan dagang RI di Vietnam | Atase Perdagangan Hanoi · ITPC Ho Chi Minh City · KBRI Hanoi | Profil pasar dan panduan bagi eksportir Indonesia. Belum diperiksa | ±30 *(kasar)* | HTTP |

### Tidak di-crawl (Vietnam)

| Situs | Alasan |
|---|---|
| https://vntr.moit.gov.vn | robots.txt: `Disallow: /` untuk semua crawler. Tarif per HS diambil dari Hải quan dan Trung tâm WTO |
| https://vbpl.vn | Isi dokumennya dimuat dari `/api/`, yang dilarang robots.txt. Naskah yang sama ada di Công báo |
| https://phapluat.gov.vn | Menolak permintaan skrip (HTTP 403). Naskah yang sama ada di Công báo |
| https://thuvienphapluat.vn · https://luatvietnam.vn | Penerbit hukum swasta; berhak cipta dan sebagian berbayar |
| https://ecosys.gov.vn · https://vnsw.gov.vn | Aplikasi pengajuan C/O dan National Single Window yang memerlukan akun |
| ASEAN Trade Repository · ASEAN Tariff Finder | Tidak merespons saat diperiksa |

---

## Mulai cepat

```powershell
# 1. Pasang dependensi (sekali saja)
.\setup.ps1

# 2. Bangun indeks (proses pertama mengunduh model embedding ~2,3 GB)
.\.venv\Scripts\python.exe main.py ingest

# 3. Jalankan antarmuka web
.\run.ps1
```

Lalu buka **http://127.0.0.1:8029**.

> Google Drive harus aktif sebelum `ingest`. Bila drive `G:` belum termuat,
> proses indexing menolak berjalan — justru untuk melindungi indeks yang sudah
> ada. Menjalankan `serve` tetap bisa tanpa `G:`, karena jawaban dilayani dari
> indeks, bukan dari berkas aslinya.

---

## Perintah baris perintah

```powershell
python main.py serve                 # antarmuka web (bawaan)
python main.py serve --reload        # muat ulang otomatis saat kode berubah
python main.py ingest                # indeks hanya berkas baru dan yang berubah
python main.py ingest --rebuild      # hapus indeks lalu baca ulang semuanya
python main.py snapshot              # ambil halaman SINTA/Scholar/PDDikti, lalu indeks
python main.py snapshot --http-only  # tanpa browser (lewati PDDikti & login SINTA)
python main.py ask "Berapa EWMP DTPR pada TS?"
python main.py ask "..." --folder "1. Budaya Mutu" --backend claude_cli
python main.py status                # ukuran indeks dan kesiapan backend
python main.py hash-password         # buat hash kata sandi admin/guest baru untuk .env
python main.py crawl --site beacukai --full --no-ingest  # salin satu situs sumber ke corpus-exim
# --site: beacukai, jdih-kemenkeu, daglu, e-ska, pen, bi, lpei, bpom, e-bpom, cong-bao
python main.py crawl --site beacukai --sections Impor,FAQ --limit 5  # sampel kecil, lalu indeks
```

### Berjalan di latar belakang

Server dan crawl dijalankan lewat Task Scheduler, sehingga tetap hidup walaupun
terminal ditutup:

| Task | Skrip | Log |
|---|---|---|
| `Exim server` | `serve-background.cmd` | `data\server.log` |
| `Exim crawl` | `crawl-full.cmd` — setiap situs dua kali jalan; halaman yang sudah ada dilewati | `data\crawl.log` |

```powershell
Start-ScheduledTask "Exim crawl"                           # crawl ulang semua situs
Get-Content D:\ALFA\exim\data\crawl.log -Wait -Tail 20     # pantau
```

Selama server berjalan, berkas baru hasil crawl diindeks sendiri tiap 60 detik
(`AUTO_INGEST_SECONDS`), jadi crawl selalu dengan `--no-ingest`: indexer kedua
di proses lain akan menimpa vektor milik server.

`Stop-ScheduledTask` hanya menghentikan pembungkus PowerShell-nya; `cmd` dan
Python di bawahnya tetap berjalan. Untuk benar-benar menghentikannya:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'crawl-full\.cmd|serve-background\.cmd|main\.py\s+(serve|crawl)' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

> Selama indexing massal berjalan, jawaban Ollama bisa sangat lambat atau
> habis waktu: Qwen3 8B dan model embedding berbagi VRAM 8 GB yang sama.
> Setelah indexing selesai, jawaban kembali normal.

---

## Login dan hak akses

Alamat **http://127.0.0.1:8029** membuka landing page beserta formulir masuk;
aplikasinya sendiri ada di `/app`. Hanya ada dua peran, masing-masing dengan
satu akun. Formulir masuknya sama; akun yang dipakai menentukan perannya.

| Peran | Nama pengguna | Hak |
|---|---|---|
| **Admin** | `admin` | semua, termasuk **Perbarui Indeks** dan **Bangun Ulang** |
| **Guest** | `guest` | tanya jawab dan unduh dokumen yang disitasi — atas data indeks yang sama |

Kata sandi kedua akun dibagikan terpisah dan tidak dicantumkan di sini.

Guest tetap melihat tombol Perbarui Indeks dan Bangun Ulang, tetapi saat diklik
muncul pesan bahwa mereka tidak memiliki hak. Server juga menolak permintaan
indexing dari guest (HTTP 403), jadi batas ini tidak bisa dilewati lewat API.

Tab **Telusuri Potongan** dan **Daftar Dokumen**, serta panel **Perangkat**,
disembunyikan dari guest. Penyembunyian ini hanya pada tampilan: endpoint
`/api/search` dan `/api/documents` tetap dapat dipanggil guest secara langsung.

- Kata sandi disimpan sebagai hash PBKDF2 (`ADMIN_PASSWORD_HASH`,
  `GUEST_PASSWORD_HASH`), bukan teks biasa. Untuk menggantinya, jalankan
  `python main.py hash-password` lalu salin hasilnya ke variabel yang sesuai
  di `.env`.
- Sesi berupa cookie HttpOnly yang ditandatangani HMAC dan berlaku 12 jam
  (`SESSION_HOURS`). Kunci tanda tangannya disimpan di `data/session-secret.key`;
  hapus berkas itu untuk mengeluarkan semua pengguna.
- Lima kali salah kata sandi dari alamat yang sama mengunci login selama 60 detik.
- Semua endpoint `/api/*` dan `/docs` wajib login, kecuali `/api/auth/*` dan
  `/api/public/overview` (hanya jumlah dokumen, tanpa isi).

---

## Antarmuka

Di web, sistem tampil dengan nama **Custom-Local AI: Asisten Ekspor Impor** —
pada landing page, panel samping, judul tab peramban, dan footer. Nama ini
disiapkan untuk pameran umum: istilah *RAG* terlalu teknis bagi pengunjung,
sedangkan intinya — mengelola dokumen lokal dan merangkumnya dengan AI — mudah
dipahami. Nama *Exim RAG* tetap dipakai di kode, skrip, dan dokumentasi
teknis ini.

Untuk pameran pula, landing page dibuka dengan ajakan *"Butuh AI lokal yang
disesuaikan untuk organisasi Anda? Coba demo ini"* tepat di atas judul utama,
dan bagian tepat sebelum **Fitur** menawarkan sistem serupa untuk organisasi
pengunjung. Kedua
tautan **Coba demo ini** menuju formulir masuk. Kotak akun tamu di sana sedang
disembunyikan (kelas `hidden` pada blok *DEMO PAMERAN*), jadi pengunjung memakai
akun tamu yang tertera di poster.

Tiga tab: **Tanya Jawab**, **Telusuri Potongan** (melihat hasil pencarian tanpa
membuat jawaban — berguna memeriksa mutu retrieval), dan **Daftar Dokumen**.
Dua tab terakhir hanya tampil untuk admin.

Tombol **☰** di kiri baris tab menyembunyikan atau menampilkan panel samping,
sehingga percakapan dan kotak pertanyaan dapat memakai seluruh lebar layar;
pilihannya diingat peramban. Di ponsel (lebar ≤ 960 px) panel samping menjadi
laci yang tertutup secara bawaan dan terbuka di atas percakapan.

### Koneksi putus saat jawaban dibuat

Jawaban dibuat di server terlepas dari koneksi yang memintanya. Bila koneksi
terputus — layar ponsel terkunci, aplikasi lain dibuka, atau Wi-Fi berganti —
antarmuka menampilkan *"Koneksi terputus — menyambung kembali"* lalu
melanjutkan jawaban tepat dari bagian terakhir yang diterima, tanpa mengulang
teks. Jawaban tetap selesai dan tersimpan ke percakapan walaupun tidak ada yang
menunggu. Tombol **Hentikan** memberi tahu server untuk berhenti membuat
jawaban.

### Percakapan tersimpan

Setiap pertanyaan dan jawabannya tersimpan otomatis beserta sitasinya.
Percakapan dapat dilanjutkan kapan saja dari panel samping, diganti namanya,
atau dihapus. Judulnya diambil dari pertanyaan pertama agar mudah dikenali.

Pertanyaan lanjutan yang pendek ("siapa saja mereka?") secara otomatis
digabungkan dengan pertanyaan sebelumnya saat pencarian, sehingga tetap
menemukan dokumen yang tepat — tanpa itu, kalimat sependek itu nyaris tidak
membawa sinyal untuk dicari.

Riwayat disimpan di `data/conversations.db`, berkas terpisah dari indeks,
sehingga `ingest --rebuild` tidak pernah menghapus percakapan.

### Salin ke papan klip

Tersedia pada pertanyaan, jawaban (**Salin jawaban** atau **Salin + sumber**),
setiap kartu sitasi (**Salin kutipan**, lengkap dengan nama dokumen dan
lokasinya), dan setiap potongan pada tab Telusuri.

Yang disalin adalah Markdown mentah, bukan HTML hasil render, agar dapat
ditempel dengan rapi ke Word, surel, maupun editor Markdown.

### Unduh dan buka dokumen

Setiap dokumen dan setiap kartu sitasi menyediakan tombol **Unduh**, yang
bekerja baik dari komputer ini maupun dari komputer lain di jaringan.

Tombol **Tampilkan di folder** dan **Buka dokumen** hanya muncul bila antarmuka
diakses dari komputer yang menjalankan server. Keduanya bekerja pada desktop
*server*, sehingga bagi pengunjung jaringan tombol itu akan membuka berkas di
komputer orang lain — bukan miliknya.

---

## Akses dari komputer lain

Setel `HOST=0.0.0.0` pada `.env`, atau:

```powershell
.\run.ps1 -Lan
```

Program mencetak alamat IP komputer ini agar rekan kerja tinggal membukanya.
Izinkan porta lewat Windows Firewall sekali saja (**sebagai Administrator**):

```powershell
New-NetFirewallRule -DisplayName "Exim RAG" -Direction Inbound `
  -LocalPort 8029 -Protocol TCP -Action Allow -Profile Private
```

**Bila memakai Mobile Hotspot komputer ini**, adaptor hotspot tidak memiliki
profil jaringan sehingga Windows memperlakukannya sebagai *Public* — setiap
aturan bawaan, termasuk izin ping, hanya berlaku untuk *Private*. Tambahkan
aturan yang mencakup profil mana pun, dibatasi pada subnet hotspot saja:

```powershell
New-NetFirewallRule -DisplayName "Exim RAG (hotspot)" -Direction Inbound `
  -LocalPort 8029 -Protocol TCP -Action Allow -Profile Any `
  -RemoteAddress 192.168.137.0/24
```

Klien hotspot membuka **http://192.168.137.1:8029** — yaitu alamat komputer ini
*pada jaringan hotspot*, bukan alamat Ethernet-nya yang berada di subnet berbeda.

> **Setiap pengunjung wajib login.** Namun siapa pun yang memegang kata sandi
> `guest` dapat membaca seluruh dokumen terindeks — bagikan hanya ke tim.

---

## Backend penjawab

Backend dapat diganti kapan saja dari panel samping; pilihan berlaku per
pertanyaan.

| Backend | Model & tingkat penalaran | Teks dokumen keluar dari komputer? |
|---|---|---|
| `ollama` | Qwen3 8B (lokal) | **Tidak** |
| `claude_cli` | `claude-opus-5` · effort `max` | Ya → Anthropic |
| `codex_cli` | `gpt-5.6-sol` · effort `max` | Ya → OpenAI |
| `antigravity_cli` | `gemini-3.1-pro` · effort `high` | Ya → Google |

Setiap CLI disetel ke model terkuat pada tingkat penalaran tertinggi yang
didukungnya. Nama model diambil dari katalog masing-masing CLI (`claude --help`,
`~/.codex/models_cache.json`, `agy models`), bukan diasumsikan.

Ketiga backend CLI memakai perkakas yang sudah Anda pasang dan masuki akunnya,
sehingga tidak memerlukan kunci API. Kemampuan sintesisnya lebih baik daripada
model lokal 8B, tetapi masing-masing mengirimkan kutipan dokumen ke penyedianya.
Antarmuka menandai hal ini tepat pada pemilih backend, agar pertimbangannya
terlihat saat memilih — bukan tersembunyi di berkas konfigurasi.

Setiap CLI dipanggil non-interaktif dengan perkakas berkas dan shell dimatikan:
model menjawab murni dari prompt dan tidak pernah diberi jalan membaca korpus
sendiri. Codex tambahan dijalankan dengan `--sandbox read-only`, dan Antigravity
dijalankan di direktori kosong agar tidak memperlakukan proyek ini sebagai
basis kode yang perlu ditelusuri.

**Catatan perilaku.** Codex mengembalikan jawaban dalam satu peristiwa sehingga
muncul sekaligus; Ollama, Claude Code, dan Antigravity mengalirkannya bertahap.
Pada effort tertinggi, satu jawaban wajar memakan 30–60 detik.

---

## Perangkat keras dan pilihan model

Komputer ini memakai **RTX 4060 dengan VRAM 8 GB**, dan seluruh susunan model
dirancang menyesuaikan batas tersebut:

| Komponen | Model | Memori | Perangkat |
|---|---|---|---|
| Penjawab | Qwen3 8B, Q4_K_M | ~5,0 GB | GPU |
| Embedding | BGE-M3 | ~1,2 GB | GPU |
| Reranking | BGE-reranker-v2-m3 | ~1,1 GB | **CPU** |

Reranker sengaja dijalankan di CPU. Memuat ketiganya ke kartu grafis
membutuhkan sekitar 7,3 GB sebelum menghitung KV cache, sehingga berisiko gagal
kehabisan memori **di tengah jawaban** — saat pengguna sudah menunggu. CPU 20
inti menilai dua puluhan kandidat cukup cepat untuk tidak terasa.

Qwen3 8B dipilih dibanding model yang lebih besar karena inilah model terbesar
yang **seluruhnya** muat di GPU sambil menyisakan ruang untuk konteks RAG yang
panjang. Model 14B pada Q4 memerlukan ~9 GB, sebagian tumpah ke CPU, dan
kecepatannya turun menjadi sekitar seperlima untuk jawaban sepanjang ini.

---

## Sumber eksternal

`main.py snapshot` mengambil halaman yang dirujuk dokumen — profil SINTA dan
Google Scholar dari pemetaan dosen — ditambah halaman awal yang dikonfigurasi
(PDDikti, situs kampus), lalu menyimpannya sebagai teks bertanggal di
`corpus-lokal/eksternal/` untuk diindeks seperti dokumen biasa.

**Mengapa salinan, bukan pengambilan langsung.** Bukti akreditasi diaudit:
asesor harus dapat melihat halaman yang sama dengan yang Anda kutip. Halaman web
dapat berubah setelah dikutip, maka setiap salinan mencatat URL dan waktu
pengambilannya, dan jawaban menyitasi salinan beku itu. Kartu sitasi tetap
menyediakan tautan ke halaman aslinya, sehingga pertanyaan seperti *"tampilkan
alamat profil SINTA Pak Handri Santoso"* dijawab dengan URL lengkap yang dapat
langsung diklik.

Setiap seed membawa polanya sendiri untuk menentukan tautan mana yang
ditelusuri, ditulis sebagai `url::regex` pada `BROWSER_SEED_URLS`. Polanya
harus per-seed karena situsnya berbeda: isi PDDikti berada di balik halaman
`/detail-…/`, sedangkan situs kampus menaruh satu halaman per dosen di bawah
`/praktisi-akademisi/dosen/`. Satu pola bersama akan diam-diam tidak menelusuri
apa pun di salah satunya.

| Seed | Menelusuri | Hasil |
|---|---|---|
| Pencarian PDDikti | `/detail-(dosen\|prodi\|pt\|mahasiswa)/` | 84 halaman |
| `/praktisi-akademisi` | `/praktisi-akademisi/dosen/` | 17 halaman dosen |
| Profil SINTA & Scholar | dipanen dari pemetaan dosen | 35 halaman |

Empat temuan teknis menentukan rancangannya — semuanya hasil pengujian di
komputer ini, bukan asumsi:

| Sumber | Kendala | Penanganan |
|---|---|---|
| SINTA | Halaman ke-2 dst. perlu login | Login memakai `SINTA_USER`/`SINTA_PASS`, lalu paginasi diikuti |
| PDDikti | Cloudflare menolak **setiap** browser yang dijalankan Playwright — Chromium, Chrome, Firefox, headless maupun tidak | Chrome dijalankan sebagai proses biasa, lalu dilekati lewat DevTools |
| Chrome | Menolak remote debugging pada profil bawaannya | Berkas cookie disalin ke profil kerja terpisah |
| Google Scholar | Menolak lalu lintas beruntun (429) | Jeda antar permintaan; hanya halaman profil, bukan tiap artikel |

Penanda sumber diulang di sepanjang isi setiap salinan, bukan hanya di
kepalanya. Pencarian bekerja pada potongan, dan potongan yang dipotong dari
tengah dokumen panjang tidak akan membawa jejak asalnya — sehingga jawaban tak
dapat menawarkan tautan balik, dan model tak tahu bahwa potongan itu berasal
dari SINTA.

> Tutup Chrome sebelum menjalankan `snapshot` — porta debug tidak dapat dipakai
> bersamaan. Memerlukan Playwright: `pip install playwright` lalu
> `playwright install chromium`.

---

## Jaminan baca-saja

Korpus berada di shared drive yang dipakai bersama rekan kerja, sehingga aturan
"jangan pernah mengubah sumber" ditegakkan, bukan diasumsikan. Semua jalur
berkas melewati `app/safety.py`:

* **`open_for_reading`** adalah satu-satunya cara sah membaca berkas korpus, dan
  menolak setiap mode yang mengandung `w`, `a`, `x`, atau `+`.
* **`assert_writable`** menjaga setiap penulisan ke disk. Fungsi ini menormalkan
  symlink dan segmen `..` sebelum membandingkan, sehingga
  `data/../G:/Shared drives/...` pun tertangkap.
* **Pemeriksaan saat start** — server menolak berjalan bila `DATA_DIR` ternyata
  mengarah ke dalam korpus.
* Berkas Excel dibuka dengan `openpyxl(read_only=True)`, yang tidak pernah
  memegang handle yang dapat menulis.

Seluruh artefak yang dihasilkan — indeks, vektor, unduhan model, salinan
halaman — disimpan di luar korpus.

Dijaga oleh 16 pengujian khusus di `tests/test_safety.py`, termasuk satu yang
memastikan seluruh proses pemindaian meninggalkan setiap berkas identik byte
demi byte.

---

## Ketahanan indexing

Korpus hidup di shared drive yang bisa berubah saat proses berjalan — rekan
kerja mengganti nama folder, Google Drive menyinkronkan ulang, Excel mengunci
berkas. Empat pengaman menangani hal itu:

| Situasi | Perilaku |
|---|---|
| Indexing terputus di tengah | Vektor di-flush tiap 25 dokumen; dokumen yang kehilangan vektor dikenali saat proses berikutnya dan dibaca ulang |
| Berkas gagal dibaca sesaat | Dokumen gagal tidak menyimpan tanda tangan, sehingga selalu dicoba lagi — bukan dilewati selamanya |
| Folder diganti nama saat proses berjalan | Entri lama terhapus, berkas di lokasi baru terindeks sebagai dokumen baru |
| Shared drive belum tersinkron penuh | Bila pemindaian menemukan kurang dari separuh dokumen yang terindeks, penghapusan **dilewati** — drive setengah tersinkron tampak persis seperti korpus yang dikosongkan |

Vektor yatim — vektor yang baris potongannya sudah tidak ada — dibuang di akhir
setiap proses. Vektor semacam itu tidak merusak jawaban (pencarian melewatinya),
tetapi memakan memori dan menggeser kandidat sungguhan dari daftar top-k.

---

## Cara kerja pencarian

```
pertanyaan
   ├─► embedding BGE-M3 ──► indeks vektor (kosinus)  ─┐
   │                                                  ├─► reciprocal rank fusion
   └─► ekstraksi kata kunci ─► SQLite FTS5 (BM25)    ─┘          │
                                                                 ▼
                                                      reranking cross-encoder
                                                                 │
                                                                 ▼
                                          potongan terbaik ──► prompt ──► model
```

Kedua metode pencarian sama-sama diperlukan. Pencarian vektor memahami bahwa
*"beban mengajar dosen"* dan *"EWMP"* merujuk hal yang sama; pencarian kata
kunci justru yang mampu menemukan nomor SK seperti `SK-023/PRA/XI/2024`.
Penggabungan dilakukan atas **peringkat**, bukan skor mentah, karena kemiripan
kosinus dan BM25 berada pada skala yang tidak sebanding — penjumlahan berbobot
apa pun akan perlu disetel ulang setiap kali korpus berubah.

Indeks disimpan sebagai SQLite biasa ditambah larik NumPy, bukan basis data
vektor khusus. Pada ukuran korpus ini seluruh matriks hanya beberapa puluh
megabita, sehingga perkalian titik menyeluruh menjawab dalam hitungan milidetik
dengan recall **eksak** — indeks aproksimatif hanya menambah dependensi dan
mengurangi recall tanpa manfaat.

---

## Struktur proyek

```
exim/
├── app/
│   ├── config.py            pengaturan, pembacaan variabel lingkungan
│   ├── safety.py            penegakan baca-saja  ← berkas terpenting
│   ├── documents.py         objek nilai lintas tahap, deteksi tautan eksternal
│   ├── extractors.py        ekstraksi teks per format
│   ├── ocr.py               OCR opsional untuk halaman hasil pindai
│   ├── chunking.py          pemotongan sadar-token dengan tumpang tindih
│   ├── embeddings.py        encoder BGE-M3, penentuan perangkat
│   ├── reranking.py         tahap presisi cross-encoder
│   ├── storage.py           katalog SQLite + indeks vektor NumPy
│   ├── conversations.py     sesi percakapan tersimpan (SQLite terpisah)
│   ├── ingestion.py         pemindai korpus, pipeline inkremental
│   ├── retrieval.py         pencarian hibrida dan penggabungan peringkat
│   ├── crawler.py           crawl situs sumber ke corpus-exim (penulis tunggal korpus)
│   ├── crawl_sites.py       rencana crawl per situs: Bea Cukai, …
│   ├── web_sources.py       pengambilan salinan halaman eksternal
│   ├── browser_sources.py   pembacaan lewat browser (login SINTA, PDDikti)
│   ├── language_models.py   Ollama + tiga backend CLI agen
│   ├── rag.py               penyusunan prompt, aliran jawaban
│   ├── answer_streams.py    jawaban di latar belakang, lanjut setelah koneksi putus
│   └── server.py            rute FastAPI dan SSE
├── web/
│   ├── index.html           antarmuka satu halaman (tanpa proses build)
│   ├── app.js               klien berbasis kelas
│   ├── styles.css           tema warna Bea Cukai (navy dan emas)
│   └── vendor/              marked + DOMPurify, disalin lokal
├── tests/                   518 pengujian unit
├── docs/                    arsitektur, konfigurasi, operasional
├── corpus-exim/             salinan situs sumber: <Situs>\<Bagian>\ (hasil crawl)
├── data/                    dihasilkan: indeks, vektor, percakapan, cache model, log
├── main.py                  titik masuk baris perintah
├── crawl-full.cmd           crawl semua situs (task "Exim crawl")
├── serve-background.cmd     server tanpa jendela (task "Exim server")
├── setup.ps1                pemasangan sekali jalan
└── run.ps1                  menjalankan antarmuka web
```

---

## Pengujian

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
.\.venv\Scripts\python.exe -m pytest tests --cov=app --cov-report=term-missing
```

518 pengujian, tanpa akses jaringan dan tanpa mengunduh model — tahap pencarian
dan penjawaban digantikan objek tiruan, sehingga seluruh suite selesai dalam
sekitar 15 detik.

---

## Dokumen hasil pindai

Korpus akreditasi banyak memuat SK dan sertifikat hasil pindai yang tidak
memiliki lapisan teks PDF. Halaman semacam itu dihitung saat indexing dan
dilaporkan di panel samping. Untuk membacanya:

```powershell
.\.venv\Scripts\python.exe -m pip install rapidocr-onnxruntime
```

lalu setel `OCR_ENABLED=1` pada `.env` dan jalankan ulang
`main.py ingest --rebuild`.

RapidOCR dipakai alih-alih Tesseract karena dapat dipasang lewat pip tanpa
instalasi di tingkat sistem. Potongan hasil OCR ditandai di antarmuka dan
ditandai pula kepada model, yang diminta memberi catatan bila mengutip angka
dari sumber tersebut.

---

## Dokumentasi

| Dokumen | Isi |
|---|---|
| [docs/arsitektur.md](docs/arsitektur.md) | Rancangan komponen, alur data, keputusan penting beserta alasannya |
| [docs/konfigurasi.md](docs/konfigurasi.md) | Seluruh variabel lingkungan, nilai bawaan, dan saran penyetelan |
| [docs/operasional.md](docs/operasional.md) | Menjalankan sistem, indexing, penyiapan backend, pemeliharaan |
| [docs/pemecahan-masalah.md](docs/pemecahan-masalah.md) | Gejala, penyebab, dan penanganannya |

Contoh konfigurasi lengkap ada di [`.env.example`](.env.example).

---

## Catatan dan keterbatasan

- **Format Office lama** (`.doc`, `.xls`) tidak terbaca tanpa konverter dan
  dilewati. Hanya `.docx` / `.xlsx` yang didukung.
- **Salinan halaman eksternal tidak diperbarui otomatis.** Jalankan
  `main.py snapshot` secara berkala bila data SINTA atau PDDikti berubah.
- **PDDikti memerlukan Chrome tertutup** saat `snapshot` berjalan, karena porta
  debug tidak dapat dipakai bersamaan.
- **`.env` dapat memuat kredensial** (mis. akun SINTA) dan sudah dikecualikan
  oleh `.gitignore`. Jangan menyimpan kata sandi dalam teks biasa bila dapat
  dihindari.
- **Login hanya membedakan dua akun bersama.** Bila `HOST=0.0.0.0`, seluruh
  dokumen terindeks dapat dibaca siapa pun di jaringan yang memegang kata sandi
  `guest`. Tidak ada akun per orang maupun catatan siapa bertanya apa.
