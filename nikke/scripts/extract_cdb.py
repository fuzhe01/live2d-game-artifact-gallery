"""从本地 NIKKE 客户端 store.cdb 提取 Spine 资源（fb/cover/aim）。

用法：
    python extract_cdb.py                        # 提取全部（hd 优先，跳过 sd）
    python extract_cdb.py --pak dp               # 只提 dp
    python extract_cdb.py --ids c010 c470        # 只提指定角色
    python extract_cdb.py --limit 20             # 只处理前 20 个
    python extract_cdb.py --list                 # 只列出
    python extract_cdb.py --include-sd           # 同时提 SD 版本
    python extract_cdb.py --skip-existing        # 断点续跑

输出：nikke/data/<id>/[cover|aim]/<stem>.skel|.atlas|.png
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C
import nikke_cdb_lib as L

CLIENT_ROOT = os.environ.get(
    'NIKKE_CLIENT_PATCH',
    r'D:\nikke\Unity\com_proximabeta_NIKKE\com.shiftup.patch')
WORK_DIR = os.path.join(C.BASE, 'cache', 'cdb')

POSE_SUBDIR = {'fb': '', 'cover': 'cover', 'aim': 'aim'}

# 优先级：数字越小越优先（同一 id+pose 只保留最优）
GROUP_PRIORITY = {
    'spinestandingcharactergroup(hd)': 1,
    'spinestandingcharactergroup(sd)': 5,
    'spinestandingprefabgroup(hd)': 3,
    'spinestandingprefabgroup(sd)': 7,
    'spinecombatcharactergroup(hd)': 1,
    'spinecombatcharactergroup(sd)': 5,
}


def group_of(key):
    return key.split('_assets_')[0]


def out_dir_for(data_dir, cid, pose):
    base = os.path.join(data_dir, cid)
    sub = POSE_SUBDIR.get(pose, pose)
    return os.path.join(base, sub) if sub else base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pak', choices=['core', 'dp', 'both'], default='both')
    ap.add_argument('--ids', nargs='*', help='只提指定角色 id')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--include-sd', action='store_true',
                    help='同时提取 SD（低分辨率）版本')
    ap.add_argument('--skip-existing', action='store_true')
    ap.add_argument('--with-prefab', action='store_true',
                    help='也提取 standingprefabgroup')
    args = ap.parse_args()

    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(C.DATA_DIR, exist_ok=True)

    paks = ['core', 'dp'] if args.pak == 'both' else [args.pak]
    grand = {'cand': 0, 'ok': 0, 'skip': 0, 'fail': 0, 'none': 0}
    done_keys = {}
    ids_seen = {}

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
        print('  catalog/CIDX 就绪 (%d 条, %.1fs)' % (len(cidx), time.time() - t0))

        rows = con.execute(
            "SELECT file_id, key FROM files_chunktype "
            "WHERE key LIKE '%_assets_spine/%' ORDER BY file_id").fetchall()
        print('  spine bundle 共 %d 个' % len(rows))

        # 归并：(cid, pose) -> 最优候选
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
        print('  去重后待提取: %d 个 (id,pose) 组合' % len(items))
        if args.limit:
            items = items[:args.limit]
            print('  --limit 后: %d' % len(items))

        if args.list:
            for (cid, pose), (prio, fid, key) in items:
                print('    %-16s %-6s p%d [%6d] %s' % (cid, pose, prio, fid, key))
            reader.close()
            con.close()
            continue

        for i, ((cid, pose), (prio, fid, key)) in enumerate(items, 1):
            odir = out_dir_for(C.DATA_DIR, cid, pose)
            if args.skip_existing and os.path.isdir(odir):
                if any(f.endswith('.skel') for f in os.listdir(odir)):
                    grand['skip'] += 1
                    continue
            grand['cand'] += 1
            try:
                bundle = L.reassemble(con, reader, fid)
                info = L.extract_spine_from_bundle(bundle, odir)
                if not info or not info['skel']:
                    grand['none'] += 1
                    continue
                grand['ok'] += 1
                ids_seen[cid] = ids_seen.get(cid, 0) + 1
                if i <= 10 or i % 100 == 0:
                    print('  [%d/%d] %-18s %-6s v%-4s skel=%d png=%d' % (
                        i, len(items), cid, pose, info['version'] or '?',
                        len(info['skel']), len(info['png'])))
            except Exception as e:
                grand['fail'] += 1
                if grand['fail'] <= 10:
                    print('  [%d/%d] %-18s %-6s FAIL %s' % (i, len(items), cid, pose, e))

        reader.close()
        con.close()
        print('  pak %s 完成 %.1fs' % (pak, time.time() - t0))

    print()
    print('=' * 70)
    print('提取汇总:')
    print('  候选组合   : %d' % grand['cand'])
    print('  成功       : %d' % grand['ok'])
    print('  跳过(已有) : %d' % grand['skip'])
    print('  无 skel    : %d' % grand['none'])
    print('  失败       : %d' % grand['fail'])
    print('  角色数     : %d' % len(ids_seen))
    print()
    print('下一步: python gen_nikke_manifest.py')


if __name__ == '__main__':
    main()
