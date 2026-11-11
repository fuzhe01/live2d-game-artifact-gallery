"""按分类导出角色语音 WAV。

与 export_voice.py 的区别：
  - 本脚本按【分类名】筛选，而非只导出 reactions.json 用到的
  - 用于填充「语音全集」面板

用法：
    python export_voice_by_category.py                # 默认常用分类
    python export_voice_by_category.py --all          # 全部 141 类
    python export_voice_by_category.py --cats Damage Gift Reload
    python export_voice_by_category.py --list         # 只列出分类统计
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C
import nikke_cdb_lib as L

CLIENT_ROOT = os.environ.get(
    'NIKKE_CLIENT_PATCH',
    r'D:\nikke\Unity\com_proximabeta_NIKKE\com.shiftup.patch')
WORK_DIR = os.path.join(C.BASE, 'cache', 'cdb')
INDEX_PATH = os.path.join(C.BASE, 'cache', 'ja_audio_index.json')
VOICE_DIR = os.path.join(C.DATA_DIR, 'voice')

# 默认导出的常用分类（覆盖「看角色、听语音」的主要场景）
DEFAULT_CATS = [
    'Lobby_Touch', 'Lobby_Touch_Love', 'Lobby_Stay', 'Lobby_Stay_Love',
    'Outpost_Touch',
    'Gift',
    'Battle_Ready', 'Battle_Enter',
    'Attractive_Up',
    'Ult_Skill', 'Ult_Skill_Ready', 'FullBurst',
    'Damage', 'Die', 'Win', 'Kill_Monster',
    'Reload', 'Reinforce',
    'Party_Deploy',
    'Gacha_Get',
    'Field_Touch', 'Field_Go',
]

CLIP_RE = re.compile(r'^c\d{3}(?:_\d{2})?_([A-Za-z0-9_]+?)_(\d+)$')


def categorize(name):
    m = CLIP_RE.match(name)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cats', nargs='*', help='分类名列表')
    ap.add_argument('--all', action='store_true', help='全部角色语音分类')
    ap.add_argument('--list', action='store_true', help='只列分类统计')
    ap.add_argument('--force', action='store_true', help='重新导出已存在的')
    args = ap.parse_args()

    if not os.path.exists(INDEX_PATH):
        print('缺少索引: %s' % INDEX_PATH)
        return
    index = json.load(open(INDEX_PATH, encoding='utf-8'))
    print('AudioClip 索引: %d' % len(index))

    # 只保留角色语音（c### 开头）
    char_clips = {}
    cat_counts = Counter()
    for name, e in index.items():
        cat, num = categorize(name)
        if cat is None:
            continue
        char_clips[name] = (cat, num, e)
        cat_counts[cat] += 1

    print('角色语音 clip: %d / %d 分类' % (len(char_clips), len(cat_counts)))

    if args.list:
        print()
        print('分类统计:')
        for c, n in cat_counts.most_common():
            print('  %-30s %5d' % (c, n))
        return

    if args.all:
        want = set(cat_counts.keys())
        print('导出全部 %d 类' % len(want))
    elif args.cats:
        want = set(args.cats)
    else:
        want = set(DEFAULT_CATS)
        print('导出默认 %d 类' % len(want))

    # 筛选
    targets = {n: v for n, v in char_clips.items() if v[0] in want}
    missing_cats = want - set(cat_counts.keys())
    print('目标 clip: %d' % len(targets))
    if missing_cats:
        print('指定但不存在: %s' % sorted(missing_cats))

    # 估算体积
    os.makedirs(VOICE_DIR, exist_ok=True)
    existing = set(f[:-4] for f in os.listdir(VOICE_DIR) if f.endswith('.wav'))
    todo = [n for n in targets if n not in existing or args.force]
    print('已存在: %d，待导出: %d' % (len(existing), len(todo)))
    if not todo:
        print('无需导出')
        return

    # 按 bundle 分组
    by_bundle = {}
    for n in todo:
        by_bundle.setdefault(targets[n][2]['file_id'], []).append(n)
    print('涉及 bundle: %d' % len(by_bundle))

    # 打开 ja pak
    import UnityPy
    pak_dir = os.path.join(CLIENT_ROOT, 'ja')
    con = L.open_catalog(pak_dir, WORK_DIR)
    cidx = L.load_cidx(os.path.join(pak_dir, 'chunk', 'store.cdb.idx'))
    reader = L.CdbReader(os.path.join(pak_dir, 'chunk', 'store.cdb'), cidx)

    t0 = time.time()
    ok = 0
    fail = 0
    total_bytes = 0

    for i, (fid, names) in enumerate(by_bundle.items(), 1):
        want_set = set(names)
        try:
            b = L.reassemble(con, reader, fid)
            env = UnityPy.load(b)
            for o in env.objects:
                if o.type.name != 'AudioClip':
                    continue
                d = o.read()
                nm = getattr(d, 'm_Name', '')
                if nm not in want_set:
                    continue
                try:
                    samples = d.samples
                except Exception:
                    fail += 1
                    continue
                if isinstance(samples, dict) and samples:
                    if len(samples) > 1:
                        print('  WARN %s 含 %d 个 sample' % (nm, len(samples)))
                    for si, (fname, data) in enumerate(samples.items()):
                        base = nm if len(samples) == 1 else '%s_%d' % (nm, si)
                        p = os.path.join(VOICE_DIR, base + '.wav')
                        open(p, 'wb').write(data)
                        total_bytes += len(data)
                        if len(data) > 44 and data[:4] == b'RIFF':
                            ok += 1
                        else:
                            fail += 1
        except Exception as e:
            fail += len(names)
            if fail <= 5:
                print('  bundle %d FAIL: %s' % (fid, e))
        if i % 50 == 0:
            print('  [%d/%d] 导出 %d, 失败 %d, %.1f MB, %.0fs' % (
                i, len(by_bundle), ok, fail, total_bytes / 1048576,
                time.time() - t0), flush=True)

    reader.close()
    con.close()

    print()
    print('=' * 66)
    print('导出完成 %.1fs' % (time.time() - t0))
    print('  成功 WAV : %d' % ok)
    print('  失败     : %d' % fail)
    print('  新增体积 : %.1f MB' % (total_bytes / 1048576))

    # 全量统计
    allw = [f for f in os.listdir(VOICE_DIR) if f.endswith('.wav')]
    sz = sum(os.path.getsize(os.path.join(VOICE_DIR, f)) for f in allw)
    print('  目录总计 : %d 个 / %.1f GB' % (len(allw), sz / 1073741824))


if __name__ == '__main__':
    main()
