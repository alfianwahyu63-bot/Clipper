# VIRALCUT STUDIO — PWA Auto Video Clipper

Prototipe aplikasi web HTML/PWA + backend Python untuk **video yang Anda miliki atau telah diberi izin penggunaannya**. Bisa ditambahkan ke layar utama melalui Chrome Android dan desktop setelah di-host lewat HTTPS (atau `localhost` di komputer sendiri).

## Fitur yang sudah diimplementasikan

- Input **link YouTube** (link video/Shorts) lewat `yt-dlp` di server **jika pengguna memiliki izin dan akses diizinkan oleh platform**, atau upload file MP4/MOV/MKV/WEBM.
- Pilih 1, 3, 5, atau 8 klip, dengan durasi 15/30/45/60 detik, dan rasio 9:16, 1:1, atau 16:9.
- Analisis audio tiap detik dan **ranking editorial 0–100** berdasarkan energi, dinamika, kepadatan ucapan. **Opsional** transkripsi `faster-whisper` untuk mendeteksi hook, pertanyaan, kata pemicu perhatian, dan menghasilkan subtitle SRT + subtitle tertanam (bila ffmpeg mendukung).
- Render otomatis MP4 H.264/AAC memakai FFmpeg; preview dan unduh hasil langsung dari aplikasi.
- Antarmuka responsive, mode demo, manifest & service worker PWA, tombol instal jika browser mendukung.

**Penting:** Skor bukan jaminan/prediksi angka views. Akurasi tergantung kualitas audio/transkripsi dan konteks video. Cropping vertikal menggunakan *center crop*; subjek di luar tengah bisa terpotong. PWA tidak berarti server pemrosesan bekerja saat offline.

## Cara menjalankan di Windows / macOS / Linux

1. Instal **Python 3.10+**, **FFmpeg** (termasuk `ffprobe`) dan masukkan FFmpeg ke PATH.
2. Buka folder ini di terminal/Command Prompt.
3. Instal paket:

   ```bash
   python -m pip install -r requirements.txt
   ```

   Opsional, untuk transkripsi AI + subtitle otomatis:

   ```bash
   python -m pip install -r requirements-ai.txt
   ```

   Model Whisper `tiny` diunduh pertama kali saat dipakai; cukup CPU, tetapi untuk video panjang memerlukan waktu. Atur `WHISPER_MODEL=base` untuk kualitas lebih tinggi, dengan konsekuensi beban CPU lebih besar.

4. Jalankan:

   ```bash
   python app.py
   ```

5. Buka `http://localhost:8000` di Chrome pada **komputer yang menjalankan server**.
6. Pilih video, atur durasi, centang bahwa Anda memiliki hak, klik **Generate Viral Clips**. Hasil disimpan di `workspace/<id>/`.

**Tips Windows:** tersedia `start_windows.bat` untuk instalasi dan menjalankan server. Jika FFmpeg belum ditemukan, instal dulu dari sumber tepercaya.

## Agar dapat diinstal dari Chrome Android

- Deploy ke hosting yang mampu menjalankan **Python + FFmpeg** (VPS, server Docker, atau layanan hosting backend), aktifkan **HTTPS**.
- Buka URL `https://domain-anda/...` di Chrome Android, pilih menu `⋮` → **Install aplikasi** atau **Tambahkan ke layar utama**.
- `beforeinstallprompt` hanya muncul pada browser/kondisi yang mendukung. Bila hanya ada *Tambahkan ke layar utama*, fungsinya mungkin sebagai shortcut.
- `index.html` yang dibuka langsung sebagai file **bukan** server backend dan tidak menghasilkan MP4. **GitHub Pages saja** tidak cukup untuk proses Python/FFmpeg.
- Dockerfile tersedia untuk deployment. Build `docker build -t viralcut .`; jalankan `docker run -p 8000:8000 viralcut`. Docker default hanya menginstal analisis audio; untuk Whisper tambahkan `pip install -r requirements-ai.txt` pada image jika server memadai.

## Etika penggunaan YouTube

Ketentuan YouTube membatasi pengunduhan, pengolahan, dan penggunaan konten tanpa izin/otorisasi. Gunakan sumber media asli Anda bila impor lewat link tidak diizinkan atau mengalami kegagalan. Aplikasi ini tidak mencoba menembus DRM, video privat, pembayaran, atau kontrol akses. Opsi YouTube yang tersedia secara teknis tidak menjamin semua video bisa diproses.

## Struktur

```
app.py                   REST API FastAPI + pemrosesan
static/index.html        HTML/CSS/JavaScript frontend responsif
static/manifest.webmanifest
static/sw.js             PWA offline shell
static/icons/            ikon PWA
requirements.txt         paket backend dasar
requirements-ai.txt      opsional faster-whisper
Dockerfile               contoh deployment cloud
workspace/               hasil video yang dibuat saat menjalankan (tidak termasuk dalam arsip)
```

## Batasan prototipe dan hardening sebelum produksi

- Saat ini penyimpanan job dalam memori; job akan hilang setelah restart server, sedangkan berkas lokal masih ada. Untuk produk multi-pengguna, perlu database, login, kuota, dan antrian worker.
- Tambahkan autentikasi, batas upload proxy, rate limit, background queue, moderasi, batas penyimpanan, dan pembersihan file berkala sebelum mempublikasikan ke internet.
- Jangan menyimpan video berhak cipta atau data privat orang tanpa izin.
- FFmpeg yang dibundel tanpa `libass` dapat menghasilkan MP4 tanpa subtitle tertanam. File SRT tetap dibuat dan dapat disematkan saat editing.
- Beberapa host mematikan proses latar belakang atau tidak menyediakan disk persisten. Gunakan server yang mendukung workload video.
