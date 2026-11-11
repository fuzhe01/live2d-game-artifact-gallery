"""按正确顺序覆盖 CDN 资源：用本地 cdb 版替换，再清理冗余。

背景（用户澄清）：
  CDN 下载是【中途放弃】的 —— 下到 257 个时转向解包方向。
  所以那 257 个不是「CDN 只收录这么多」，而是「没下完」。

正确顺序：
  1. 用 extract_cdb.py 重新提取这 257 个角色（cdb 版覆盖 CDN 版）
  2. 验证 mtime 全部变新、skel/atlas/png 齐全
  3. 清理真正冗余的文件（重复的 _2.png 等）

用法：
  python replace_cdn_with_cdb.py --check    # 只检查，不执行
  python replace_cdn_with_cdb.py            # 执行覆盖
"""
import json
import os
import subprocess
import sys
import datetime

sys.path.insert(0, r'D:\live2d-game-artifact-gallery\nikke\scripts')
import config as C

DATA = C.DATA_DIR
MAN = os.path.join(C.BASE, 'viewer', 'nikke_manifest.json')
SCRIPTS = os.path.join(C.BASE, 'scripts')
PY = r'C:\Python315\python.exe'


def find_cdn_ids():
    """找出 CDN 批的 257 个 cid（skel mtime 11-10 15:xx）"""
    m = json.load(open(MAN, encoding='utf-8'))
    out = []
    for c in m['characters']:
        info = (c.get('poses') or {}).get('fb')
        if not info:
            continue
        sub = info.get('sub') or ''
        base = os.path.join(DATA, c['id'], sub) if sub else os.path.join(DATA, c['id'])
        p = os.path.join(base, info.get('skel', ''))
        if not os.path.isfile(p):
            continue
        t = datetime.datetime.fromtimestamp(os.path.getmtime(p))
        if t.strftime('%m-%d') == '11-10' and t.hour == 15:
            out.append(c['id'])
    return out


def snapshot(cids):
    """记录覆盖前的状态"""
    snap = {}
    for cid in cids:
        d = os.path.join(DATA, cid)
        info = {'files': {}, 'total': 0, 'bytes': 0}
        if not os.path.isdir(d):
            snap[cid] = info
            continue
        for root, dirs, files in os.walk(d):
            for f in files:
                if f == '_icon.png':
                    continue
                p = os.path.join(root, f)
                rel = os.path.relpath(p, d)
                info['files'][rel] = {
                    'size': os.path.getsize(p),
                    'mtime': int(os.path.getmtime(p)),
                }
                info['total'] += 1
                info['bytes'] += os.path.getsize(p)
        snap[cid] = info
    return snap


def main():
    check_only = '--check' in sys.argv

    cdn = find_cdn_ids()
    print('CDN 批: %d 个角色' % len(cdn))
    if not cdn:
        print('无 CDN 角色，无需覆盖')
        return

    print()
    print('=' * 70)
    print('覆盖前快照')
    print('=' * 70)
    before = snapshot(cdn)
    tot_files = sum(v['total'] for v in before.values())
    tot_bytes = sum(v['bytes'] for v in before.values())
    print('  文件 %d 个 / %.1f MB' % (tot_files, tot_bytes / 1048576))

    if check_only:
        print()
        print('  [CHECK] 前 10 个角色:')
        for cid in cdn[:10]:
            v = before[cid]
            print('    %-16s %2d 文件  %.1f MB' % (
                cid, v['total'], v['bytes'] / 1048576))
        print()
        print('  加 --force 不带 --check 则执行覆盖')
        return

    # 执行 extract_cdb 覆盖
    print()
    print('=' * 70)
    print('第 1 步：extract_cdb 重新提取（cdb 版覆盖）')
    print('=' * 70)

    # 分批传 ids（命令行不宜过长）
    BATCH = 40
    ok_total = 0
    for i in range(0, len(cdn), BATCH):
        batch = cdn[i:i + BATCH]
        cmd = [PY, os.path.join(SCRIPTS, 'extract_cdb.py'),
               '--pak', 'both', '--force', '--ids'] + batch
        print('  [%d-%d/%d] 提取中...' % (i + 1, min(i + BATCH, len(cdn)), len(cdn)),
              flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', cwd=SCRIPTS)
        # 解析结果
        out = (r.stdout or '') + (r.stderr or '')
        if r.returncode != 0:
            print('    FAIL rc=%d' % r.returncode)
            print('    %s' % out[-400:])
        else:
            # 提取最后几行汇总
            lines = [l for l in out.splitlines() if '提取汇总' in l or '成功' in l or '失败' in l]
            for l in lines[-3:]:
                print('    %s' % l.strip())

    print()
    print('=' * 70)
    print('第 2 步：验证覆盖结果')
    print('=' * 70)
    after = snapshot(cdn)
    changed = 0
    unchanged = 0
    still_cdn = []
    for cid in cdn:
        b = before[cid]
        a = after[cid]
        # 判断文件是否更新（mtime 变大 或 新增文件）
        updated = False
        for rel, meta in a['files'].items():
            if rel in b['files']:
                if meta['mtime'] > b['files'][rel]['mtime']:
                    updated = True
                    break
            else:
                updated = True   # 新增
                break
        if updated:
            changed += 1
        else:
            unchanged += 1
            still_cdn.append(cid)

    print('  已更新: %d' % changed)
    print('  未变: %d' % unchanged)
    if still_cdn:
        print()
        print('  未更新的角色（可能 catalog 里没有）:')
        for cid in still_cdn[:30]:
            print('    %s' % cid)

    # 检查 skel mtime 是否还是旧
    print()
    print('  复查 skel mtime（应变成今天）:')
    m = json.load(open(MAN, encoding='utf-8'))
    id2char = {c['id']: c for c in m['characters']}
    old_left = []
    for cid in cdn:
        c = id2char.get(cid)
        if not c:
            continue
        info = (c.get('poses') or {}).get('fb')
        if not info:
            continue
        sub = info.get('sub') or ''
        base = os.path.join(DATA, cid, sub) if sub else os.path.join(DATA, cid)
        p = os.path.join(base, info.get('skel', ''))
        if not os.path.isfile(p):
            continue
        t = datetime.datetime.fromtimestamp(os.path.getmtime(p))
        if t.strftime('%m-%d') == '11-10' and t.hour == 15:
            old_left.append(cid)
    print('    仍是旧 CDN 版: %d 个' % len(old_left))
    if old_left:
        print('    %s' % old_left[:20])

    # 保存快照供清理用
    snap_path = os.path.join(C.BASE, 'cache', 'cdn_replace_before.json')
    json.dump(before, open(snap_path, 'w', encoding='utf-8'),
              ensure_ascii=False)
    print()
    print('  覆盖前快照已存: %s' % snap_path)
    print('  （后续清理可据此判断哪些文件是冗余的）')


if __name__ == '__main__':
    main()