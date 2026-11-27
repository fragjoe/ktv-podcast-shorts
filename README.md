# ktv-podcast-shorts

Skill Claude Code untuk merapikan caption video pendek potongan podcast langsung di file proyek
**CapCut desktop (macOS)**: cek typo dan istilah dengan Whisper, memastikan semua caption tampil 1 baris,
memecah caption di jeda bicara, membuang filler, dan memberi warna berbeda untuk narasumber dan pewawancara.
Juga memasang gaya caption, text box + klik mouse dan watermark di tiap short, lalu memotong export jadi satu file per short.

## Pasang

```bash
git clone https://github.com/fragjoe/ktv-podcast-shorts.git ~/.claude/skills/caption-podcast-capcut
```

Lalu di Claude Code cukup minta, misalnya: "cek caption proyek 1120", atau panggil `/caption-podcast-capcut`.

## Kebutuhan

- macOS dengan CapCut desktop (proyek di `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/`)
- Python 3 (hanya pustaka standar)
- `ffmpeg` dan `whisper-cli` (whisper.cpp) beserta model `ggml-*.bin`

## Isi

| File | Fungsi |
|---|---|
| `SKILL.md` | Alur kerja dan aturan keamanan yang dibaca Claude |
| `references/gaya-caption.md` | Gaya caption rumah (warna, penggalan, filler, ejaan) |
| `scripts/capcut_caption.py` | Alat: `find`, `status`, `backup`, `dump`, `words`, `lines`, `calibrate`, `apply`, `compare`, `template-save`, `restyle`, `boxes`, `overlay`, `split`, `mics`, `audiotrack` |
| `templates/kpods.json` | Gaya caption, box PNG (crop, posisi, keyframe), klik mouse, watermark — diambil dengan `template-save` |
| `calibration.json` | Batas lebar 1 baris per gaya font/ukuran/kanvas |

Skrip selalu bisa dicoba tanpa menulis (`apply` tanpa `--write`), membuat backup lewat `backup`,
dan menolak menulis saat CapCut masih terbuka.

## Memperbarui

Perubahan gaya dari revisi terbaru disimpan di `references/gaya-caption.md`. Setelah diperbarui:

```bash
cd ~/.claude/skills/caption-podcast-capcut && git add -A && git commit -m "Perbarui gaya caption" && git push
```
