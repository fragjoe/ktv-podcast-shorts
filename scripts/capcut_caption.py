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
    for t in d['tracks']:
        if t['type'] != 'video': continue
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
    a = ap.parse_args(); a.f(a)

if __name__ == '__main__':
    main()
