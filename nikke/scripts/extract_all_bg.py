"""批量提取游戏内 eventscene 剧情插画（scenariobackground hd）→ WebP。

用法：
  python extract_all_bg.py            # 全量
  python extract_all_bg.py 50         # 只跑前 50 个（抽样验证）

输出：D:\\nikke-toolchain\\_work\\gallery_webp\\
清单：D:\\nikke-toolchain\\_work\\gallery_webp\\manifest.json
"""
import json
import os
import sqlite3
import sys
import time

PATCH = r'D:\nikke\Unity\com_proximabeta_NIKKE\com.shiftup.patch'
WORK = r'D:\nikke-toolchain\_work'
OUT = os.path.join(WORK, 'gallery_webp')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_scenario_bg import CidxIndex, Cdb, open_catalog  # noqa: E402


def collect_targets():
    """列出所有 scenariobackground(hd) 的 eventscene 资源。"""
    cons = {}
    targets = {}
    for pak in ('dp', 'core'):
        cons[pak] = open_catalog(pak)
        rows = cons[pak].execute(
            "SELECT file_id, key FROM files_chunktype "
            "WHERE key LIKE 'scenariobackground(hd)_assets_eventscene_%'").fetchall()
        for fid, key in rows:
            import re
            m = re.match(r'^scenariobackground\(hd\)_assets_(.+?)_[0-9a-f]{32}\.bundle$',
                         key, re.I)
            if m:
                targets.setdefault(m.group(1), (pak, fid, key))
    return targets, cons


def chunk_rows(cons, fid):
    for pak in ('dp', 'core'):
        rs = cons[pak].execute(
            "SELECT cm.file_offset, c.hash, c.compressed_size "
            "FROM chunk_file_map cm JOIN chunks c ON c.chunk_id=cm.chunk_id "
            "WHERE cm.file_id=? ORDER BY cm.file_offset", (fid,)).fetchall()
        if rs:
            return rs
    return []


def reassemble(rows, cidx, cdbs):
    buf = bytearray()
    for foff, h, comp in rows:
        ent, use = None, None
        for pak in ('dp', 'core'):
            ent = cidx[pak].locate(h)
            if ent:
                use = pak
                break
        if not ent:
            return None, 'chunk-missing'
        real, csz = ent
        try:
            data = cdbs[use].read_chunk(real, csz)
        except Exception as e:
            return None, 'chunk-read:%s' % str(e)[:30]
        if foff != len(buf):
            return None, 'gap'
        buf += data
    return bytes(buf), None


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    os.makedirs(OUT, exist_ok=True)

    import UnityPy
    from PIL import Image

    targets, cons = collect_targets()
    print('目标 eventscene(hd) 资源: %d 个' % len(targets))

    cidx = {p: CidxIndex(p) for p in ('dp', 'core')}
    cdbs = {p: Cdb(p) for p in ('dp', 'core')}

    items = sorted(targets.items())
    if limit:
        items = items[:limit]
    print('本次处理: %d' % len(items))

    manifest = []
    ok = 0
    fail = {}
    t0 = time.time()

    for n, (name, (pak, fid, key)) in enumerate(items):
        rows = chunk_rows(cons, fid)
        if not rows:
            fail['no-chunks'] = fail.get('no-chunks', 0) + 1
            continue
        data, err = reassemble(rows, cidx, cdbs)
        if not data:
            fail[err] = fail.get(err, 0) + 1
            continue
        try:
            env = UnityPy.load(data)
            tex = None
            for o in env.objects:
                if o.type.name == 'Texture2D':
                    tex = o.read()
                    break
            if not tex:
                fail['no-texture'] = fail.get('no-texture', 0) + 1
                continue
            img = tex.image
            out = os.path.join(OUT, name + '.webp')
            img.save(out, 'WEBP', quality=88, method=4)
            manifest.append({
                'name': name,
                'file': name + '.webp',
                'w': tex.m_Width,
                'h': tex.m_Height,
                'bytes': os.path.getsize(out),
            })
            ok += 1
        except Exception as e:
            fail['exc:' + str(e)[:24]] = fail.get('exc:' + str(e)[:24], 0) + 1

        if (n + 1) % 50 == 0:
            el = time.time() - t0
            print('  %d/%d 成功 %d 失败 %s  (%.1fs, %.2f/s)' % (
                n + 1, len(items), ok, dict(list(fail.items())[:3]),
                el, (n + 1) / el if el else 0))

    json.dump({'count': len(manifest), 'items': manifest},
              open(os.path.join(OUT, 'manifest.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    print()
    print('完成: 成功 %d / %d，用时 %.1fs' % (ok, len(items), time.time() - t0))
    if fail:
        print('失败明细: %s' % fail)
    total = sum(x['bytes'] for x in manifest)
    print('总大小: %.1f MB' % (total / 1024**2))
    for p in ('dp', 'core'):
        cdbs[p].close()
        cons[p].close()


if __name__ == '__main__':
    main()