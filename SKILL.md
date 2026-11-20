---
name: caption-podcast-capcut
description: Merapikan caption/subtitle video pendek (shorts/reels) potongan podcast di proyek CapCut desktop - cek typo, ejaan dan istilah terhadap audio, pastikan semua caption tampil 1 baris, pecah caption di jeda bicara, buang filler, dan beri warna berbeda untuk narasumber vs pewawancara langsung di file proyek CapCut. Gunakan skill ini setiap kali pengguna menyebut caption, subtitle, teks otomatis, transkripsi, typo caption, caption 2 baris, warna pembicara, proyek/timeline CapCut untuk konten podcast mingguan, text box/box reels, klik mouse, watermark, atau memotong/merender export jadi short terpisah, walaupun tidak menyebut "skill" — termasuk "kerjakan podcast minggu ini", "cek caption short minggu ini", "rapikan subtitle proyek 1120", "pasang box dan watermark", "potong export jadi 10 file", atau "pelajari revisi saya di short 1".
---

# Caption podcast CapCut

Pengguna mengunggah potongan podcast setiap minggu. Beberapa video short (1.mp4, 2.mp4, …)
digabung jadi satu timeline CapCut, captionnya dibuat dengan transkripsi otomatis CapCut, lalu
direvisi. Skill ini mengerjakan paket mingguan langsung di file proyek CapCut: gaya + revisi
caption, text box + klik mouse di awal tiap short, watermark sepanjang tiap short, lalu memotong
hasil export jadi satu file per short.

Permintaan "kerjakan podcast minggu ini, proyek X" = seluruh paket: langkah 1–4 (caption),
lalu 7 (box, klik, watermark). Setelah pengguna export: langkah 8 (potong).

Gaya rumah (warna, penggalan, filler, ejaan) ada di `references/gaya-caption.md`. **Baca file itu
sebelum menyusun plan.** Isinya dipelajari dari revisi final pengguna, dan revisi pengguna
mengalahkan aturan tertulis: kalau pengguna merevisi dengan cara lain, ikuti dan perbarui file itu.

Semua pekerjaan teknis memakai `scripts/capcut_caption.py` (pustaka standar Python + ffmpeg +
whisper-cli). Sebut `S=~/.claude/skills/caption-podcast-capcut/scripts/capcut_caption.py`.
Simpan file kerja (dump, words, plan, csv) di folder kerja pengguna atau scratchpad, bukan di
folder proyek CapCut.

## Prinsip keamanan file proyek

File proyek adalah kerja pengguna; kesalahan menulis bisa membuat proyek tidak bisa dibuka.
- Selalu `backup` sebelum menulis apa pun, dan sebut lokasi zip-nya ke pengguna.
- Tulis hanya saat CapCut tertutup (`status` memeriksa ini; `apply --write` menolak bila berjalan).
  Baca boleh kapan saja.
- CapCut menyimpan draft yang sama di beberapa salinan (`draft_info.json`, `.bak`, `template-2.tmp`,
  dan di `Timelines/<id>/`). Skrip menulis ke semuanya; jangan menulis manual ke satu file saja.
- `apply` menolak bila proyek berubah sejak `dump` (md5) — artinya pengguna mengedit lagi;
  dump ulang dan susun plan dari keadaan terbaru, jangan menimpa revisi pengguna.
- Jalankan `apply` tanpa `--write` dulu (dry-run), periksa laporannya, baru `--write`.

## Alur kerja

### 1. Kenali proyek
```
python3 $S find                 # proyek terbaru
python3 $S status <proyek>      # klip video, gaya, batas lebar, CapCut jalan/tidak
```
Konfirmasi ke pengguna proyek mana yang dimaksud bila ada keraguan (nama folder biasanya tanggal, mis. `MMDD`).

### 2. Ambil caption dan transkripsi pembanding
```
python3 $S dump <proyek> captions.json --text captions.txt
python3 $S words <proyek> words.json      # Whisper per kata, per klip video, waktu absolut
```
`captions.txt` adalah daftar ringkas per klip (nomor, waktu, pembicara A/B, teks) untuk dibaca.
`words.json` berisi kata+waktu dan transkrip utuh per klip. Model yang tersedia mungkin kecil
(base): bagus untuk menemukan perbedaan, tidak cukup untuk memutuskan sendiri. Bila Whisper dan
caption berbeda, putuskan dengan konteks + gaya rumah, dan tandai yang tetap ragu.

### 3. Susun plan
Plan adalah JSON yang menyebut caption mana yang diubah, nomor = `idx` dari dump:
```json
{
  "dump": "captions.json",
  "captions": {
    "12": ["A|jadi menurut aku", "A|itu penting"],
    "22": ["B|terus gimana", "A|ya tergantung"],
    "57": [],
    "90": ["=|ternyata begitu"]
  }
}
```
- Setiap entri = daftar potongan berurutan; `A|` narasumber (kuning), `B|` pewawancara (peach),
  `=|` pertahankan warna yang ada. Daftar kosong = hapus caption.
- Teks potongan boleh memperbaiki typo, membuang filler, atau mengganti kata; skrip
  menyejajarkan kata lama-baru untuk menentukan waktu tiap potongan.
- Caption yang tidak disebut tidak disentuh.

Yang dipertimbangkan per caption (detail di `references/gaya-caption.md`):
1. Typo/ejaan/istilah — bandingkan dengan transkrip Whisper.
2. Pembicara — tentukan dari isi: pertanyaan dan sahutan host = B, penjelasan narasumber = A.
   Audio podcast biasanya mono/identik L-R, jadi tidak bisa dipisah per mic.
   Sahutan pendek sering salah tebak: masukkan ke daftar "perlu dicek".
3. Penggalan 1–4 kata di jeda bicara; setiap ganti pembicara = caption baru.
4. Filler pembuka dibuang, kata lisan bermakna dipertahankan.

Untuk timeline panjang, kerjakan per klip agar plan tetap terbaca.

### 4. Terapkan
```
python3 $S backup <proyek> <folder-kerja>
python3 $S apply <proyek> plan.json --words words.json            # dry-run
python3 $S apply <proyek> plan.json --words words.json --write
python3 $S dump <proyek> captions_after.json                       # untuk perbandingan berikutnya
```
Waktu potongan diambil dari timestamp kata CapCut bila teks belum diedit, lalu dari Whisper,
terakhir proporsi panjang teks. Pinggiran caption asli tidak digeser. Laporan dry-run menyebut
sumber waktu tiap potongan — potongan "proporsi" sebutkan ke pengguna sebagai yang perlu dicek
telinga. Bila ada PERINGATAN lebar, pecah lagi potongan itu.

### 5. Verifikasi dari export
Minta pengguna membuka proyek di CapCut (memastikan proyek terbuka normal) lalu export (720p cukup).
```
python3 $S lines <proyek> <export.mp4> lines.csv [--from 0 --to 120] [--frames frames/]
```
Menghitung jumlah baris dan warna yang terlihat di tengah durasi tiap caption. Lihat beberapa
frame dengan mata untuk memastikan deteksinya benar. Jika gaya caption baru (font/ukuran/kanvas
lain) dan `status` menyebut belum dikalibrasi: jalankan `lines` pada export yang memiliki caption
1 dan 2 baris, lalu `python3 $S calibrate <proyek> lines.csv`.

### 6. Belajar dari revisi pengguna
Bila pengguna bilang sudah merevisi sebagian (mis. "short 1 sudah final"):
```
python3 $S dump <proyek> captions_final.json
python3 $S compare captions_after.json captions_final.json
```
Rangkum polanya ke pengguna (penggalan, pembicara yang dikoreksi, kata yang diubah, filler,
timing), terapkan ke klip yang belum final, dan perbarui `references/gaya-caption.md` dengan
pelajaran baru yang berlaku umum (bukan contoh satu kalimat saja).

### 7. Gaya, text box, klik mouse, watermark (dari template)
Template ada di `templates/kpods.json` (gaya caption Poppins ExtraBold Italic 11 + posisi,
box PNG ber-crop 1:1 + keyframe scale + durasi 5 dtk, klik "Mouse click 1" dari pustaka CapCut,
watermark.mov). Semua perintah dry-run dulu, lalu `--write`; backup dulu sebelum menulis.
```
python3 $S restyle <proyek>                      # caption masih gaya bawaan CapCut -> gaya rumah
python3 $S boxes <proyek> <folder-png>           # PNG ...-01.png di awal klip 1, -02 di klip 2, dst + klik mouse
python3 $S overlay <proyek> [--file watermark.mov]   # dari detik 0 file sampai akhir tiap klip
```
- `restyle` dijalankan **sebelum** `apply` (batas lebar 1 baris dikalibrasi untuk gaya rumah),
  lalu `dump` ulang karena md5 berubah.
- Box/klik/overlay yang sudah ada di awal klip dilewati, jadi aman dijalankan ulang atau bila
  pengguna sudah memasang contoh di klip 1.
- `boxes` memeriksa isi PNG masih di dalam area crop template; bila PERINGATAN muncul, posisi
  kotak di PNG baru berbeda — tanyakan ke pengguna sebelum menulis.
- Klik mouse memakai file cache CapCut. Bila cache hilang, skrip berhenti dan meminta pengguna
  drag "Mouse click 1" sekali dari CapCut ke proyek; skrip lalu memakai file itu.
- Bila pengguna mengganti desain (box, durasi, animasi, watermark baru, gaya caption): minta dia
  pasang satu contoh di klip 1, lalu `python3 $S template-save <proyek>` (opsi `--parts box,click`).

### 8. Potong export jadi satu file per short
Pengguna export **seluruh** timeline sekali dari CapCut, lalu:
```
python3 $S split <proyek> <export.mp4> <folder-keluar> --name "Nama Narasumber"
```
Hasil `<nama> - 01.mp4` … tepat di batas frame tiap klip (codec mengikuti export, bitrate
±1,4×). Skrip menolak bila durasi export ≠ timeline, dan mencetak jumlah frame tiap file (harus OK).

## Laporan ke pengguna
Bahasa Indonesia, ringkas, berisi:
- Apa yang berubah (jumlah caption sebelum → sesudah per klip, contoh penggalan, typo diperbaiki).
- Tabel "perlu dicek": waktu (mm:ss), teks, warna yang dipilih, alasan ragu — terutama pembicara
  dan potongan berwaktu "proporsi".
- Lokasi backup dan apa yang belum diverifikasi (mis. belum di-export).
Buat juga CSV pembicara bila pengguna ingin mengecek di spreadsheet.

## Batasan yang perlu disampaikan bila relevan
- Pembicara ditebak dari isi ucapan; model pemisah suara (diarization) belum terpasang. Bila
  pengguna ingin, tawarkan memasang model offline (mis. sherpa-onnx) — perlu izin unduh.
- Versi CapCut baru bisa mengubah/mengenkripsi format draft; skrip akan berhenti dengan pesan.
- Skrip mengharapkan satu track teks dan caption satu gaya per teks.
- Track video pertama = klip utama; overlay (box, watermark) ada di track video berikutnya.
- CapCut tidak bisa di-export dari luar aplikasi: export selalu dilakukan pengguna.
