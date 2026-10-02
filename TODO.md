# TODO — korpus ekspor-impor

Dijeda 30 September 2026. Prioritas dari pengguna: **khusus ekspor-impor**, dan
yang pertama **makanan, hewan, dan tumbuhan** (punya izin sendiri).

## Selesai, belum di-commit

- [x] BPOM (`--site bpom`, `--site e-bpom`) ke `corpus-exim\Indonesia\BPOM\`.
  - JDIH BPOM: 83 halaman peraturan + 77 PDF, dari pencarian perihal
    "pemasukan", "impor", "ekspor". Terjemahan Inggris dan naskah urgensi dilewati.
  - e-BPOM: FAQ (SKI, SKE, rekomendasi impor, post border) + halaman unduhan
    dengan 17 PDF. Daftar perusahaan prioritas dan materi usang dilewati.
  - Export Consultation Desk (kerjasamahumas.pom.go.id/ecd) tidak diambil:
    memasang pemeriksaan bot (HTTP 468).
- [x] Crawler mengenali PDF dari bita awalnya bila server tidak menyebut tipe
  (JDIH BPOM mengirim PDF tanpa `Content-Type`). Tes: 602 lulus.
- [x] README (baris BPOM, daftar `--site`) dan `crawl-full.cmd` diperbarui.
- [ ] Commit dan push perubahan di atas (hanya bila diminta).

## Sedang dikerjakan: hewan, tumbuhan, satwa liar

### Badan Karantina Indonesia — dipetakan, belum ada rencana

robots.txt mengizinkan semua (portal dan JDIH).

- **Portal** karantinaindonesia.go.id (Next.js): hampir tanpa isi persyaratan.
  FAQ "Tanya Karantina" hanya ada sebagai JSON di
  `https://karantinaindonesia.go.id/api/faqs`: 30 tanya jawab (hewan 7,
  tumbuhan 7, ikan 6, sistem SSm/prior notice 10), field `question`, `answer`,
  `category`.
  - Perlu fitur crawler kecil: membaca respons JSON dan menulis field terpilih
    sebagai teks.
- **JDIH** jdih.karantinaindonesia.go.id (Laravel Livewire): lewat GET hanya
  **10 dokumen terbaru per jenis**
  (`/document/peraturan?bentuk_peraturan=<JENIS>`). Halaman berikutnya hanya
  lewat aksi Livewire `goToPage` (POST); `?page=`, `search`, dan `perPage`
  diabaikan. Tidak ada sitemap atau umpan JDIHN.
  - Jumlah per jenis: PERBAN 29, Keputusan setingkat Eselon 1 27, KEPKA 13,
    KEPMEN 4, SE 4, PERMEN 3 (tidak relevan), KEPUTUSAN 1.
  - Detail `/document/<uuid>/detail` dan PDF-nya bisa diambil lewat GET.
  - Yang relevan di 10 teratas, antara lain:
    - PERBAN 1/2024 jo. 5/2025 (komoditas wajib periksa karantina);
    - PERBAN 2/2025 (jenis hama dan penyakit karantina);
    - PERBAN 3/2025 (media pembawa yang ditolak negara tujuan);
    - PERBAN 4/2025 (lintas batas);
    - PERBAN 6/2025 (barang yang ditahan);
    - PERBAN 9/2025 (pengamanan maksimal);
    - KEPKA 4719/2025 (tempat pemasukan dan pengeluaran);
    - KEPKA 2664 (hama dan penyakit hewan karantina);
    - KEPKA tentang Best Trust;
    - Kepmenkeu 18/KM.4/2025 (barang dibatasi impor/ekspor berdasarkan
      Peraturan Badan Karantina) dan 19/KM.4/2025;
    - Keputusan Deputi Karantina Ikan 8/2025 (keamanan dan mutu pangan produk
      ikan);
    - SE tentang PMK dan Instalasi Karantina Hewan.
  - Banyak dokumen lain bersifat internal (tunjangan, arsip, kepegawaian,
    JDIH): saring dengan `link_text_pattern` atau `title_pattern`.
- Pilihan yang perlu diputuskan:
  1. Ambil 10 terbaru per jenis dengan saringan judul (mudah, cakupan
     terbatas).
  2. Tambah dukungan paginasi Livewire di crawler (lengkap, lebih rumit).
  3. Ambil PERBAN dari JDIH BPK (jenis 293, 30 dokumen), lalu JDIH Karantina
     hanya untuk KEPKA, SE, dan Keputusan Deputi.

### Ditjen PKH (Kementan) — belum dipetakan

- ditjenpkh.pertanian.go.id: robots.txt mengizinkan semua; HTML berisi teks
  (ada skrip Cloudflare, tidak menghalangi).
- Calon halaman:
  - `/informasi/daftar-unit-usaha-asal-pemasukan-produk-hewan-yang-telah-disetujui`
  - `/faq`
  - `/informasi/...` (kesmavet, pakan, perbibitan)
  - rekomendasi pemasukan/pengeluaran ternak, bibit, produk hewan, obat
    hewan
- Permentan ada di jdih.pertanian.go.id (robots belum dicek) atau JDIH BPK
  (jenis 134/135).

### KSDAE (Kementerian Kehutanan) — belum dipetakan

- ksdae.kehutanan.go.id: tidak ada robots.txt (404), HTML berisi teks. Domain
  lama ksdae.menlhk.go.id tidak menjawab.
- Cari: izin angkut satwa dan tumbuhan liar ke luar negeri (SATS-LN), CITES,
  kuota, menu "Regulasi".
- Peraturan: jdih.kehutanan.go.id (`/new2/`, tidak ada robots.txt).

## Ditunda atas permintaan pengguna

- [ ] **JDIH BPK** (peraturan.bpk.go.id): rancangan dan hitungan sudah ada.
  - Paginasi `p` mulai dari 1 (p=0 = p=1), jadi pakai
    `page_url_template="{stem}&p={number}"`.
  - Jenis: UU 8, PP 10, Perpres 11, Permendag 67, Kepmendag 68 (kosong),
    Permentan 134, Kepmentan 135, Permen KP 139, Kepmen KP 140, PerBPOM 230/199,
    KepKa BPOM 200, Badan Karantina 293.
  - Tema: 8 (bea cukai, ekspor-impor), 50 (pangan, pertanian, peternakan),
    45 (perikanan).
  - Saringan `tentang=`: impor, ekspor, pemasukan, pengeluaran, karantina.
  - Permendag ekspor 369 dan impor 188, banyak yang sudah dicabut. Putuskan
    apakah hanya yang berlaku (status ada di halaman detail: "Berlaku" /
    "Tidak Berlaku").
- [ ] KKP: kkp.go.id/djprl (SAJI-LN, ikan dilindungi) menolak skrip (HTTP 403);
  ptsp.kkp.go.id terjangkau.
- [ ] ESDM, BSN, Kemenperin.
- [ ] Salinan pajak: saring sekitar 100 dari 10.250 berkas
  `D:\ALFA\djp\corpus-pajak` (PPN/PPh 22 impor, Kawasan Berikat, KITE).
- [ ] Situs yang isinya diisi JavaScript: cari endpoint datanya (tanpa
  browser).
  - INSW (lartas per HS; bab 01–24 dulu)
  - exim.kemendag.go.id
  - customs.gov.vn
  - dichvucong.gov.vn
- [ ] Vietnam:
  - Cục Bảo vệ thực vật (ppd.gov.vn), Cục Thú y (cucthuy.gov.vn), VFA
    (vfa.gov.vn) — prioritas makanan/hewan/tumbuhan
  - SPS dan TBT Vietnam (perlu sertifikat perantara)
  - Trung tâm WTO
  - MOIT, MOF, SBV
  - Otoritas CITES Vietnam (cites.vn tidak menjawab)
  - Perwakilan dagang RI di Vietnam
- [ ] Công báo: saringan judul belum menangkap aturan satwa liar/CITES (mis.
  "hoang dã", "CITES").
- [ ] Công báo: Thông tư 38/2015/TT-BTC (aturan pokok prosedur kepabeanan
  Vietnam) tidak ada di korpus, sedangkan pendahulunya 128/2013 dan perubahan
  121/2025 ada. Cek apakah daftar Bộ Tài chính terpotong atau judulnya lolos
  dari saringan; bila tidak ada di Công báo, cari sumber resmi lain.

## Sengaja tidak di-crawl

- jdih.kemendag.go.id: robots.txt menolak crawler AI (ClaudeBot, Claude-Web,
  anthropic-ai, GPTBot, dan lainnya). Permendag diambil dari JDIH BPK.
- Export Consultation Desk BPOM: pemeriksaan bot.

## Perapian kecil

- [x] Antarmuka Vietnam/Inggris: label halaman di kartu sumber kini
  diterjemahkan saat ditampilkan ("Trang 28", "Page 28").
- [x] Poster Indonesia dan Inggris diperbarui ke data 30 September; poster
  Vietnam ditambahkan (`publications/posters/poster_vi.pdf`).

- [ ] Satu terjemahan Inggris lolos di BPOM ("Important and Exportation of
  Narcotics…"); perluas `title_pattern` bila perlu, lalu buang salinannya.
- [ ] Judul halaman e-BPOM generik ("FREQUENTLY ASKED QUESTION", "DOWNLOAD").
- [ ] Pastikan indeks otomatis selesai membaca PDF BPOM (sedang berjalan saat
  dijeda).

## Cara melanjutkan

- Uji rencana situs baru tanpa menyentuh korpus: setel `SOURCE_DIR` dan
  `DATA_DIR` ke folder uji di luar proyek *sebelum* mengimpor `app`, lalu
  jalankan `app.crawler.SITE_CRAWLER.run(site, page_limit=3)`. Skripnya,
  `scratch_crawl.py`, ada di folder scratchpad sesi Claude
  (`%TEMP%\claude\d--ALFA-exim\7430a629-…\scratchpad\agents\`). Folder itu
  sementara, jadi buat ulang bila sudah hilang.
- Crawl penuh: `.venv\Scripts\python.exe main.py crawl --site <kunci> --full --no-ingest`.
  Server mengindeks sendiri setiap menit.
