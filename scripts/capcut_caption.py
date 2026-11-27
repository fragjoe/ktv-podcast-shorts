#!/usr/bin/env python3
"""Alat bantu caption proyek CapCut desktop (macOS).

Subperintah:
  find                         daftar proyek CapCut terbaru
  status   PROJECT             cek CapCut berjalan, kunci proyek, salinan draft identik
  backup   PROJECT OUTDIR      zip seluruh folder proyek
  dump     PROJECT OUT.json    ekspor semua caption (urut waktu) + md5 draft
  words    PROJECT OUT.json    transkripsi Whisper per kata (waktu absolut timeline)
  lines    PROJECT EXPORT.mp4 OUT.csv [--from S --to S]   hitung baris & warna tiap caption dari video export
  calibrate PROJECT LINES.csv  hitung batas lebar 1 baris dari hasil `lines`, simpan ke calibration.json
  apply    PROJECT PLAN.json [--words W.json] [--write]   pecah/ubah/hapus/warnai caption
  compare  OLD.json NEW.json   bandingkan dua hasil dump (teks, pembicara, timing)
  template-save PROJECT        simpan gaya caption, box, klik, overlay dari proyek contoh ke templates/kpods.json
  restyle  PROJECT [--write]   pasang gaya caption dari template ke semua caption
  boxes    PROJECT BOXDIR [--write]   box PNG -NN di awal klip ke-NN + klik mouse
  overlay  PROJECT [--file F] [--write]   overlay video (watermark) sepanjang tiap klip
  split    PROJECT EXPORT.mp4 OUTDIR [--name N]   potong export utuh jadi 1 file per klip

PROJECT boleh nama folder proyek (mis. nama folder tanggal) atau path lengkap.
Hanya pustaka standar Python + ffmpeg + whisper-cli.
"""
import argparse, copy, csv, difflib, hashlib, json, os, re, shutil, struct, subprocess, sys, tempfile, uuid, zipfile

HOME = os.path.expanduser('~')
DRAFTS = os.path.join(HOME, 'Movies/CapCut/User Data/Projects/com.lveditor.draft')
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIB = os.path.join(SKILL_DIR, 'calibration.json')
FR = 1e6 / 30
DEFAULT_COLORS = {'A': '#ffbf17', 'B': '#ffa880'}   # A = narasumber, B = pewawancara
WHISPER_MODELS = [os.path.join(HOME, 'Library/Application Support/ScribeOn/models')]

# ---------------------------------------------------------------- util

def die(msg):
    print('GAGAL:', msg, file=sys.stderr); sys.exit(1)

def proj_path(p):
    if os.path.isdir(p): return os.path.abspath(p)
    q = os.path.join(DRAFTS, p)
    if os.path.isdir(q): return q
    die(f'proyek tidak ditemukan: {p}')

def draft_copies(P):
    names = ['draft_info.json', 'draft_info.json.bak', 'template-2.tmp']
    out = [os.path.join(P, n) for n in names]
    tl = os.path.join(P, 'Timelines')
    if os.path.isdir(tl):
        for t in os.listdir(tl):
            if os.path.isdir(os.path.join(tl, t)):
                out += [os.path.join(tl, t, n) for n in names]
    return [f for f in out if os.path.exists(f)]

def md5(f):
    return hashlib.md5(open(f, 'rb').read()).hexdigest()

def load_draft(P):
    f = os.path.join(P, 'draft_info.json')
    raw = open(f, 'rb').read()
    try: return json.loads(raw)
    except Exception: die('draft_info.json tidak bisa dibaca sebagai JSON (mungkin terenkripsi oleh versi CapCut ini)')

def capcut_running():
    r = subprocess.run(['pgrep', '-x', 'CapCut'], capture_output=True, text=True)
    return bool(r.stdout.strip())

def hex2rgb(h):
    h = h.lstrip('#'); return [int(h[i:i+2], 16) / 255 for i in (0, 2, 4)]

def rgb2hex(c):
    return '#' + ''.join(f'{round(x*255):02x}' for x in c[:3])

def text_segments(d):
    mats = {m['id']: m for m in d['materials']['texts']}
    segs = [s for t in d['tracks'] if t['type'] == 'text' for s in t['segments']]
    segs.sort(key=lambda s: s['target_timerange']['start'])
    return segs, mats

def video_clips(d):
    vids = {m['id']: m for m in d['materials']['videos']}
    clips = []
    main = [t for t in d['tracks'] if t['type'] == 'video'][:1]   # track utama; overlay (box, watermark) diabaikan
    for t in main:
        for s in t['segments']:
            m = vids.get(s['material_id'])
            if not m or m.get('type') != 'video': continue
            clips.append(dict(path=m['path'], name=os.path.basename(m['path']),
                              src=s['source_timerange']['start'] / 1e6,
                              start=s['target_timerange']['start'] / 1e6,
                              dur=s['target_timerange']['duration'] / 1e6,
                              speed=s.get('speed', 1.0)))
    clips.sort(key=lambda c: c['start'])
    return clips

def clip_of(clips, t):
    for i, c in enumerate(clips):
        if c['start'] - 0.05 <= t < c['start'] + c['dur']: return i + 1
    return 0

# ---------------------------------------------------------------- font width (tanpa fontTools)

def font_width_fn(path):
    b = open(path, 'rb').read()
    n = struct.unpack('>H', b[4:6])[0]; tb = {}
    for i in range(n):
        tag, _, off, ln = struct.unpack('>4sIII', b[12+16*i:28+16*i]); tb[tag.decode('latin1')] = off
    upm = struct.unpack('>H', b[tb['head']+18:tb['head']+20])[0]
    nh = struct.unpack('>H', b[tb['hhea']+34:tb['hhea']+36])[0]
    adv = [struct.unpack('>H', b[tb['hmtx']+4*i:tb['hmtx']+4*i+2])[0] for i in range(nh)]
    co = tb['cmap']; nt = struct.unpack('>H', b[co+2:co+4])[0]; cmap = {}
    for i in range(nt):
        _, _, off = struct.unpack('>HHI', b[co+4+8*i:co+12+8*i]); o = co + off
        if struct.unpack('>H', b[o:o+2])[0] != 4: continue
        seg = struct.unpack('>H', b[o+6:o+8])[0] // 2
        ends = struct.unpack(f'>{seg}H', b[o+14:o+14+2*seg]); p = o + 16 + 2*seg
        starts = struct.unpack(f'>{seg}H', b[p:p+2*seg]); p2 = p + 2*seg
        deltas = struct.unpack(f'>{seg}h', b[p2:p2+2*seg]); p3 = p2 + 2*seg
        ros = struct.unpack(f'>{seg}H', b[p3:p3+2*seg])
        for s in range(seg):
            for c in range(starts[s], min(ends[s], 0x24FF) + 1):
                if ros[s] == 0: g = (c + deltas[s]) & 0xFFFF
                else:
                    a = p3 + 2*s + ros[s] + 2*(c - starts[s]); g = struct.unpack('>H', b[a:a+2])[0]
                    if g: g = (g + deltas[s]) & 0xFFFF
                cmap[c] = g
        break
    return lambda t: sum(adv[min(cmap.get(ord(ch), 0), nh-1)] for ch in t) / upm

def style_key(d, mat):
    c = json.loads(mat['content']); st = c['styles'][0]
    return dict(font=st['font']['path'], size=st.get('size'), line_max_width=mat.get('line_max_width'),
                canvas=d.get('canvas_config', {}).get('width'))

def calib_key(k):
    return f"{os.path.basename(k['font'])}|{k['size']}|{k['line_max_width']}|{k['canvas']}"

def get_limit(k):
    try: cal = json.load(open(CALIB))
    except Exception: cal = {}
    v = cal.get(calib_key(k))
    return v.get('safe') if v else None

# ---------------------------------------------------------------- perintah

def cmd_find(a):
    rows = []
    for n in os.listdir(DRAFTS):
        p = os.path.join(DRAFTS, n); f = os.path.join(p, 'draft_info.json')
        if os.path.exists(f): rows.append((os.path.getmtime(f), n, p))
    import time
    for t, n, p in sorted(rows, reverse=True)[:a.limit]:
        try:
            d = json.load(open(os.path.join(p, 'draft_info.json')))
            segs, _ = text_segments(d); clips = video_clips(d)
            info = f"{len(segs)} caption, {len(clips)} klip video, durasi {d['duration']/1e6:.1f}s"
        except Exception: info = 'tidak terbaca'
        print(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(t))}  {n:30s} {info}")

def cmd_status(a):
    P = proj_path(a.project)
    print('CapCut berjalan:', 'YA - tutup dulu sebelum menulis' if capcut_running() else 'tidak')
    print('proyek terkunci (.locked):', os.path.exists(os.path.join(P, '.locked')))
    hs = {f: md5(f) for f in draft_copies(P)}
    print('salinan draft:', len(hs), '| identik:', len(set(hs.values())) == 1)
    d = load_draft(P); segs, mats = text_segments(d)
    print('caption:', len(segs), '| kanvas:', d.get('canvas_config'))
    for i, c in enumerate(video_clips(d), 1):
        print(f"  klip {i}: {c['name']} mulai {c['start']:.2f}s durasi {c['dur']:.2f}s")
    if segs:
        k = style_key(d, mats[segs[0]['material_id']])
        print('gaya:', os.path.basename(k['font']), 'size', k['size'], 'line_max_width', k['line_max_width'],
              '| batas lebar 1 baris:', get_limit(k) or 'BELUM DIKALIBRASI (jalankan lines lalu calibrate)')

def cmd_backup(a):
    P = proj_path(a.project); os.makedirs(a.outdir, exist_ok=True)
    import time
    out = os.path.join(a.outdir, f"backup_{os.path.basename(P)}_{time.strftime('%Y%m%d_%H%M%S')}.zip")
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(P):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, os.path.dirname(P)))
    print(out)

def cmd_dump(a):
    P = proj_path(a.project); d = load_draft(P); segs, mats = text_segments(d); clips = video_clips(d)
    k = style_key(d, mats[segs[0]['material_id']]) if segs else None
    W = font_width_fn(k['font']) if k and os.path.exists(k['font']) else None
    rows = []
    colors = {v: s for s, v in DEFAULT_COLORS.items()}
    for i, s in enumerate(segs, 1):
        m = mats[s['material_id']]; c = json.loads(m['content'])
        col = rgb2hex(c['styles'][0]['fill']['content']['solid']['color'])
        st = s['target_timerange']['start'] / 1e6; en = st + s['target_timerange']['duration'] / 1e6
        wd = m.get('words') or {}
        rows.append(dict(idx=i, start=round(st, 3), end=round(en, 3), clip=clip_of(clips, st), text=c['text'],
                         color=col, speaker=colors.get(col, '?'),
                         width=round(W(c['text'].strip()), 3) if W else None,
                         capcut_words=''.join(wd.get('text', [])).strip() == c['text'].strip() and bool(wd.get('text'))))
    out = dict(project=P, md5=md5(os.path.join(P, 'draft_info.json')), clips=clips,
               style=k, limit=get_limit(k) if k else None, captions=rows)
    json.dump(out, open(a.out, 'w'), ensure_ascii=False, indent=1)
    print(f'{len(rows)} caption -> {a.out}')
    if a.text:
        with open(a.text, 'w') as f:
            cur = None
            for r in rows:
                if r['clip'] != cur:
                    cur = r['clip']; f.write(f"\n=== klip {cur}: {clips[cur-1]['name'] if cur else '?'}\n")
                f.write(f"{r['idx']}\t{r['start']:.2f}-{r['end']:.2f}\t{r['speaker']}\t{r['text']}\n")
        print('teks ringkas ->', a.text)

def find_model(name):
    if name and os.path.exists(name): return name
    cands = []
    for d in WHISPER_MODELS + [os.path.join(HOME, '.cache/whisper'), os.path.join(HOME, 'models')]:
        if os.path.isdir(d):
            cands += [os.path.join(d, f) for f in os.listdir(d) if f.startswith('ggml-') and f.endswith('.bin')]
    if not cands: die('model whisper (ggml-*.bin) tidak ditemukan; berikan --model')
    order = ['large-v3-turbo', 'large', 'medium', 'small', 'base', 'tiny']
    cands.sort(key=lambda f: next((i for i, o in enumerate(order) if o in f), 99))
    return cands[0]

def cmd_words(a):
    P = proj_path(a.project); d = load_draft(P); clips = video_clips(d)
    model = find_model(a.model); print('model:', model)
    words = []; full = []
    with tempfile.TemporaryDirectory() as td:
        for i, c in enumerate(clips, 1):
            wav = os.path.join(td, f'c{i}.wav')
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(c['src']), '-t', str(c['dur'] * c['speed']),
                            '-i', c['path'], '-ar', '16000', '-ac', '1', wav], check=True)
            base = os.path.join(td, f'w{i}')
            subprocess.run(['whisper-cli', '-m', model, '-l', a.lang, '-f', wav, '-ml', '1', '-sow', '-ocsv', '-of', base, '-np'],
                           capture_output=True)
            subprocess.run(['whisper-cli', '-m', model, '-l', a.lang, '-f', wav, '-otxt', '-of', base + 't', '-np'],
                           capture_output=True)
            for r in csv.DictReader(open(base + '.csv')):
                t = r['text'].strip()
                if t: words.append(dict(t=round(c['start'] + int(r['start']) / 1000 / c['speed'], 3),
                                        e=round(c['start'] + int(r['end']) / 1000 / c['speed'], 3), w=t, clip=i))
            full.append(dict(clip=i, name=c['name'], text=open(base + 't.txt').read().strip()))
            print(f"klip {i} {c['name']}: selesai")
    json.dump(dict(model=model, words=words, transcripts=full), open(a.out, 'w'), ensure_ascii=False, indent=1)
    print(f'{len(words)} kata -> {a.out}')

def frame_rgb(video, t, W, H):
    return subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.3f}', '-i', video, '-frames:v', '1',
                           '-vf', f'scale={W}:{H}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout

def cmd_lines(a):
    P = proj_path(a.project); d = load_draft(P); segs, mats = text_segments(d)
    cw = d['canvas_config']['width']; ch = d['canvas_config']['height']
    W = 360; H = round(360 * ch / cw)
    pal = {}
    for m in mats.values():
        c = json.loads(m['content']); pal[rgb2hex(c['styles'][0]['fill']['content']['solid']['color'])] = 1
    pal = {h: [x * 255 for x in hex2rgb(h)] for h in pal}
    out = []
    framedir = a.frames
    if framedir: os.makedirs(framedir, exist_ok=True)
    for i, s in enumerate(segs, 1):
        st = s['target_timerange']['start'] / 1e6; en = st + s['target_timerange']['duration'] / 1e6
        if a.frm is not None and st < a.frm: continue
        if a.to is not None and st >= a.to: continue
        m = mats[s['material_id']]; c = json.loads(m['content'])
        want = rgb2hex(c['styles'][0]['fill']['content']['solid']['color'])
        y = s['clip']['transform']['y']; cy = H * (0.5 - y / 2)
        y0 = max(0, int(cy - H * 0.12)); y1 = min(H, int(cy + H * 0.12))
        mid = (st + en) / 2
        raw = frame_rgb(a.video, mid, W, H)
        if len(raw) < W * H * 3: out.append(dict(idx=i, start=round(st, 3), text=c['text'], lines=-1)); continue
        # piksel teks = warna cocok palet DAN dekat stroke/shadow hitam (membedakan dari kulit/baju)
        def px(xx, yy):
            o = (yy * W + xx) * 3; return raw[o], raw[o+1], raw[o+2]
        def dark(xx, yy):
            if not (0 <= xx < W and 0 <= yy < H): return False
            R, G, B = px(xx, yy); return R + G + B < 180
        rows = []; hits = {h: 0 for h in pal}
        for yy in range(y0, y1):
            cnt = 0
            for x in range(W):
                R, G, B = px(x, yy)
                best = None
                for h, (r2, g2, b2) in pal.items():
                    dd = abs(R - r2) + abs(G - g2) + abs(B - b2)
                    if dd < 60 and (best is None or dd < best[0]): best = (dd, h)
                if best and any(dark(x + dx, yy + dy) for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (3, 3), (-3, 3))):
                    cnt += 1; hits[best[1]] += 1
            rows.append(cnt)
        bands = []; inb = False
        for k, cnt in enumerate(rows):
            on = cnt >= 2
            if on and not inb: s0 = k; inb = True
            if not on and inb: bands.append([s0, k]); inb = False
        if inb: bands.append([s0, len(rows)])
        mg = []
        for b in bands:
            if mg and b[0] - mg[-1][1] <= 3: mg[-1][1] = b[1]
            else: mg.append(b)
        mg = [b for b in mg if b[1] - b[0] >= 8]
        seen = max(hits, key=hits.get) if any(hits.values()) else ''
        out.append(dict(idx=i, start=round(st, 3), text=c['text'], lines=len(mg), color_project=want,
                        color_seen=seen, color_ok=(seen == want)))
        if framedir:
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{mid:.3f}', '-i', a.video, '-frames:v', '1',
                            '-vf', f'scale=360:-1,crop=360:{y1-y0}:0:{y0}', os.path.join(framedir, f'{i:04d}.jpg')])
    with open(a.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['idx', 'start', 'lines', 'color_project', 'color_seen', 'color_ok', 'text'])
        for r in out: w.writerow([r['idx'], r['start'], r['lines'], r.get('color_project'), r.get('color_seen'), r.get('color_ok'), r['text']])
    from collections import Counter
    print('jumlah baris:', dict(Counter(r['lines'] for r in out)))
    print('warna tidak cocok:', [r['idx'] for r in out if r.get('color_ok') is False])
    for r in out:
        if r['lines'] != 1: print(f"  #{r['idx']} {r['start']}s {r['lines']} baris: {r['text']}")

def cmd_calibrate(a):
    P = proj_path(a.project); d = load_draft(P); segs, mats = text_segments(d)
    k = style_key(d, mats[segs[0]['material_id']]); W = font_width_fn(k['font'])
    one, two = [], []
    for r in csv.DictReader(open(a.lines)):
        w = W(r['text'].strip())
        if r['lines'] == '1': one.append(w)
        elif r['lines'] == '2': two.append(w)
    if not one or not two:
        die('butuh contoh caption 1 baris DAN 2 baris di hasil lines untuk kalibrasi')
    mx1, mn2 = max(one), min(two)
    print(f'1 baris terlebar {mx1:.3f} | 2 baris tersempit {mn2:.3f}')
    if mx1 >= mn2: die('data tumpang tindih; periksa hasil lines')
    try: cal = json.load(open(CALIB))
    except Exception: cal = {}
    cal[calib_key(k)] = dict(max_one=round(mx1, 3), min_two=round(mn2, 3), safe=round(min(mx1, mn2 - 0.3), 3))
    json.dump(cal, open(CALIB, 'w'), indent=1)
    print('disimpan:', calib_key(k), cal[calib_key(k)])

def norm(w): return re.sub(r'[^a-z0-9%-]', '', w.lower())

def cmd_apply(a):
    P = proj_path(a.project)
    plan = json.load(open(a.plan))
    dump = json.load(open(plan['dump'])) if 'dump' in plan else None
    if dump and dump['md5'] != md5(os.path.join(P, 'draft_info.json')):
        die('proyek berubah sejak dump dibuat; jalankan dump ulang dan susun plan dari dump terbaru')
    colors = dict(DEFAULT_COLORS); colors.update(plan.get('colors', {}))
    d = load_draft(P); segs, mats = text_segments(d)
    anims = {m['id']: m for m in d['materials']['material_animations']}
    k = style_key(d, mats[segs[0]['material_id']]); W = font_width_fn(k['font'])
    limit = plan.get('max_width') or get_limit(k)
    if not limit: die('batas lebar belum dikalibrasi untuk gaya ini; jalankan lines + calibrate dulu')
    ws = []
    if a.words:
        ws = [(x['t'] * 1e6, norm(x['w'])) for x in json.load(open(a.words))['words'] if norm(x['w'])]
    fr = lambda x: int(round(round(x / FR) * FR))
    edits = plan['captions']; out = []; rep = []; wide = []; dm = set(); da = set()
    for i, s in enumerate(segs, 1):
        key = str(i)
        if key not in edits: out.append(s); continue
        m = mats[s['material_id']]; c = json.loads(m['content']); txt = c['text'].strip()
        if dump: assert dump['captions'][i-1]['text'].strip() == txt, f'caption {i} tidak cocok dengan dump'
        st = s['target_timerange']['start']; en = st + s['target_timerange']['duration']
        pcs = edits[key]
        if not pcs:
            dm.add(m['id']); da.update(s['extra_material_refs']); rep.append(f'#{i} HAPUS "{txt}"'); continue
        pl = []
        for p in pcs:
            spk, _, t = p.partition('|')
            if not t: spk, t = '', p
            pl.append((spk.strip(), t.strip()))
        ot = txt.split(); nt = [w for _, p in pl for w in p.split()]
        sim = difflib.SequenceMatcher(None, txt.lower(), ' '.join(nt).lower()).ratio()
        if sim < 0.5 and not plan.get('allow_rewrite'):
            die(f'caption #{i} "{txt}" -> "{" ".join(nt)}" terlalu berbeda (kemiripan {sim:.2f}); '
                f'periksa nomor idx di plan, atau set "allow_rewrite": true bila memang disengaja')
        # waktu tiap kata asli: kata CapCut > proporsi karakter
        wd = m.get('words') or {}
        wt = [(x, t) for x, t in zip(wd.get('start_time', []), wd.get('text', [])) if t.strip()]
        if len(wt) == len(ot) and all(t.strip() == o for (_, t), o in zip(wt, ot)):
            tt = [st + x * 1000 for x, _ in wt]; srcs = 'capcut'
        else:
            pos = []; o = 0
            for w in ot:
                q = txt.find(w, o); q = o if q < 0 else q; pos.append(q); o = q + len(w)
            tt = [st + (en - st) * q / max(1, len(txt)) for q in pos]; srcs = 'proporsi'
        sm = difflib.SequenceMatcher(None, [w.lower() for w in ot], [w.lower() for w in nt], autojunk=False)
        mp = [None] * len(nt)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            for j in range(j1, j2):
                if tag == 'equal': mp[j] = i1 + (j - j1)
                elif tag == 'replace': mp[j] = min(i2 - 1, i1 + int((j - j1) * (i2 - i1) / (j2 - j1)))
        starts = []; kk = 0; how = []
        for n, (_, p) in enumerate(pl):
            j = kk; oi = mp[j]
            while oi is None and j < len(nt) - 1: j += 1; oi = mp[j]
            if n == 0:
                b = st if oi in (None, 0) else tt[oi]; h = srcs
            else:
                b = tt[oi] if oi is not None else st + (en - st) * kk / max(1, len(nt)); h = srcs
                if srcs == 'proporsi' and ws:
                    t0 = norm(p.split()[0]); lo = (starts[-1] + 0.25e6); hi = en - 0.25e6
                    cand = [x for x, w in ws if lo <= x <= hi and (w[:4] == t0[:4] or (len(t0) <= 3 and w == t0))]
                    if cand: b = cand[0]; h = 'whisper'
            if n > 0: b = max(starts[-1] + 0.25e6, min(en - 0.25e6, b))
            starts.append(fr(b) if n else int(b)); how.append(h); kk += len(p.split())
        for x, y in zip(starts, starts[1:]):
            if y <= x: die(f'caption {i}: potongan terlalu rapat untuk durasinya ({pl})')
        bounds = starts + [en]
        for n, (spk, p) in enumerate(pl):
            if W(p) > limit: wide.append((i, p, round(W(p), 2)))
            if n == 0: s2, m2 = s, m
            else:
                m2 = copy.deepcopy(m); m2['id'] = str(uuid.uuid4()).upper(); d['materials']['texts'].append(m2)
                refs = []
                for r in s['extra_material_refs']:
                    if r in anims:
                        an = copy.deepcopy(anims[r]); an['id'] = str(uuid.uuid4()).upper()
                        d['materials']['material_animations'].append(an); refs.append(an['id'])
                    else: refs.append(r)
                s2 = copy.deepcopy(s); s2['id'] = str(uuid.uuid4()).upper(); s2['material_id'] = m2['id']; s2['extra_material_refs'] = refs
            cc = copy.deepcopy(c); cc['text'] = p
            for stl in cc['styles']:
                stl['range'] = [0, len(p)]
                if spk in colors: stl['fill']['content']['solid']['color'] = hex2rgb(colors[spk])
            m2['content'] = json.dumps(cc, ensure_ascii=False, separators=(',', ':'))
            if spk in colors: m2['text_color'] = colors[spk]
            if p != txt or n: m2['words'] = {'start_time': [], 'end_time': [], 'text': []}
            s2['target_timerange'] = {'start': bounds[n], 'duration': bounds[n+1] - bounds[n]}
            out.append(s2)
        rep.append(f'#{i} ' + ' | '.join(f'{sp or "="}:{p}@{b/1e6:.2f}' for (sp, p), b in zip(pl, starts)) + f'  [{",".join(how)}]')
    d['materials']['texts'] = [m for m in d['materials']['texts'] if m['id'] not in dm]
    d['materials']['material_animations'] = [m for m in d['materials']['material_animations'] if m['id'] not in da]
    tr = [t for t in d['tracks'] if t['type'] == 'text']
    if len(tr) != 1: die('skrip ini mengharapkan tepat 1 track teks')
    tr[0]['segments'] = sorted(out, key=lambda s: s['target_timerange']['start'])
    z = tr[0]['segments']
    for x, y in zip(z, z[1:]):
        if x['target_timerange']['start'] + x['target_timerange']['duration'] > y['target_timerange']['start']:
            die('hasil tumpang tindih; plan tidak ditulis')
    print('\n'.join(rep))
    print(f'segmen: {len(segs)} -> {len(z)} | durasi terpendek {min(s["target_timerange"]["duration"] for s in z)/1e6:.2f}s')
    if wide: print('PERINGATAN potongan melebihi batas 1 baris:', wide)
    if not a.write: print('(dry-run: tidak ada yang ditulis; tambahkan --write)'); return
    if P.startswith(DRAFTS) and capcut_running(): die('CapCut masih berjalan; tutup aplikasinya dulu (Cmd+Q)')
    if os.path.exists(os.path.join(P, '.locked')): die('proyek masih terkunci (.locked); tutup proyek di CapCut')
    hs = {f: md5(f) for f in draft_copies(P)}
    if len(set(hs.values())) != 1: die('salinan draft tidak identik; buka-tutup proyek di CapCut lalu ulangi')
    tmp = os.path.join(P, '.caption_tmp.json')
    json.dump(d, open(tmp, 'w'), ensure_ascii=False, separators=(',', ':'))
    for f in hs: shutil.copyfile(tmp, f)
    os.remove(tmp)
    print('ditulis ke', len(hs), 'salinan draft')

def cmd_compare(a):
    A = json.load(open(a.old)); B = json.load(open(a.new))
    ca = A['captions']; cb = B['captions']
    clips = sorted(set(r['clip'] for r in ca + cb))
    for cl in clips:
        x = [r for r in ca if r['clip'] == cl]; y = [r for r in cb if r['clip'] == cl]
        fx = [f"{r['speaker']} {r['text']}" for r in x]; fy = [f"{r['speaker']} {r['text']}" for r in y]
        if fx == fy and [(r['start'], r['end']) for r in x] == [(r['start'], r['end']) for r in y]:
            print(f'== klip {cl}: tidak berubah ({len(x)} caption)'); continue
        print(f'== klip {cl}: {len(x)} -> {len(y)} caption')
        for l in difflib.unified_diff(fx, fy, lineterm='', n=0):
            if not l.startswith(('---', '+++')): print('  ', l)
        old = {r['text']: r for r in x}; moved = []
        for r in y:
            o = old.get(r['text'])
            if o and (abs(o['start'] - r['start']) > 0.02 or abs(o['end'] - r['end']) > 0.02):
                moved.append(f"{r['text']!r} mulai {r['start']-o['start']:+.2f}s akhir {r['end']-o['end']:+.2f}s")
        if moved: print('   timing digeser:', *moved, sep='\n     ')
        import statistics as stt
        if y: print(f"   kata/caption rata {stt.mean(len(r['text'].split()) for r in y):.2f}, maks {max(len(r['text'].split()) for r in y)}")

# ---------------------------------------------------------------- template aset (gaya, box, klik, overlay)

TEMPLATE = os.path.join(SKILL_DIR, 'templates', 'kpods.json')
STYLE_KEYS = ['border_color', 'border_width', 'border_alpha', 'check_flag', 'font_path', 'font_size', 'has_shadow',
              'shadow_alpha', 'shadow_angle', 'shadow_color', 'shadow_distance', 'shadow_point', 'shadow_smoothing',
              'use_effect_default_color']

def nid(): return str(uuid.uuid4()).upper()

def tilde(o):
    """Ganti path home dengan ~ (repo publik) / kembalikan saat dipakai."""
    s = json.dumps(o, ensure_ascii=False)
    return json.loads(s.replace(json.dumps(HOME)[1:-1], '~'))

def untilde(o):
    def f(x):
        if isinstance(x, str) and x.startswith('~/'): return os.path.expanduser(x)
        if isinstance(x, dict): return {k: f(v) for k, v in x.items()}
        if isinstance(x, list): return [f(v) for v in x]
        return x
    return f(o)

def mat_index(d):
    return {m['id']: (k, m) for k, v in d['materials'].items() if isinstance(v, list)
            for m in v if isinstance(m, dict) and 'id' in m}

def main_clips(d):
    t = [t for t in d['tracks'] if t['type'] == 'video'][0]
    return sorted(t['segments'], key=lambda s: s['target_timerange']['start'])

def write_draft(P, d):
    if P.startswith(DRAFTS) and capcut_running(): die('CapCut masih berjalan; tutup aplikasinya dulu (Cmd+Q)')
    if os.path.exists(os.path.join(P, '.locked')): die('proyek masih terkunci (.locked); tutup proyek di CapCut')
    hs = {f: md5(f) for f in draft_copies(P)}
    if len(set(hs.values())) != 1: die('salinan draft tidak identik; buka-tutup proyek di CapCut lalu ulangi')
    ids = [m['id'] for _, m in mat_index(d).values()]
    if len(ids) != len(set(ids)): die('ada id material ganda; tidak ditulis')
    tmp = os.path.join(P, '.caption_tmp.json')
    json.dump(d, open(tmp, 'w'), ensure_ascii=False, separators=(',', ':'))
    for f in hs: shutil.copyfile(tmp, f)
    os.remove(tmp)
    print('ditulis ke', len(hs), 'salinan draft')

def pack(d, track, seg):
    mi = mat_index(d)
    return dict(track={k: v for k, v in track.items() if k != 'segments'}, segment=seg,
                material=mi[seg['material_id']][1], refs=[list(mi[r]) for r in seg['extra_material_refs']])

def load_template():
    if not os.path.exists(TEMPLATE): die(f'template belum ada ({TEMPLATE}); jalankan template-save pada proyek contoh')
    return untilde(json.load(open(TEMPLATE)))

def cmd_template_save(a):
    """Ambil gaya caption, box (PNG), klik (audio) dan overlay (video) dari proyek contoh."""
    P = proj_path(a.project); d = load_draft(P); mi = mat_index(d); done = set()
    try: tpl = json.load(open(TEMPLATE))
    except Exception: tpl = {}
    tracks = d['tracks']; vids = [t for t in tracks if t['type'] == 'video']
    segs, mats = text_segments(d)
    if segs and 'caption' in a.parts:
        s0 = segs[0]; m0 = mats[s0['material_id']]; st = json.loads(m0['content'])['styles'][0]
        tpl['caption'] = tilde(dict(material={k: m0[k] for k in STYLE_KEYS if k in m0},
                                    style={k: v for k, v in st.items() if k not in ('range', 'fill')}, clip=s0['clip']))
        print('caption:', os.path.basename(m0['font_path']), m0['font_size'])
    for t in vids[1:]:
        for s in t['segments']:
            m = mi[s['material_id']][1]
            if m.get('type') == 'photo' and 'box' in a.parts and 'box' not in done:
                tpl['box'] = tilde(pack(d, t, s)); done.add('box'); print('box:', m['material_name'], s['target_timerange']['duration'] / 1e6, 'dtk')
            if m.get('type') == 'video' and 'overlay' in a.parts and 'overlay' not in done:
                tpl['overlay'] = tilde(pack(d, t, s)); done.add('overlay'); print('overlay:', m['material_name'])
    for t in tracks:
        if t['type'] != 'audio' or 'click' not in a.parts or 'click' in done: continue
        s = t['segments'][0]; m = mi[s['material_id']][1]
        tpl['click'] = tilde(pack(d, t, s)); done.add('click'); print('klik:', m.get('name'), s['target_timerange']['duration'] / 1e6, 'dtk')
    os.makedirs(os.path.dirname(TEMPLATE), exist_ok=True)
    json.dump(tpl, open(TEMPLATE, 'w'), ensure_ascii=False, indent=1)
    print('disimpan ->', TEMPLATE)

def cmd_restyle(a):
    P = proj_path(a.project); d = load_draft(P); T = load_template().get('caption') or die('template caption tidak ada')
    segs, mats = text_segments(d)
    for s in segs:
        m = mats[s['material_id']]; c = json.loads(m['content'])
        m.update(copy.deepcopy(T['material']))
        new = []
        for st in c['styles']:
            x = copy.deepcopy(T['style']); x['range'] = st['range']; x['fill'] = st['fill']; new.append(x)
        c['styles'] = new; m['content'] = json.dumps(c, ensure_ascii=False, separators=(',', ':'))
        s['clip'] = copy.deepcopy(T['clip'])
    if not os.path.exists(T['material']['font_path']): print('PERINGATAN font tidak ditemukan:', T['material']['font_path'])
    print(f"gaya {os.path.basename(T['material']['font_path'])} {T['material']['font_size']} -> {len(segs)} caption")
    if not a.write: print('(dry-run: tambahkan --write)'); return
    write_draft(P, d)

def place(d, T, start, dur=None, src=None, material=None, before=None):
    """Tambah satu salinan segmen template di waktu `start` (mikrodetik). Track dibuat bila belum ada."""
    mi = mat_index(d)
    key = T['material'].get('music_id') or os.path.basename(T['material'].get('path', ''))
    tr = None
    for t in d['tracks']:
        if t['type'] != T['track']['type'] or t is main_track(d): continue
        for s in t['segments']:
            m = mi[s['material_id']][1]
            if (m.get('music_id') or os.path.basename(m.get('path', ''))) == key or \
               (T['material'].get('type') == 'photo' and m.get('type') == 'photo'):
                tr = t; break
        if tr: break
    if tr is None:
        tr = copy.deepcopy(T['track']); tr['id'] = nid(); tr['segments'] = []
        pos = len(d['tracks'])
        if T['track']['type'] == 'video':   # overlay video di atas caption, sebelum track audio
            au = [i for i, t in enumerate(d['tracks']) if t['type'] == 'audio']
            if au: pos = au[0]
        if before:
            for i, t in enumerate(d['tracks']):
                if t['type'] == 'video' and any(os.path.basename(mi[s['material_id']][1].get('path', '')) == before for s in t['segments']):
                    pos = i; break
        d['tracks'].insert(pos, tr)
    m = copy.deepcopy(material or T['material']); m['id'] = nid()
    kind = 'audios' if T['track']['type'] == 'audio' else 'videos'
    d['materials'].setdefault(kind, []).append(m)
    s = copy.deepcopy(T['segment']); s['id'] = nid(); s['material_id'] = m['id']; refs = []
    for k, x in T['refs']:
        x = copy.deepcopy(x); x['id'] = nid(); d['materials'].setdefault(k, []).append(x); refs.append(x['id'])
    s['extra_material_refs'] = refs
    for kf in s.get('common_keyframes') or []:
        kf['id'] = nid()
        for x in kf['keyframe_list']: x['id'] = nid()
    dur = dur or T['segment']['target_timerange']['duration']
    s['target_timerange'] = {'start': int(start), 'duration': int(dur)}
    s['source_timerange'] = {'start': T['segment']['source_timerange']['start'] if src is None else src, 'duration': int(dur)}
    tr['segments'].append(s); tr['segments'].sort(key=lambda x: x['target_timerange']['start'])
    return tr

def main_track(d): return [t for t in d['tracks'] if t['type'] == 'video'][0]

def has_at(d, T, start, tol=40000):
    mi = mat_index(d); key = T['material'].get('music_id') or ('photo' if T['material'].get('type') == 'photo' else os.path.basename(T['material'].get('path', '')))
    for t in d['tracks']:
        if t is main_track(d) or t['type'] != T['track']['type']: continue
        for s in t['segments']:
            m = mi[s['material_id']][1]
            k = m.get('music_id') or ('photo' if m.get('type') == 'photo' else os.path.basename(m.get('path', '')))
            if k == key and abs(s['target_timerange']['start'] - start) < tol: return True
    return False

def alpha_bbox(png, W, H):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', png, '-vf', 'alphaextract,format=gray', '-f', 'rawvideo', '-'],
                         capture_output=True).stdout
    if len(raw) != W * H: return None
    rows = [y for y in range(H) if max(raw[y*W:(y+1)*W]) > 20]
    if not rows: return None
    cols = [x for x in range(W) if any(raw[y*W+x] > 20 for y in range(rows[0], rows[-1] + 1, 4))]
    return cols[0], rows[0], cols[-1], rows[-1]

def check_overlaps(d):
    for t in d['tracks']:
        z = sorted(t['segments'], key=lambda s: s['target_timerange']['start'])
        for x, y in zip(z, z[1:]):
            if x['target_timerange']['start'] + x['target_timerange']['duration'] > y['target_timerange']['start'] + 1:
                die(f"segmen tumpang tindih di track {t['type']}; tidak ditulis")

def cmd_boxes(a):
    """Box PNG ke-k (…-01.png, …-02.png, …) di awal klip ke-k, plus klik mouse."""
    P = proj_path(a.project); d = load_draft(P); T = load_template()
    B = T.get('box') or die('template box tidak ada'); C = T.get('click')
    pngs = sorted(f for f in os.listdir(a.boxdir) if re.search(r'-(\d+)\.png$', f, re.I) and not f.startswith('._'))
    num = {int(re.search(r'-(\d+)\.png$', f, re.I).group(1)): f for f in pngs}
    clips = main_clips(d)
    if C and not a.no_click:
        cpath = C['material']['path']
        if not os.path.exists(cpath):
            mi = mat_index(d)
            alt = [m for _, m in mi.values() if m.get('music_id') == C['material'].get('music_id') and os.path.exists(m.get('path', ''))]
            if alt: C = dict(C, material=alt[0])
            else: die(f"file klik mouse tidak ada di cache CapCut ({cpath}). Drag '{C['material'].get('name')}' sekali dari CapCut ke proyek ini, simpan, tutup CapCut, lalu ulangi")
    cw, ch = B['material']['width'], B['material']['height']; cr = B['material']['crop']
    box_px = (cr['upper_left_x'] * cw, cr['upper_left_y'] * ch, cr['lower_right_x'] * cw, cr['lower_right_y'] * ch)
    ov = T.get('overlay', {}).get('material', {}).get('path')
    for i, c in enumerate(clips, 1):
        t0 = c['target_timerange']['start']
        f = num.get(i)
        if not f: print(f'klip {i}: TIDAK ADA PNG -{i:02d}.png, dilewati'); continue
        path = os.path.join(os.path.abspath(a.boxdir), f)
        if has_at(d, B, t0): print(f'klip {i}: box sudah ada, dilewati')
        else:
            bb = alpha_bbox(path, cw, ch)
            if bb is None: print(f'  PERINGATAN {f}: ukuran bukan {cw}x{ch} atau kosong')
            elif not (bb[0] >= box_px[0] - 1 and bb[1] >= box_px[1] - 1 and bb[2] <= box_px[2] + 1 and bb[3] <= box_px[3] + 1):
                print(f'  PERINGATAN {f}: isi PNG {bb} keluar dari area crop {tuple(round(x) for x in box_px)}; cek posisi di CapCut')
            m = copy.deepcopy(B['material']); m['path'] = path; m['material_name'] = f
            place(d, B, t0, material=m, before=os.path.basename(ov) if ov else None)
            print(f'klip {i}: {f} @ {t0/1e6:.2f}s')
        if C and not a.no_click:
            if has_at(d, C, t0): print(f'klip {i}: klik sudah ada, dilewati')
            else: place(d, C, t0, material=C['material'])
    fix_render_index(d); check_overlaps(d)
    if not a.write: print('(dry-run: tambahkan --write)'); return
    write_draft(P, d)

def cmd_overlay(a):
    """Overlay video (mis. watermark.mov) dari detik 0 file sampai akhir setiap klip."""
    P = proj_path(a.project); d = load_draft(P); O = load_template().get('overlay') or die('template overlay tidak ada')
    if a.file: O = dict(O, material=dict(O['material'], path=os.path.abspath(a.file), material_name=os.path.basename(a.file)))
    if not os.path.exists(O['material']['path']): die(f"file overlay tidak ada: {O['material']['path']}")
    clips = main_clips(d)
    for i, c in enumerate(clips, 1):
        t0, dur = c['target_timerange']['start'], c['target_timerange']['duration']
        if dur > O['material']['duration']: die(f"overlay ({O['material']['duration']/1e6:.1f} dtk) lebih pendek dari klip {i} ({dur/1e6:.1f} dtk)")
        if has_at(d, O, t0): print(f'klip {i}: overlay sudah ada, dilewati'); continue
        place(d, O, t0, dur=dur, src=0)
        print(f"klip {i}: {O['material']['material_name']} {t0/1e6:.2f}-{(t0+dur)/1e6:.2f}s")
    fix_render_index(d); check_overlaps(d)
    if not a.write: print('(dry-run: tambahkan --write)'); return
    write_draft(P, d)

def audio_streams(path):
    pr = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries',
                                    'stream=index:stream_tags=handler_name,title', '-of', 'json', path],
                                   capture_output=True, text=True).stdout)
    return [(s.get('tags') or {}).get('handler_name') or (s.get('tags') or {}).get('title') or f'a{i+1}'
            for i, s in enumerate(pr.get('streams', []))]

def stream_pcm(path, i, src, dur):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(src), '-t', str(dur), '-i', path, '-map', f'0:a:{i}',
                          '-ac', '1', '-ar', '8000', '-f', 's16le', '-'], capture_output=True).stdout
    import array
    return array.array('h', raw[:len(raw) // 2 * 2])

def cmd_mics(a):
    """Kenali track audio per mic (narasumber/host/gabungan) dan cek warna caption terhadap mic yang paling keras."""
    import math, statistics
    P = proj_path(a.project); d = load_draft(P); clips = main_clips(d); mi = mat_index(d)
    segs, mats = text_segments(d); colors = {v: k for k, v in DEFAULT_COLORS.items()}; caps = []
    for i, sg in enumerate(segs, 1):
        c = json.loads(mats[sg['material_id']]['content']); st = sg['target_timerange']['start'] / 1e6
        caps.append(dict(idx=i, start=st, end=st + sg['target_timerange']['duration'] / 1e6, text=c['text'],
                         speaker=colors.get(rgb2hex(c['styles'][0]['fill']['content']['solid']['color']), '?')))
    names = None; E = []  # (caption, [rms per stream])
    for k, c in enumerate(clips, 1):
        path = mi[c['material_id']][1]['path']; st = audio_streams(path)
        if names is None: names = st
        if len(st) != len(names): die(f'klip {k} punya {len(st)} track audio, klip 1 punya {len(names)}')
        if len(st) < 2: die(f'klip {k} hanya punya {len(st)} track audio; tidak bisa dipisah per mic')
        src, t0, dur = c['source_timerange']['start'] / 1e6, c['target_timerange']['start'] / 1e6, c['target_timerange']['duration'] / 1e6
        pcm = [stream_pcm(path, i, src, dur) for i in range(len(st))]
        for cap in caps:
            if not (t0 <= cap['start'] < t0 + dur): continue
            s0, s1 = int((cap['start'] - t0) * 8000), int((cap['end'] - t0) * 8000)
            r = []
            for x in pcm:
                seg = x[s0:s1:2] or [0]
                r.append(math.sqrt(sum(v * v for v in seg) / len(seg)))
            E.append((cap, r))
    n = len(names)
    print('track audio di sumber:', ', '.join(f'{i+1}={nm}' for i, nm in enumerate(names)))
    if n > 3 and not a.mix: die('lebih dari 3 track audio: tanyakan ke pengguna track mana untuk render, lalu ulangi dengan --mix N')
    db = lambda p, q: 20 * math.log10((p + 1) / (q + 1))
    # skor tiap track: seberapa jauh lebih keras saat caption A dibanding saat caption B
    score = []
    for i in range(n):
        A = [r[i] for c, r in E if c['speaker'] == 'A'] or [0]; B = [r[i] for c, r in E if c['speaker'] == 'B'] or [0]
        score.append(db(statistics.median(A), statistics.median(B)))
    na, ho = max(range(n), key=lambda i: score[i]), min(range(n), key=lambda i: score[i])
    rest = [i for i in range(n) if i not in (na, ho)]
    mix = a.mix - 1 if a.mix else (rest[0] if len(rest) == 1 else None)
    print(f'narasumber (A) = track {na+1} "{names[na]}" | host (B) = track {ho+1} "{names[ho]}"'
          + (f' | gabungan untuk render = track {mix+1} "{names[mix]}"' if mix is not None else ''))
    for i, nm in ((na, 'narasumber'), (ho, 'host')):
        if nm[0] != names[i][0].lower() and names[i][0].lower() in 'hn': print(f'  catatan: label "{names[i]}" di file tidak sesuai isi (terdengar {nm})')
    rows = []
    for c, r in E:
        x = db(r[na], r[ho]); mic = 'A' if x > a.db else 'B' if x < -a.db else '?'
        if mic != '?' and mic != c['speaker']: rows.append((c, mic, x))
    print(f'caption: {len(E)} | warna beda dengan mic (selisih > {a.db:g} dB): {len(rows)}')
    for c, mic, x in rows:
        print(f"  #{c['idx']} {int(c['start']//60)}:{c['start']%60:05.2f} {c['speaker']}->{mic} ({x:+.0f} dB) {c['text']}")
    if a.plan:
        dp = os.path.splitext(a.plan)[0] + '_dump.json'
        cmd_dump(argparse.Namespace(project=a.project, out=dp, text=None))
        json.dump({'dump': dp, 'captions': {str(c['idx']): [f"{mic}|{c['text']}"] for c, mic, x in rows}},
                  open(a.plan, 'w'), ensure_ascii=False, indent=1)
        print('plan warna ->', a.plan, '(periksa dulu, lalu: apply PROYEK PLAN)')

def cmd_audiotrack(a):
    """Set semua klip utama memakai track audio N dari file sumber (mis. track gabungan untuk render)."""
    P = proj_path(a.project); d = load_draft(P); clips = main_clips(d); mi = mat_index(d)
    L = d['materials'].setdefault('audio_track_indexes', [])
    for k, c in enumerate(clips, 1):
        st = audio_streams(mi[c['material_id']][1]['path'])
        if len(st) > 3 and not a.index: die(f'klip {k} punya {len(st)} track audio ({", ".join(st)}): tanyakan ke pengguna, lalu --index N')
        N = a.index or len(st)
        if N > len(st): die(f'klip {k} hanya punya {len(st)} track audio')
        cur = [mi[r][1] for r in c['extra_material_refs'] if r in mi and mi[r][0] == 'audio_track_indexes']
        if cur:
            if cur[0]['audio_track_index'] == N: print(f'klip {k}: sudah track {N} ({st[N-1]})'); continue
            cur[0]['audio_track_index'] = N
        else:
            m = dict(id=nid(), type='audio_track_index', audio_track_index=N, default_audio_track_index=1)
            L.append(m); refs = c['extra_material_refs']
            j = next((i for i, r in enumerate(refs) if mi.get(r, ('',))[0] == 'vocal_separations'), len(refs))
            refs.insert(j, m['id'])
        print(f'klip {k}: track audio {N} ({st[N-1]})')
    if not a.write: print('(dry-run: tambahkan --write)'); return
    write_draft(P, d)

def fix_render_index(d):
    for i, t in enumerate(d['tracks']):
        if t['type'] in ('video', 'audio'):
            for s in t['segments']: s['track_render_index'] = i

def cmd_split(a):
    """Potong video export utuh jadi satu file per klip, tepat di batas frame."""
    P = proj_path(a.project); d = load_draft(P); clips = main_clips(d)
    pr = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                                    'stream=codec_type,codec_name,pix_fmt,r_frame_rate,bit_rate:format=duration', '-of', 'json', a.video],
                                   capture_output=True, text=True).stdout)
    v = [s for s in pr['streams'] if s['codec_type'] == 'video'][0]
    n, dd = v['r_frame_rate'].split('/'); fps = int(n) / int(dd)
    total = float(pr['format']['duration']); tl = d['duration'] / 1e6
    if abs(total - tl) > 0.2: die(f'durasi export {total:.2f}s tidak sama dengan timeline {tl:.2f}s; export ulang seluruh timeline')
    bounds = [round(c['target_timerange']['start'] / 1e6 * fps) for c in clips] + [round(tl * fps)]
    tenbit = '10' in v.get('pix_fmt', '')
    br = max(8_000_000, int(int(v.get('bit_rate') or 10_000_000) * 1.4))
    if v['codec_name'] == 'hevc':
        venc = ['-c:v', 'hevc_videotoolbox', '-tag:v', 'hvc1'] + (['-profile:v', 'main10', '-pix_fmt', 'p010le'] if tenbit else [])
    else:
        venc = ['-c:v', 'h264_videotoolbox', '-pix_fmt', 'yuv420p']
    os.makedirs(a.outdir, exist_ok=True)
    name = a.name or os.path.basename(P)
    for i in range(len(clips)):
        f0, f1 = bounds[i], bounds[i + 1]; out = os.path.join(a.outdir, f'{name} - {i+1:02d}.mp4')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{f0/fps:.6f}', '-i', a.video, '-t', f'{(f1-f0)/fps:.6f}',
                        '-frames:v', str(f1 - f0), '-map', '0:v:0', '-map', '0:a:0?'] + venc +
                       ['-b:v', str(br), '-maxrate', str(int(br * 1.3)), '-color_primaries', 'bt709', '-color_trc', 'bt709',
                        '-colorspace', 'bt709', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', out], check=True)
        got = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries',
                              'stream=nb_read_frames', '-of', 'csv=p=0', out], capture_output=True, text=True).stdout.strip()
        ok = 'OK' if got == str(f1 - f0) else f'BEDA (harus {f1-f0})'
        print(f'{out}  {(f1-f0)/fps:.2f}s  {got} frame {ok}')

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('find'); p.add_argument('--limit', type=int, default=10); p.set_defaults(f=cmd_find)
    p = sp.add_parser('status'); p.add_argument('project'); p.set_defaults(f=cmd_status)
    p = sp.add_parser('backup'); p.add_argument('project'); p.add_argument('outdir'); p.set_defaults(f=cmd_backup)
    p = sp.add_parser('dump'); p.add_argument('project'); p.add_argument('out'); p.add_argument('--text'); p.set_defaults(f=cmd_dump)
    p = sp.add_parser('words'); p.add_argument('project'); p.add_argument('out'); p.add_argument('--model'); p.add_argument('--lang', default='id'); p.set_defaults(f=cmd_words)
    p = sp.add_parser('lines'); p.add_argument('project'); p.add_argument('video'); p.add_argument('out')
    p.add_argument('--from', dest='frm', type=float); p.add_argument('--to', type=float); p.add_argument('--frames'); p.set_defaults(f=cmd_lines)
    p = sp.add_parser('calibrate'); p.add_argument('project'); p.add_argument('lines'); p.set_defaults(f=cmd_calibrate)
    p = sp.add_parser('apply'); p.add_argument('project'); p.add_argument('plan'); p.add_argument('--words'); p.add_argument('--write', action='store_true'); p.set_defaults(f=cmd_apply)
    p = sp.add_parser('compare'); p.add_argument('old'); p.add_argument('new'); p.set_defaults(f=cmd_compare)
    p = sp.add_parser('template-save'); p.add_argument('project')
    p.add_argument('--parts', default='caption,box,click,overlay', type=lambda x: x.split(',')); p.set_defaults(f=cmd_template_save)
    p = sp.add_parser('restyle'); p.add_argument('project'); p.add_argument('--write', action='store_true'); p.set_defaults(f=cmd_restyle)
    p = sp.add_parser('boxes'); p.add_argument('project'); p.add_argument('boxdir'); p.add_argument('--no-click', action='store_true')
    p.add_argument('--write', action='store_true'); p.set_defaults(f=cmd_boxes)
    p = sp.add_parser('overlay'); p.add_argument('project'); p.add_argument('--file'); p.add_argument('--write', action='store_true'); p.set_defaults(f=cmd_overlay)
    p = sp.add_parser('mics'); p.add_argument('project'); p.add_argument('--db', type=float, default=6); p.add_argument('--mix', type=int)
    p.add_argument('--plan'); p.set_defaults(f=cmd_mics)
    p = sp.add_parser('audiotrack'); p.add_argument('project'); p.add_argument('--index', type=int); p.add_argument('--write', action='store_true'); p.set_defaults(f=cmd_audiotrack)
    p = sp.add_parser('split'); p.add_argument('project'); p.add_argument('video'); p.add_argument('outdir'); p.add_argument('--name'); p.set_defaults(f=cmd_split)
    a = ap.parse_args(); a.f(a)

if __name__ == '__main__':
    main()
