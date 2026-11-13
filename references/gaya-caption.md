# Gaya caption podcast

Pegangan ini disusun dari revisi final pengguna sendiri. Kalau pengguna merevisi dengan cara
lain di minggu berikutnya, revisi terbaru yang dipakai: perbarui file ini dengan pola umumnya
(jangan menyalin kalimat dari episode tertentu).

## Tampilan
- Font Poppins ExtraBold Italic, ukuran 11, stroke hitam 0,1, shadow hitam (jarak 10, sudut -45°, tanpa blur).
- Posisi tengah, y = -0,167; kanvas 1080×1920; `line_max_width` 0,82.
- **Kuning `#FFBF17` = narasumber** (tamu yang menjelaskan).
- **Peach `#FFA880` = pewawancara/host** (yang bertanya, menyahut, menanggapi).
- Semua caption harus tampil **1 baris**. Batas lebar ada di `calibration.json`.

## Penggalan
- Target **1–4 kata** per caption (rata-rata versi final ±2,5–3 kata, maksimal 5).
- Potong di **jeda bicara/napas**, bukan berdasar tata bahasa. Satu kalimat boleh terpecah
  di tengah frasa bila pembicara memang berhenti sejenak di situ
  (pola: `kata kerja / sisa kalimat`, `kata sambung / sisa kalimat`).
- **Setiap ganti pembicara = caption baru**, walaupun cuma satu kata.
- Satu kata sendirian boleh kalau memang diucapkan terpisah (mis. "makanya", "terus").
- Ungkapan/idiom bahasa Inggris tetap utuh dalam satu caption bila muat.

## Filler dan sahutan
- Buang filler pembuka yang tidak bermakna: *oh, nah, hmm, iya* di awal caption
  ("nah iya tapi …" → "iya tapi …", "hmm kondisi …" → "kondisi …").
- Hapus caption yang hanya berisi filler/sahutan tumpang tindih ("nah", "iya iya", "oke").
- "oke" di depan kalimat yang bukan bagian isi dibuang ("oke gitu" → "gitu").
- **Pertahankan** "oh" yang bermakna (reaksi terkejut/paham, "oh gitu" sebagai respons host),
  "ya ya ya", "ya kan", dan kata lisan/dialek seperti **"toh"** (bukan typo).
- Pengulangan gagap dibiarkan ("dengan dengan …", "di di …").

## Ejaan dan istilah
- "aku" huruf kecil (bukan "Aku"); nama orang kapital; merek dan singkatan kapital (mis. nama aplikasi, "MRT").
- Kata ulang pakai tanda hubung ("bener-bener", "beda-beda").
  Bila satu kata diucapkan dua kali terpisah di dua caption sebagai bentuk jamak → gabung
  jadi kata ulang dalam satu caption.
- Bilangan tingkat ditulis huruf ("yang kedua", bukan "ke-2").
- Istilah Inggris dieja benar; transkripsi otomatis sering menulisnya sesuai bunyi
  — kembalikan ke ejaan Inggris yang benar, dan perhatikan bentuk kata benda vs kata kerja.
- Kata yang salah dengar diperbaiki dengan mendengarkan, bukan ditebak dari konteks saja;
  kata yang terlewat oleh transkripsi otomatis ditambahkan.
- "nafas" dibiarkan (pengguna tidak mengubahnya).

## Menentukan pembicara (pelajaran)
Tebakan dari isi ucapan paling sering meleset di sahutan pendek dan pergantian cepat:
- Komentar singkat di tengah pertanyaan host bisa jadi milik narasumber, begitu juga jawaban
  satu kata ("enggak") atas pertanyaan balik narasumber.
- Satu kalimat bisa dimulai narasumber lalu diselesaikan host (atau sebaliknya): pecah di titik gantinya.
- Petunjuk berguna: host dan narasumber sering memakai kata ganti berbeda ("saya" vs "aku");
  kalimat tanya umumnya host; narasumber sering menutup penjelasan dengan "kayak gitu kak".
Selalu tandai sahutan pendek dan pergantian cepat sebagai "perlu dicek".
