"""批量提取全部 Spine 资源的元数据（SoundEvents + 动画清单）。

遍历 core + dp 的所有 spine bundle，把 meta.json 写到对应 data 目录。
与 extract_cdb.py 的目录映射保持一致。

用法：
    python extract_meta.py                 # 全部
    python extract_meta.py --ids c010      # 指定角色
    python extract_meta.py --limit 10      # 测试
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C
import nikke_cdb_lib as L
import spine_meta as M
from extract_cdb import CLIENT_ROOT, WORK_DIR, GROUP_PRIORITY, POSE_SUBDIR, group_of, out_dir_for


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pak', choices=['core', 'dp', 'both'], default='both')
    ap.add_argument('--ids', nargs='*')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--include-sd', action='store_true')
    ap.add_argument('--with-prefab', action='store_true')
    ap.add_argument('--force', action='store_true', help='覆盖已有 meta.json')
    args = ap.parse_args()

    os.makedirs(WORK_DIR, exist_ok=True)
    paks = ['core', 'dp'] if args.pak == 'both' else [args.pak]
    stats = {'cand': 0, 'ok': 0, 'skip': 0, 'none': 0, 'fail': 0}
    all_meta = {}

    for pak in paks:
        pak_dir = os.path.join(CLIENT_ROOT, pak)
        if not os.path.isdir(pak_dir):
            continue
        print('=' * 70)
        print('pak: %s' % pak)
        t0 = time.time()
        con = L.open_catalog(pak_dir, WORK_DIR)
        cidx = L.load_cidx(os.path.join(pak_dir, 'chunk', 'store.cdb.idx'))
        reader = L.CdbReader(os.path.join(pak_dir, 'chunk', 'store.cdb'), cidx)

        rows = con.execute(
            "SELECT file_id, key FROM files_chunktype "
            "WHERE key LIKE '%_assets_spine/%' ORDER BY file_id").fetchall()

        # 与 extract_cdb 相同的去重逻辑
        best = {}
        for fid, key in rows:
            g = group_of(key)
            prio = GROUP_PRIORITY.get(g)
            if prio is None:
                if not args.with_prefab:
                    continue
                prio = 50
            if not args.include_sd and '(sd)' in g:
                continue
            cid, pose = L.classify_key(key)
            if not cid:
                continue
            if args.ids and cid not in set(args.ids):
                continue
            k = (cid, pose)
            cur = best.get(k)
            if cur is None or prio < cur[0]:
                best[k] = (prio, fid, key)

        items = sorted(best.items(), key=lambda x: (x[0][0], x[0][1]))
        print('  待处理: %d 个 (id,pose)' % len(items))
        if args.limit:
            items = items[:args.limit]

        for i, ((cid, pose), (prio, fid, key)) in enumerate(items, 1):
            odir = out_dir_for(C.DATA_DIR, cid, pose)
            mpath = os.path.join(odir, 'meta.json')
            if not args.force and os.path.exists(mpath) and os.path.getsize(mpath) > 2:
                stats['skip'] += 1
                continue
            # 防孤儿：meta 只在对应目录已有 .skel 时才写
            if not os.path.isdir(odir) or not any(
                    f.endswith('.skel') for f in os.listdir(odir)):
                stats['no_skel_dir'] = stats.get('no_skel_dir', 0) + 1
                continue
            stats['cand'] += 1
            try:
                bundle = L.reassemble(con, reader, fid)
                meta = M.extract_meta(bundle)
                if not meta:
                    stats['none'] += 1
                    continue
                meta['id'] = cid
                meta['pose'] = pose
                meta['_source_key'] = key
                os.makedirs(odir, exist_ok=True)
                with open(mpath, 'w', encoding='utf-8') as fp:
                    json.dump(meta, fp, ensure_ascii=False, indent=1)
                stats['ok'] += 1
                all_meta[(cid, pose)] = meta
                if i <= 8 or i % 100 == 0:
                    print('  [%d/%d] %-16s %-6s anim=%-3d sound_anim=%-3d v%s' % (
                        i, len(items), cid, pose,
                        meta['animation_count'], meta['sound_animation_count'],
                        meta['spine_version']))
            except Exception as e:
                stats['fail'] += 1
                if stats['fail'] <= 8:
                    print('  [%d/%d] %-16s %-6s FAIL %s' % (i, len(items), cid, pose, e))

        reader.close()
        con.close()
        print('  pak %s 完成 %.1fs' % (pak, time.time() - t0))

    print()
    print('=' * 70)
    print('元数据提取汇总:')
    print('  候选     : %d' % stats['cand'])
    print('  成功     : %d' % stats['ok'])
    print('  跳过(已有): %d' % stats['skip'])
    if stats.get('no_skel_dir'):
        print('  无 skel 目录: %d' % stats['no_skel_dir'])
    print('  无元数据  : %d' % stats['none'])
    print('  失败     : %d' % stats['fail'])

    # 汇总统计
    if all_meta:
        anim_total = sum(m['animation_count'] for m in all_meta.values())
        sound_total = sum(m['sound_animation_count'] for m in all_meta.values())
        ev_total = sum(sum(len(v) for v in m['sounds'].values()) for m in all_meta.values())
        print()
        print('  动画条目总数   : %d' % anim_total)
        print('  带音效的动画数 : %d' % sound_total)
        print('  音效事件总数   : %d' % ev_total)


if __name__ == '__main__':
    main()
