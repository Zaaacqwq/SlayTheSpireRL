"""Render an M1 A0 evaluation JSONL/JSON into a self-contained HTML dashboard.

The evaluator (tools/m1_evaluate_progress.py) appends one JSON object per
finished episode to rl/runs/<tag>.jsonl. This reads that (partial or complete)
log, computes summary stats, and writes a static HTML dashboard with the data
embedded, so it works under the Artifact CSP (no external fetch). Re-run to
refresh the snapshot.

Usage:
  python tools/render_eval_dashboard.py --tag m1_a0_1000_v2 --target 1000 \
         --out <path.html>
"""
from __future__ import annotations
import argparse, json, os, time

CHARS = ['Ironclad', 'Silent', 'Defect', 'Necrobinder', 'Regent']
STEP_CAP = 2000  # evaluator's max steps; reaching it == soft-lock, not real survival


def load(tag):
    jsonl = os.path.join('rl', 'runs', f'{tag}.jsonl')
    js = os.path.join('rl', 'runs', f'{tag}.json')
    rows, src, start_mtime, last_mtime = [], None, None, None
    if os.path.exists(jsonl):
        src = jsonl
        start_mtime = os.path.getctime(jsonl)
        last_mtime = os.path.getmtime(jsonl)
        with open(jsonl) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    elif os.path.exists(js):
        src = js
        start_mtime = last_mtime = os.path.getmtime(js)
        with open(js) as f:
            rows = json.load(f)
    return rows, src, start_mtime, last_mtime


def compute(rows, target, start_mtime, last_mtime):
    done = len(rows)
    errs = [r for r in rows if r.get('error')]
    clean = done - len(errs)
    per_char = {}
    for c in CHARS:
        cr = [r for r in rows if r['character'] == c]
        per_char[c] = {
            'n': len(cr),
            'wins': sum(1 for r in cr if r.get('outcome') is True),
            'clean': sum(1 for r in cr if not r.get('error')),
            'errors': sum(1 for r in cr if r.get('error')),
        }
    err_types = {}
    for r in errs:
        err_types[r['error']] = err_types.get(r['error'], 0) + 1
    steps = sorted(r.get('steps', 0) for r in rows)
    # histogram of steps
    hist = []
    if steps:
        lo, hi = min(steps), max(steps)
        nb = 20
        span = max(1, hi - lo)
        width = max(1, -(-span // nb))  # ceil
        buckets = {}
        for s in steps:
            b = (s - lo) // width
            buckets[b] = buckets.get(b, 0) + 1
        for b in range(0, (span // width) + 1):
            hist.append({'lo': lo + b * width, 'hi': lo + (b + 1) * width, 'count': buckets.get(b, 0)})
    slowest = sorted(rows, key=lambda r: r.get('seconds', 0), reverse=True)[:12]
    # Episodes that hit the step cap never reached game_over — with the current
    # engine that means a soft-lock (combat stops advancing), not real 2000-step
    # survival. Surface them as a distinct, non-clean outcome.
    capped_rows = [r for r in rows if r.get('steps', 0) >= STEP_CAP and not r.get('error')]
    elapsed = (last_mtime - start_mtime) if (start_mtime and last_mtime) else 0
    rate = done / elapsed if elapsed > 0 else 0
    eta = (target - done) / rate if (rate > 0 and done < target) else 0
    total_secs = sum(r.get('seconds', 0) for r in rows)
    return {
        'target': target, 'done': done, 'clean': clean, 'errors': len(errs),
        'capped': len(capped_rows),
        'capped_seeds': [r['seed'] for r in capped_rows],
        'remaining': max(0, target - done),
        'per_char': per_char, 'err_types': err_types, 'err_rows': errs,
        'steps_median': steps[len(steps) // 2] if steps else 0,
        'steps_max': steps[-1] if steps else 0,
        'hist': hist, 'slowest': slowest,
        'elapsed': elapsed, 'rate': rate, 'eta': eta,
        'avg_episode_secs': (total_secs / done) if done else 0,
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
    }


HTML = r"""<title>M1 A0 Evaluation Monitor</title>
<style>
  :root{
    --bg:#0c0d13; --panel:#14161f; --panel2:#191c27; --line:#242838;
    --ink:#cbcfdd; --ink-strong:#f2f4fb; --muted:#7a8098;
    --ember:#f0a92b; --ember-dim:#7a5a1e;
    --ok:#35b981; --ok-dim:#1c4a39; --err:#e5484d; --err-dim:#5a2225;
    --blue:#5b8bd0;
    --ring-track:#232838;
    --mono:"SFMono-Regular","Cascadia Code","JetBrains Mono",Menlo,Consolas,monospace;
    --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  }
  @media (prefers-color-scheme: light){
    :root{
      --bg:#f4f5f8; --panel:#ffffff; --panel2:#f7f8fb; --line:#e2e5ee;
      --ink:#2b2f3d; --ink-strong:#0e1019; --muted:#6a7189;
      --ember:#c17d12; --ember-dim:#e6c98f;
      --ok:#1f9e6b; --ok-dim:#c7ead9; --err:#c8383d; --err-dim:#f2d2d3;
      --blue:#3f6fb8; --ring-track:#e2e5ee;
    }
  }
  :root[data-theme="dark"]{
    --bg:#0c0d13; --panel:#14161f; --panel2:#191c27; --line:#242838;
    --ink:#cbcfdd; --ink-strong:#f2f4fb; --muted:#7a8098;
    --ember:#f0a92b; --ember-dim:#7a5a1e; --ok:#35b981; --ok-dim:#1c4a39;
    --err:#e5484d; --err-dim:#5a2225; --blue:#5b8bd0; --ring-track:#232838;
  }
  :root[data-theme="light"]{
    --bg:#f4f5f8; --panel:#ffffff; --panel2:#f7f8fb; --line:#e2e5ee;
    --ink:#2b2f3d; --ink-strong:#0e1019; --muted:#6a7189;
    --ember:#c17d12; --ember-dim:#e6c98f; --ok:#1f9e6b; --ok-dim:#c7ead9;
    --err:#c8383d; --err-dim:#f2d2d3; --blue:#3f6fb8; --ring-track:#e2e5ee;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
    line-height:1.5;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1080px;margin:0 auto;padding:32px 24px 64px}
  .eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ember);margin:0 0 6px}
  h1{font-family:var(--mono);font-size:26px;font-weight:600;color:var(--ink-strong);
    margin:0 0 4px;letter-spacing:-.01em}
  .sub{color:var(--muted);font-size:14px;margin:0 0 28px}
  .sub code{font-family:var(--mono);color:var(--ink)}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:14px}
  .hero{display:grid;grid-template-columns:auto 1fr;gap:32px;align-items:center;
    padding:28px 30px;margin-bottom:20px}
  @media (max-width:640px){.hero{grid-template-columns:1fr;gap:20px;text-align:center}}
  .ring{position:relative;width:168px;height:168px;margin:auto}
  .ring svg{transform:rotate(-90deg)}
  .ring .center{position:absolute;inset:0;display:flex;flex-direction:column;
    align-items:center;justify-content:center}
  .ring .big{font-family:var(--mono);font-size:34px;font-weight:600;
    color:var(--ink-strong);font-variant-numeric:tabular-nums;line-height:1}
  .ring .of{font-family:var(--mono);font-size:13px;color:var(--muted);margin-top:4px}
  .ring .pct{font-family:var(--mono);font-size:12px;color:var(--ember);margin-top:6px;
    letter-spacing:.05em}
  .stats{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
  @media (max-width:640px){.stats{max-width:360px;margin:0 auto}}
  .stat{background:var(--panel2);border:1px solid var(--line);border-radius:10px;
    padding:14px 16px}
  .stat .k{font-family:var(--mono);font-size:11px;letter-spacing:.12em;
    text-transform:uppercase;color:var(--muted);margin-bottom:6px}
  .stat .v{font-family:var(--mono);font-size:24px;font-weight:600;
    color:var(--ink-strong);font-variant-numeric:tabular-nums}
  .stat .v small{font-size:13px;color:var(--muted);font-weight:400}
  .stat.good .v{color:var(--ok)} .stat.bad .v{color:var(--err)}
  .grid5{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px}
  @media (max-width:820px){.grid5{grid-template-columns:repeat(2,1fr)}}
  @media (max-width:460px){.grid5{grid-template-columns:1fr}}
  .cc{padding:16px 16px 18px}
  .cc h3{font-family:var(--mono);font-size:14px;margin:0 0 12px;color:var(--ink-strong);
    display:flex;justify-content:space-between;align-items:baseline}
  .cc h3 .n{font-size:11px;color:var(--muted);font-weight:400}
  .bar{height:8px;border-radius:5px;background:var(--ring-track);overflow:hidden;
    display:flex;margin-bottom:12px}
  .bar i{display:block;height:100%}
  .cc dl{margin:0;display:grid;grid-template-columns:1fr auto;gap:5px 8px;
    font-family:var(--mono);font-size:12px}
  .cc dt{color:var(--muted)} .cc dd{margin:0;color:var(--ink-strong);
    font-variant-numeric:tabular-nums;text-align:right}
  .cc dd.e{color:var(--err)} .cc dd.z{color:var(--muted)}
  .cols{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  @media (max-width:820px){.cols{grid-template-columns:1fr}}
  .sec{padding:20px 22px}
  .sec h2{font-family:var(--mono);font-size:13px;letter-spacing:.1em;
    text-transform:uppercase;color:var(--muted);margin:0 0 16px;font-weight:600}
  .hist{display:flex;align-items:flex-end;gap:3px;height:130px}
  .hist .col{flex:1;background:linear-gradient(var(--ember),var(--ember-dim));
    border-radius:3px 3px 0 0;min-height:2px;position:relative}
  .hist .col:hover::after{content:attr(data-t);position:absolute;bottom:100%;left:50%;
    transform:translateX(-50%);background:var(--ink-strong);color:var(--bg);
    font-family:var(--mono);font-size:11px;padding:3px 6px;border-radius:4px;
    white-space:nowrap;margin-bottom:4px}
  .hist-x{display:flex;justify-content:space-between;font-family:var(--mono);
    font-size:11px;color:var(--muted);margin-top:8px}
  table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px}
  th{text-align:left;color:var(--muted);font-weight:500;padding:6px 8px;
    border-bottom:1px solid var(--line);letter-spacing:.04em}
  td{padding:6px 8px;border-bottom:1px solid var(--line);
    font-variant-numeric:tabular-nums;color:var(--ink)}
  td.r,th.r{text-align:right} tr:last-child td{border-bottom:none}
  .pill{display:inline-block;font-family:var(--mono);font-size:11px;padding:1px 7px;
    border-radius:20px;border:1px solid}
  .pill.err{color:var(--err);border-color:var(--err);background:var(--err-dim)}
  .pill.ok{color:var(--ok);border-color:var(--ok);background:var(--ok-dim)}
  .empty{font-family:var(--mono);font-size:13px;color:var(--ok);padding:8px 0}
  .foot{margin-top:28px;font-family:var(--mono);font-size:12px;color:var(--muted);
    display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
  .live{display:inline-flex;align-items:center;gap:6px;color:var(--ember)}
  .warn{background:var(--panel);border:1px solid var(--ember);border-left:3px solid var(--ember);
    border-radius:12px;padding:14px 18px;margin-bottom:20px;font-size:13.5px;color:var(--ink)}
  .warn b{font-family:var(--mono);color:var(--ember);font-weight:600}
  .warn code{font-family:var(--mono);color:var(--ink-strong);font-size:12px}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--ember)}
  @media (prefers-reduced-motion:no-preference){
    .dot{animation:pulse 1.6s ease-in-out infinite}
    @keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
  }
</style>

<div class="wrap">
  <p class="eyebrow">STS2 · RL v2 · M1 acceptance</p>
  <h1>A0 Random-Agent Evaluation</h1>
  <p class="sub">1,000 episodes on the real engine (<code>sts2-cli 5eef59f</code>) · zero-illegal-action / zero-timeout gate · <span id="livewrap"></span></p>

  <div class="panel hero">
    <div class="ring">
      <svg width="168" height="168" viewBox="0 0 168 168">
        <circle cx="84" cy="84" r="74" fill="none" stroke="var(--ring-track)" stroke-width="14"/>
        <circle id="ringfill" cx="84" cy="84" r="74" fill="none" stroke="var(--ember)"
          stroke-width="14" stroke-linecap="round" stroke-dasharray="465" stroke-dashoffset="465"/>
      </svg>
      <div class="center">
        <div class="big" id="h-done">0</div>
        <div class="of" id="h-target">/ 1000</div>
        <div class="pct" id="h-pct">0%</div>
      </div>
    </div>
    <div class="stats">
      <div class="stat good"><div class="k">Clean episodes</div><div class="v" id="h-clean">0</div></div>
      <div class="stat" id="stat-err"><div class="k">Errors</div><div class="v" id="h-err">0</div></div>
      <div class="stat"><div class="k">Throughput</div><div class="v" id="h-rate">0 <small>ep/s</small></div></div>
      <div class="stat"><div class="k" id="h-eta-k">ETA</div><div class="v" id="h-eta">—</div></div>
    </div>
  </div>

  <div id="warnbox"></div>

  <div class="grid5" id="chars"></div>

  <div class="cols">
    <div class="panel sec">
      <h2>Errors by type</h2>
      <div id="errbox"></div>
    </div>
    <div class="panel sec">
      <h2>Steps per episode</h2>
      <div class="hist" id="hist"></div>
      <div class="hist-x"><span id="hx-lo">0</span><span id="hx-md"></span><span id="hx-hi">0</span></div>
    </div>
  </div>

  <div class="panel sec" style="margin-top:20px">
    <h2>Slowest episodes</h2>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Seed</th><th class="r">Steps</th><th class="r">Seconds</th><th>Result</th></tr></thead>
        <tbody id="slow"></tbody>
      </table>
    </div>
  </div>

  <div class="foot">
    <span>Snapshot generated <span id="gen"></span></span>
    <span id="mediansteps"></span>
  </div>
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
  const D = JSON.parse(document.getElementById('data').textContent);
  const CHAR_COLOR = {Ironclad:'#d0554b',Silent:'#7bb662',Defect:'#5b8bd0',Necrobinder:'#9b7bd0',Regent:'#e0b34a'};
  const q = id => document.getElementById(id);

  // hero
  q('h-done').textContent = D.done.toLocaleString();
  q('h-target').textContent = '/ ' + D.target.toLocaleString();
  const pct = D.target ? D.done / D.target : 0;
  q('h-pct').textContent = (pct*100).toFixed(1) + '%';
  q('ringfill').style.strokeDashoffset = (465 * (1 - pct)).toFixed(1);
  q('h-clean').textContent = D.clean.toLocaleString();
  q('h-err').textContent = D.errors.toLocaleString();
  if (D.errors > 0) q('stat-err').classList.add('bad'); else q('stat-err').classList.add('good');
  q('h-rate').innerHTML = D.rate.toFixed(2) + ' <small>ep/s</small>';
  const running = D.done < D.target;
  if (running) {
    q('h-eta').textContent = fmtDur(D.eta);
    q('livewrap').innerHTML = '<span class="live"><span class="dot"></span>running</span>';
  } else {
    q('h-eta-k').textContent = 'Total time';
    q('h-eta').textContent = fmtDur(D.elapsed);
    const parts = ['complete', D.errors + ' errors'];
    if (D.capped) parts.push(D.capped + ' soft-locked');
    const cls = (D.errors === 0 && !D.capped) ? 'ok' : 'err';
    q('livewrap').innerHTML = '<span class="pill ' + cls + '">' + parts.join(' · ') + '</span>';
  }

  // soft-lock warning banner
  if (D.capped) {
    q('warnbox').innerHTML =
      '<div class="warn"><b>' + D.capped + ' soft-locked episode' + (D.capped>1?'s':'') +
      '</b> hit the ' + '2000' + '-step cap without reaching game_over. These are counted ' +
      'as "clean" by the zero-error gate but are <b>not real games</b> — combat stops ' +
      'advancing after a nested card-selection leaves the engine’s action executor stuck. ' +
      'Seeds: <code>' + D.capped_seeds.join('</code> <code>') + '</code>. ' +
      'Root fix pending before M1 can be declared passed.</div>';
  }

  // per-character
  const cw = q('chars');
  for (const c of ['Ironclad','Silent','Defect','Necrobinder','Regent']) {
    const p = D.per_char[c]; const col = CHAR_COLOR[c];
    const cleanW = p.n ? (p.clean/p.n*100) : 0;
    const errW = p.n ? (p.errors/p.n*100) : 0;
    const el = document.createElement('div');
    el.className = 'panel cc';
    el.innerHTML =
      '<h3>'+c+'<span class="n">'+p.n+'</span></h3>'+
      '<div class="bar"><i style="width:'+cleanW+'%;background:'+col+'"></i>'+
        '<i style="width:'+errW+'%;background:var(--err)"></i></div>'+
      '<dl><dt>clean</dt><dd>'+p.clean+'</dd>'+
      '<dt>wins</dt><dd class="'+(p.wins?'':'z')+'">'+p.wins+'</dd>'+
      '<dt>errors</dt><dd class="'+(p.errors?'e':'z')+'">'+p.errors+'</dd></dl>';
    cw.appendChild(el);
  }

  // errors by type
  const eb = q('errbox');
  const types = Object.entries(D.err_types).sort((a,b)=>b[1]-a[1]);
  if (!types.length) {
    eb.innerHTML = '<div class="empty">✓ No errors — zero EngineTimeout / ProtocolError so far.</div>';
  } else {
    const maxc = Math.max(...types.map(t=>t[1]));
    eb.innerHTML = types.map(([k,v])=>
      '<div style="margin-bottom:12px">'+
      '<div style="display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px;margin-bottom:4px">'+
      '<span class="pill err">'+k+'</span><span style="color:var(--err)">'+v+'</span></div>'+
      '<div class="bar"><i style="width:'+(v/maxc*100)+'%;background:var(--err)"></i></div></div>'
    ).join('') +
    '<div style="font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:4px">'+
    D.err_rows.map(r=>r.seed).join(' · ')+'</div>';
  }

  // histogram
  const hb = q('hist');
  if (D.hist.length) {
    const maxh = Math.max(...D.hist.map(h=>h.count), 1);
    hb.innerHTML = D.hist.map(h=>
      '<div class="col" style="height:'+(h.count/maxh*100)+'%" data-t="'+h.lo+'–'+h.hi+' steps · '+h.count+'"></div>'
    ).join('');
    q('hx-lo').textContent = D.hist[0].lo + ' steps';
    q('hx-hi').textContent = D.hist[D.hist.length-1].hi + ' steps';
    q('hx-md').textContent = 'median ' + D.steps_median;
  }

  // slowest table
  q('slow').innerHTML = D.slowest.map(r=>{
    const res = r.error
      ? '<span class="pill err">'+r.error+'</span>'
      : (r.outcome===true ? '<span class="pill ok">victory</span>'
         : '<span style="color:var(--muted)">game_over</span>');
    return '<tr><td>'+r.seed+'</td><td class="r">'+r.steps+'</td><td class="r">'+r.seconds.toFixed(1)+
      '</td><td>'+res+'</td></tr>';
  }).join('');

  q('gen').textContent = D.generated;
  q('mediansteps').textContent = 'median '+D.steps_median+' steps · max '+D.steps_max+' · avg '+D.avg_episode_secs.toFixed(1)+'s/episode';

  function fmtDur(s){
    s = Math.max(0, Math.round(s)); const h=Math.floor(s/3600), m=Math.floor(s%3600/60), sec=s%60;
    return h? h+':'+String(m).padStart(2,'0')+':'+String(sec).padStart(2,'0') : m+':'+String(sec).padStart(2,'0');
  }
</script>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', default='m1_a0_1000_v2')
    ap.add_argument('--target', type=int, default=1000)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    rows, src, start_mtime, last_mtime = load(args.tag)
    if not rows:
        raise SystemExit(f'no data found for tag {args.tag} (looked for rl/runs/{args.tag}.jsonl/.json)')
    stats = compute(rows, args.target, start_mtime, last_mtime)
    html = HTML.replace('__DATA__', json.dumps(stats))
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'wrote {args.out} from {src}: {stats["done"]}/{stats["target"]} done, '
          f'{stats["clean"]} clean, {stats["errors"]} errors')


if __name__ == '__main__':
    main()
