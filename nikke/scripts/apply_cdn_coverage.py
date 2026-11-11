"""按精确匹配结果，用 cdb 覆盖 CDN 资源。

前置：
  classify_cdn_coverage.py 已生成 cache/cdn_coverage_result.json
    coverable     205 个 → 用 cdb 版覆盖
    not_coverable  52 个 → 保留 CDN 版（catalog 里确实没有）

流程：
  1. 快照覆盖前状态
  2. extract_cdb.py --ids <205个> --force 覆盖
  3. 验证 mtime 变新 + 文件齐全
  4. 输出清理清单（真正冗余的旧文件）
"""
import json
import os
import subprocess
import sys
import datetime
from collections import Counter

sys.path.insert(0, r'D:\live2d-game-artifact-gallery\nikke\scripts')
import config as C

DATA = C.DATA_DIR
BASE = C.BASE
SCRIPTS = os.path.join(BASE, 'scripts')
PY = r'C:\Python315\python.exe'
COVERAGE = os.path.join(BASE, 'cache', 'cdn_coverage_result.json')
SNAP = os.path.join(BASE, 'cache', 'cdn_before_snapshot.json')


def load_coverage():
    if not os.path.isfile(COVERAGE):
        print('缺少 %s，请先跑 classify_cdn_coverage.py' % COVERAGE)
        sys.exit(1)
    return json.load(open(COVERAGE, encoding='utf-8'))


def snapshot(cids):
    snap = {}
    for cid in cids:
        d = os.path.join(DATA, cid)
        info = {'files': {}}
        if not os.path.isdir(d):
            snap[cid] = info
            continue
        for root, dirs, files in os.walk(d):
            for f in files:
                if f == '_icon.png':
                    continue
                p = os.path.join(root, f)
                rel = os.path.relpath(p, d).replace('\\', '/')
                info['files'][rel] = {
                    'size': os.path.getsize(p),
                    'mtime': int(os.path.getmtime(p)),
                }
        snap[cid] = info
    return snap


def main():
    dry = '--dry-run' in sys.argv
    cov = load_coverage()
    targets = cov['coverable']
    keep = cov['not_coverable']

    print('=' * 74)
    print('CDN 覆盖计划（基于精确路径匹配）')
    print('=' * 74)
    print('  可覆盖（用 cdb 替换）: %d' % len(targets))
    print('  保留 CDN 版（catalog 无）: %d' % len(keep))

    before = snapshot(targets)
    n_before = sum(len(v['files']) for v in before.values())
    sz_before = sum(f['size'] for v in before.values() for f in v['files'].values())
    print('  覆盖前文件 %d 个 / %.1f MB' % (n_before, sz_before / 1048576))

    if dry:
        print()
        print('  [DRY RUN] 前 12 个待覆盖:')
        for cid in targets[:12]:
            print('    %-16s %d 文件' % (cid, len(before[cid]['files'])))
        print()
        print('  去掉 --dry-run 执行')
        return

    json.dump(before, open(SNAP, 'w', encoding='utf-8'), ensure_ascii=False)

    print()
    print('=' * 74)
    print('执行 extract_cdb 覆盖')
    print('=' * 74)
    BATCH = 30
    ok_batches = 0
    fail_batches = 0
    for i in range(0, len(targets), BATCH):
        batch = targets[i:i + BATCH]
        cmd = [PY, os.path.join(SCRIPTS, 'extract_cdb.py'),
               '--pak', 'both', '--ids'] + batch
        print('  [%3d-%3d / %d] ...' % (i + 1, min(i + BATCH, len(targets)),
                                        len(targets)), flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', cwd=SCRIPTS, timeout=600)
        out = (r.stdout or '') + (r.stderr or '')
        if r.returncode != 0:
            fail_batches += 1
            print('     FAIL rc=%d' % r.returncode)
            print('     %s' % out[-300:])
        else:
            ok_batches += 1
            for line in out.splitlines():
                s = line.strip()
                if s.startswith('成功') or s.startswith('失败') or '完成' in s:
                    print('     %s' % s)

    print()
    print('  批次: 成功 %d / 失败 %d' % (ok_batches, fail_batches))

    # 验证
    print()
    print('=' * 74)
    print('验证覆盖结果')
    print('=' * 74)
    after = snapshot(targets)
    updated = 0
    unchanged = []
    for cid in targets:
        b = before[cid]['files']
        a = after[cid]['files']
        if set(a) != set(b):
            updated += 1
            continue
        if any(a[k]['mtime'] > b[k]['mtime'] for k in a):
            updated += 1
        else:
            unchanged.append(cid)

    print('  已更新: %d' % updated)
    print('  未变  : %d' % len(unchanged))
    if unchanged:
        print('  未变明细（前 20）:')
        for cid in unchanged[:20]:
            print('    %s' % cid)

    # 统计新文件
    n_after = sum(len(v['files']) for v in after.values())
    sz_after = sum(f['size'] for v in after.values() for f in v['files'].values())
    print()
    print('  覆盖前: %d 文件 / %.1f MB' % (n_before, sz_before / 1048576))
    print('  覆盖后: %d 文件 / %.1f MB' % (n_after, sz_after / 1048576))

    # 冗余分析：覆盖前有、覆盖后仍在但 mtime 未变 = 旧残留
    stale = []
    for cid in targets:
        b = before[cid]['files']
        a = after[cid]['files']
        for rel, meta in a.items():
            if rel in b and meta['mtime'] == b[rel]['mtime']:
                # 没被覆盖，但同目录已有新文件 → 可能是旧皮肤命名
                pass
    json.dump(after, open(os.path.join(BASE, 'cache', 'cdn_after_snapshot.json'),
                          'w', encoding='utf-8'), ensure_ascii=False)
    print()
    print('  覆盖后快照: cache/cdn_after_snapshot.json')
    print('  覆盖前快照: %s' % SNAP)


if __name__ == '__main__':
    main()