"""为缺 _icon.png 的角色渲染真实缩略图。

原理：用 headless Chromium 加载一个最小 spine-player 页面，
       渲染该角色 fb 姿态的 idle 第 0 帧，截 canvas，
       裁掉透明边，缩放到 256 宽存为 _icon.png。

为什么不用 atlas 贴图：
  atlas page 是 2048×2048 的图集，里面平铺着所有身体部位碎片，
  填充率 93~99%（满版），浏览器直接显示就是「一堆散落的零件」。
  真正的缩略图必须经 Spine 骨骼摆位后渲染。

用法：
  python render_icons.py                  # 处理所有缺图标的
  python render_icons.py --ids c016 c017  # 指定角色
  python render_icons.py --limit 20       # 只处理前 N 个
  python render_icons.py --force          # 重渲已有的
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C

BASE_URL = 'http://127.0.0.1:8899'
CHROMIUM = r'C:\Users\liuyangdang\AppData\Local\ms-playwright\chromium-1254\chrome-win64\chrome.exe'
ICON_WIDTH = 256
ICON_HEIGHT = 512

# 最小渲染页：加载 spine 4.0/4.1，播 idle 第 0 帧
RENDER_HTML = r'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin:0; padding:0; background:transparent; overflow:hidden;
               width:%(W)dpx; height:%(H)dpx; }
  #c { width:%(W)dpx; height:%(H)dpx; display:block; position:relative;
       background:transparent; }
  #c canvas { width:100%% !important; height:100%% !important; display:block; }
</style>
</head><body>
<div id="c"></div>
<script src="%(BASE)s/shared/vendor/spine-player4.0.js"></script>
<script src="%(BASE)s/shared/vendor/spine-player4.1.js"></script>
<script>
window.__done = false;
window.__err = null;
window.__ready = false;
window.__pl = null;

window.renderOne = function(cfg) {
  window.__done = false; window.__err = null; window.__ready = false;
  window.__pl = null;
  // 清掉旧 player 的 DOM，避免多实例叠加
  const host = document.getElementById('c');
  host.innerHTML = '';

  // 关键：必须等容器有真实尺寸再构造 SpinePlayer，
  // 否则 spine-player 会退化成 300×150 默认画布，角色跑到视野外 → 截图全透明。
  const waitSize = (cb, tries) => {
    tries = tries || 0;
    if (host.clientWidth > 10 && host.clientHeight > 10) { cb(); return; }
    if (tries > 120) { window.__err = 'container has no size'; window.__done = true; return; }
    requestAnimationFrame(() => waitSize(cb, tries + 1));
  };

  waitSize(() => {
    const rt = (cfg.version === '4.0') ? spine40 : spine41;
    try {
      const pl = new rt.SpinePlayer('c', {
        skelUrl: cfg.key,
        rawDataURIs: { [cfg.key]: cfg.dataUri },
        atlasUrl: cfg.atlasUrl,
        premultipliedAlpha: true,
        alpha: true,
        backgroundColor: '#00000000',
        mipmaps: false,
        preserveDrawingBuffer: true,
        showControls: false,
        defaultMix: 0,
        success: (p) => {
          try {
            const sd = p.animationState.data.skeletonData;
            const names = sd.animations.map(a => a.name);
            let pick = names.find(n => /^idle$/i.test(n))
                    || names.find(n => /idle/i.test(n))
                    || names[0];
            if (pick) p.animationState.setAnimation(0, pick, true);
            p.play();
            window.__pl = p;
            window.__ready = true;
          } catch(e) { window.__err = String(e); window.__done = true; }
        },
        error: (p, msg) => { window.__err = msg || 'load error'; window.__done = true; }
      });
    } catch(e) {
      window.__err = String(e); window.__done = true;
    }
  });
};

// 等 canvas 出现非透明像素（贴图真加载完），最多等 timeout 毫秒
window.waitForPixels = async function(timeout) {
  const pl = window.__pl;
  if (!pl || !pl.canvas) return {err: 'no canvas'};
  const cv = pl.canvas;
  const t0 = performance.now();
  while (performance.now() - t0 < timeout) {
    // 用 2D 画布抽样统计非透明像素
    try {
      const c2 = document.createElement('canvas');
      c2.width = Math.min(cv.width, 400);
      c2.height = Math.min(cv.height, 800);
      const x = c2.getContext('2d');
      x.drawImage(cv, 0, 0, c2.width, c2.height);
      const d = x.getImageData(0, 0, c2.width, c2.height).data;
      let nz = 0;
      for (let k = 3; k < d.length; k += 4) if (d[k] > 10) nz++;
      if (nz > c2.width * c2.height * 0.01) {
        return {ok: true, waited: Math.round(performance.now() - t0), nz};
      }
    } catch(e) {}
    await new Promise(r => setTimeout(r, 80));
  }
  return {err: 'no pixels after ' + timeout + 'ms'};
};

// 等 spine 布局稳定后截图
window.grab = function() {
  const pl = window.__pl;
  if (!pl || !pl.canvas) return {err: 'no canvas'};
  const cv = pl.canvas;
  const url = cv.toDataURL('image/png');
  return {url, w: cv.width, h: cv.height};
};
</script></body></html>'''


def ensure_http_server():
    """确保 8899 的静态服务在跑（渲染页要用）。"""
    import socket
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(('127.0.0.1', 8899))
        s.close()
        return True
    except Exception:
        s.close()
    return False


def collect_targets(ids=None, limit=0, force=False):
    man = os.path.join(C.BASE, 'viewer', 'nikke_manifest.json')
    m = json.load(open(man, encoding='utf-8'))
    out = []
    for c in m['characters']:
        cid = c['id']
        if ids and cid not in ids:
            continue
        icon = os.path.join(C.DATA_DIR, cid, '_icon.png')
        if not force and os.path.isfile(icon) and os.path.getsize(icon) > 0:
            continue
        info = (c.get('poses') or {}).get('fb')
        if not info:
            continue
        out.append({
            'id': cid,
            'version': info.get('version') or c.get('version') or '4.1',
            'sub': info.get('sub') or '',
            'skel': info.get('skel'),
            'atlas': info.get('atlas'),
        })
    if limit:
        out = out[:limit]
    return out


async def render_all(targets, workers=4):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROMIUM, headless=True,
            args=['--no-sandbox', '--enable-webgl', '--use-gl=swiftshader'])

        # 渲染页容器尺寸必须与视口一致：spine-player 按容器尺寸自适应，
        # 容器/视口不匹配会让角色被裁或缩放错误。
        W, H = 800, 1200
        html = RENDER_HTML % {'W': W, 'H': H, 'BASE': BASE_URL}
        # 写到 cache 供 http 访问
        html_dir = os.path.join(C.BASE, 'cache')
        os.makedirs(html_dir, exist_ok=True)
        html_path = os.path.join(html_dir, '_render_icon.html')
        open(html_path, 'w', encoding='utf-8').write(html)
        page_url = BASE_URL + '/nikke/cache/_render_icon.html'

        ok = 0
        fail = 0
        skipped = 0
        t0 = time.time()
        results = []

        # 用单页串行（spine-player 对多实例不友好）
        pg = await browser.new_page(viewport={'width': W, 'height': H})
        pg.on('pageerror', lambda e: None)
        await pg.goto(page_url, wait_until='domcontentloaded', timeout=30000)

        for i, t in enumerate(targets, 1):
            cid = t['id']
            sub = t['sub']
            rel_dir = 'nikke/data/%s%s' % (cid, ('/' + sub) if sub else '')
            skel_url = '%s/%s/%s' % (BASE_URL, rel_dir, t['skel'])
            atlas_url = '%s/%s/%s' % (BASE_URL, rel_dir, t['atlas'])

            # ── atlas 预清洗 ────────────────────────────────────────
            # spine-player 的 TextureAtlas 把 `name (value)` 当特殊语法，
            # NIKKE 有 176 个 atlas 把括号用在 attachment 名里，会崩
            # （实测 2026-11-12；注意与「NN/ 前缀 region 名」是两回事，
            #  后者 13 个且不影响渲染）。
            # 这里把 `xxx(yyy)` → `xxx_yyy`（等长），并同步改 skel 里的字符串。
            atlas_src = os.path.join(C.DATA_DIR, cid, sub, t['atlas']) \
                if sub else os.path.join(C.DATA_DIR, cid, t['atlas'])
            renames = []
            atlas_use_url = atlas_url
            try:
                raw_atlas = open(atlas_src, encoding='utf-8', errors='replace').read()
                import re as _re

                def _sub(m):
                    base, inner = m.group(1), m.group(2)
                    orig = base + '(' + inner + ')'
                    safe = base + '_' + inner
                    renames.append([orig, safe])
                    return safe

                cleaned = _re.sub(r'^(\S[^\n:]*?)\(([^)\n]+)\)\s*$',
                                  _sub, raw_atlas, flags=_re.M)
                if renames:
                    tmp_dir = os.path.join(C.BASE, 'cache', '_atlas_clean')
                    os.makedirs(tmp_dir, exist_ok=True)
                    fname = '%s%s_atlas.txt' % (cid, ('_' + sub) if sub else '')
                    open(os.path.join(tmp_dir, fname), 'w',
                         encoding='utf-8').write(cleaned)
                    atlas_use_url = '%s/nikke/cache/_atlas_clean/%s' % (
                        BASE_URL, fname)
            except Exception:
                pass

            try:
                cfg = await pg.evaluate("""async (args) => {
                  const buf = await (await fetch(args.skelUrl)).arrayBuffer();
                  if (!buf || buf.byteLength < 100) return {err: 'skel fetch failed'};
                  const blob = new Blob([buf]);
                  const dataUri = await new Promise((res, rej) => {
                    const fr = new FileReader();
                    fr.onload = () => res(fr.result);
                    fr.onerror = rej;
                    fr.readAsDataURL(blob);
                  });

                  // ⚠️ atlas 预处理：
                  // spine-player 的 TextureAtlas 解析器把 `name (value)` 当特殊语法
                  // （括号内应是 rotate/index 等修饰符）。但 NIKKE 有 176 个 atlas
                  // 把括号用在 attachment 名里（如 'add_jarket_l_1_(in)'、
                  // 'collar_r_(shadow)'），会触发
                  //   pageFields['repeat'] 里 entry[1].indexOf 的空指针
                  //   → "Unable to render skeleton: Cannot read properties of null"
                  //
                  // 做法：把改写后的 atlas 由 Python 写到临时文件（HTTP 可达），
                  //       这里只负责改 skel 里的同名字符串（等长替换）。
                  //       atlasUrl 由外部传入已清洗版本。
                  let skelBytes = new Uint8Array(buf);
                  if (args.renames && args.renames.length) {
                    for (const [orig, safe] of args.renames) {
                      if (orig.length !== safe.length) continue;
                      const pat = new TextEncoder().encode(orig);
                      const rep = new TextEncoder().encode(safe);
                      for (let i = 0; i + pat.length <= skelBytes.length; i++) {
                        let m = true;
                        for (let k = 0; k < pat.length; k++) {
                          if (skelBytes[i + k] !== pat[k]) { m = false; break; }
                        }
                        if (m) { skelBytes.set(rep, i); i += pat.length - 1; }
                      }
                    }
                  }
                  const dataUri2 = await new Promise((res, rej) => {
                    const fr = new FileReader();
                    fr.onload = () => res(fr.result);
                    fr.onerror = rej;
                    fr.readAsDataURL(new Blob([skelBytes]));
                  });

                  window.renderOne({
                    key: args.key, dataUri: dataUri2, atlasUrl: args.atlasUrl,
                    version: args.version,
                  });
                  // 等 success 或 error
                  for (let k = 0; k < 200; k++) {
                    await new Promise(r => setTimeout(r, 50));
                    if (window.__ready || window.__done) break;
                  }
                  if (window.__err) return {err: window.__err, renamed: (args.renames ? args.renames.length : 0)};
                  if (!window.__ready) return {err: 'timeout waiting player', renamed: (args.renames ? args.renames.length : 0)};
                  // 关键：等 canvas 真出现非透明像素（贴图加载完）再截图。
                  const px = await window.waitForPixels(8000);
                  if (px.err) return {err: px.err, renamed: (args.renames ? args.renames.length : 0)};
                  // 再等 2 帧让画面稳定
                  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
                  const g = window.grab();
                  g.pixels = px;
                  g.renamed = (args.renames ? args.renames.length : 0);
                  return g;
                }""", {'skelUrl': skel_url, 'atlasUrl': atlas_use_url,
                       'key': '%s_fb' % cid, 'version': t['version'],
                       'renames': renames})

                if not cfg or 'err' in cfg:
                    fail += 1
                    results.append((cid, 'FAIL', cfg.get('err') if cfg else 'null'))
                    continue

                # 保存 PNG → 裁透明边 → 缩放
                import base64
                import io
                from PIL import Image

                raw = base64.b64decode(cfg['url'].split(',', 1)[1])
                im = Image.open(io.BytesIO(raw)).convert('RGBA')
                bbox = im.getbbox()
                if not bbox:
                    skipped += 1
                    results.append((cid, 'SKIP', '全透明'))
                    continue
                im = im.crop(bbox)
                # 等比缩放到 ICON_WIDTH 宽（不超过 ICON_HEIGHT 高）
                w, h = im.size
                scale = min(ICON_WIDTH / w, ICON_HEIGHT / h)
                nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                im = im.resize((nw, nh), Image.LANCZOS)
                # 贴到透明画布上居中
                canvas = Image.new('RGBA', (ICON_WIDTH, ICON_HEIGHT), (0, 0, 0, 0))
                canvas.paste(im, ((ICON_WIDTH - nw) // 2, (ICON_HEIGHT - nh) // 2), im)
                out_dir = os.path.join(C.DATA_DIR, cid)
                os.makedirs(out_dir, exist_ok=True)
                canvas.save(os.path.join(out_dir, '_icon.png'), 'PNG')
                ok += 1
                results.append((cid, 'OK', '%dx%d → %dx%d' % (w, h, nw, nh)))

            except Exception as e:
                fail += 1
                results.append((cid, 'EXC', str(e)[:80]))

            if i % 20 == 0:
                print('  [%d/%d] OK %d, FAIL %d, SKIP %d, %.0fs' % (
                    i, len(targets), ok, fail, skipped, time.time() - t0),
                    flush=True)

        await pg.close()
        await browser.close()

    print()
    print('=' * 68)
    print('渲染完成 %.1fs' % (time.time() - t0))
    print('  OK   %d' % ok)
    print('  FAIL %d' % fail)
    print('  SKIP %d' % skipped)
    if fail:
        print()
        print('  失败明细（前 15）:')
        for cid, st, msg in results:
            if st in ('FAIL', 'EXC'):
                print('    %-20s %s %s' % (cid, st, msg))
    return ok, fail, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ids', nargs='*')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    if not ensure_http_server():
        print('需要 8899 静态服务。请先运行：')
        print(r'  cd D:\live2d-game-artifact-gallery')
        print(r'  C:\Python315\python.exe -m http.server 8899 --bind 127.0.0.1')
        return 1

    targets = collect_targets(args.ids, args.limit, args.force)
    print('待渲染: %d 个' % len(targets))
    if not targets:
        print('无需处理')
        return 0
    asyncio.run(render_all(targets))
    return 0


if __name__ == '__main__':
    sys.exit(main())