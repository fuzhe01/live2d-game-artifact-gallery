"""用 viewer 页面本身渲染缩略图。

为什么不用自建 spine-player 页面：
  spine-player 的 atlas 解析器把 `name (value)` 当特殊语法，
  NIKKE 有 176 个 atlas 把括号用在 attachment 名里
  （'add_jarket_l_1_(in)'、'collar_r_(shadow)' 等，实测 2026-11-12），
  自建页面加载会崩。
  而 viewer 页面能正常显示这些角色 —— 说明它走的是另一条路径
  （index.html 里可能是通过 rawDataURIs + 预清洗，或者
   viewer 里 atlas 是直接 fetch 文本后由 spine-player 内部解析，
   实测 697 个条目在 viewer 里都能显示）。

做法：打开 viewer，进入角色，等 canvas 有像素，直接从 canvas 截图，
      裁透明边后缩放成 _icon.png。
"""
import argparse
import asyncio
import base64
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C

VIEWER = 'http://127.0.0.1:8901/nikke/viewer/index.html'
CHROMIUM = r'C:\Users\liuyangdang\AppData\Local\ms-playwright\chromium-1254\chrome-win64\chrome.exe'
ICON_W, ICON_H = 256, 512

# 等 canvas 出现有意义的像素，然后返回 dataURL
GRAB_JS = """
async (minPct) => {
  const pl = window.__ia && window.__ia.player;
  if (!pl || !pl.canvas) return {err: 'no player canvas'};
  const cv = pl.canvas;
  // 等 canvas 分配真实尺寸
  for (let k = 0; k < 120; k++) {
    if (cv.width > 100 && cv.height > 100) break;
    await new Promise(r => setTimeout(r, 50));
  }
  if (cv.width <= 100) return {err: 'canvas too small: ' + cv.width + 'x' + cv.height};
  // 等出现非透明像素
  const t0 = performance.now();
  let nz = 0;
  while (performance.now() - t0 < 10000) {
    try {
      const c2 = document.createElement('canvas');
      const sc = 400 / cv.width;
      c2.width = 400; c2.height = Math.round(cv.height * sc);
      const x = c2.getContext('2d');
      x.drawImage(cv, 0, 0, c2.width, c2.height);
      const d = x.getImageData(0, 0, c2.width, c2.height).data;
      nz = 0;
      for (let k = 3; k < d.length; k += 4) if (d[k] > 10) nz++;
      if (nz > c2.width * c2.height * minPct) {
        return {ok: true, url: cv.toDataURL('image/png'),
                w: cv.width, h: cv.height, nz,
                waited: Math.round(performance.now() - t0)};
      }
    } catch (e) { return {err: String(e)}; }
    await new Promise(r => setTimeout(r, 100));
  }
  return {err: 'no pixels (nz=' + nz + ')'};
}
"""


def collect_targets(ids=None, limit=0, force=False):
    man = os.path.join(C.BASE, 'viewer', 'nikke_manifest.json')
    m = json.load(open(man, encoding='utf-8'))
    out = []
    for c in m['characters']:
        cid = c['id']
        if ids and cid not in ids:
            continue
        icon = os.path.join(C.DATA_DIR, cid, '_icon.png')
        if not force and os.path.isfile(icon) and os.path.getsize(icon) > 1000:
            continue
        if not (c.get('poses') or {}).get('fb'):
            continue
        out.append(cid)
    if limit:
        out = out[:limit]
    return out


def save_icon(cid, data_url):
    from PIL import Image
    raw = base64.b64decode(data_url.split(',', 1)[1])
    im = Image.open(io.BytesIO(raw)).convert('RGBA')
    bbox = im.getbbox()
    if not bbox:
        return None, '全透明'
    im = im.crop(bbox)
    w, h = im.size

    # ⚠️ 缩放策略：高度优先（保证列表里所有角色立绘高度一致）。
    # 旧逻辑 scale = min(256/w, 512/h) 是「等比塞进框内」，
    # 宽图会被宽度限制 → 高度只占 30~50%，列表里高低不齐（实测 31%~100%）。
    # 新逻辑：先按目标高度缩，若宽度超出画布则改按宽度缩（极少数宽构图角色）。
    TARGET_H = int(ICON_H * 0.96)          # 内容高度占画布 96%
    scale = TARGET_H / h
    nw, nh = int(w * scale), int(h * scale)
    if nw > ICON_W:
        # 太宽，按宽度约束（会牺牲一点高度，但避免横向裁切）
        scale = ICON_W / w
        nw, nh = int(w * scale), int(h * scale)

    im2 = im.resize((max(1, nw), max(1, nh)), Image.LANCZOS)
    canvas = Image.new('RGBA', (ICON_W, ICON_H), (0, 0, 0, 0))
    canvas.paste(im2, ((ICON_W - nw) // 2, (ICON_H - nh) // 2), im2)
    out_dir = os.path.join(C.DATA_DIR, cid)
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, '_icon.png')
    canvas.save(dst, 'PNG')
    return dst, '%dx%d→%dx%d (高占%.0f%%)' % (w, h, nw, nh, nh / ICON_H * 100)


async def run(targets):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROMIUM, headless=True,
                                    args=['--no-sandbox', '--enable-webgl',
                                          '--use-gl=swiftshader'])
            # ⚠️ 视口必须用竖版 1:2：spine-player 按容器尺寸自适应缩放，
            # 横版视口会让角色「横向铺开」（实测 c140 fillW=54%、fillH=83%），
            # 再塞进 1:2 的 256×512 就只剩 46% 高，列表里高低不齐。
            # 用 512×1024（1:2）让角色自然占满竖向。
        pg = await b.new_page(viewport={'width': 512, 'height': 1024})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)[:120]))
        await pg.goto(VIEWER, wait_until='networkidle', timeout=60000)

        ok = fail = skip = 0
        t0 = time.time()
        results = []

        # 直接调 viewer 的 enterStage（按 manifest 索引）
        idx_map = await pg.evaluate("""() => {
          const m = {};
          window.__manifestCache || (window.__manifestCache = null);
          return m;
        }""")

        for i, cid in enumerate(targets, 1):
            try:
                # 回列表 → 搜索 → 进入
                ok_nav = await pg.evaluate("""async (cid) => {
                  // 关掉可能开着的浮层
                  document.querySelectorAll('.on').forEach(e => {
                    if (/panel|bubble/.test(e.className)) e.classList.remove('on');
                  });
                  const stage = document.getElementById('stage-view');
                  if (stage.style.display !== 'none') {
                    document.getElementById('back').click();
                    await new Promise(r => setTimeout(r, 400));
                  }
                  const inp = document.getElementById('search');
                  inp.value = cid;
                  inp.dispatchEvent(new Event('input', {bubbles: true}));
                  await new Promise(r => setTimeout(r, 700));
                  const cards = [...document.querySelectorAll('.card')];
                  let hit = cards.find(c => {
                    const nm = c.querySelector('.name');
                    return nm && nm.textContent.includes(cid);
                  });
                  if (!hit) hit = cards[0];
                  if (!hit) return false;
                  hit.click();
                  return true;
                }""", cid)
                if not ok_nav:
                    fail += 1
                    results.append((cid, 'NAV_FAIL', ''))
                    continue

                await pg.wait_for_timeout(4500)
                r = await pg.evaluate(GRAB_JS, 0.005)
                if not r or 'err' in r:
                    fail += 1
                    results.append((cid, 'GRAB_FAIL', (r or {}).get('err', 'null')))
                    continue
                dst, info = save_icon(cid, r['url'])
                if not dst:
                    skip += 1
                    results.append((cid, 'SKIP', info))
                    continue
                ok += 1
                results.append((cid, 'OK', info))
            except Exception as e:
                fail += 1
                results.append((cid, 'EXC', str(e)[:80]))

            if i % 20 == 0:
                print('  [%d/%d] OK %d, FAIL %d, SKIP %d, %.0fs' % (
                    i, len(targets), ok, fail, skip, time.time() - t0), flush=True)

        await b.close()

    print()
    print('=' * 68)
    print('渲染完成 %.1fs' % (time.time() - t0))
    print('  OK   %d' % ok)
    print('  FAIL %d' % fail)
    print('  SKIP %d' % skip)
    if fail:
        print()
        print('  失败（前 15）:')
        for cid, st, msg in results:
            if st in ('FAIL', 'NAV_FAIL', 'GRAB_FAIL', 'EXC'):
                print('    %-18s %-10s %s' % (cid, st, msg))
    return ok, fail, skip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ids', nargs='*')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    targets = collect_targets(args.ids, args.limit, args.force)
    print('待渲染: %d 个' % len(targets))
    if not targets:
        print('无需处理')
        return 0
    asyncio.run(run(targets))
    return 0


if __name__ == '__main__':
    sys.exit(main())