"""从 PlumButa/Nikke-L2D-Desktop-Viewer 抓取角色语音字幕。

数据源：assets/characters/char_<资源ID>[_N]/config.json
每条 config 含 interactions[]: {id, category, audio, text, animation}

产出：nikke/data/subtitles.json
{
  "c080": {
    "Lobby_Touch_1":    {"text": "...", "category": "在大廳點擊時I",        "animation": "action"},
    "Lobby_Touch_Love_1": {...},
    ...
  },
  "_meta": {...}
}

category → 我们分类名的映射规则见 CATEGORY_MAP。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C

RAW = 'https://raw.githubusercontent.com/PlumButa/Nikke-L2D-Desktop-Viewer/main'
API = 'https://api.github.com/repos/PlumButa/Nikke-L2D-Desktop-Viewer/contents/assets/characters'
CACHE = os.path.join(C.BASE, 'cache', 'plumbuta')
OUT = os.path.join(C.DATA_DIR, 'subtitles.json')

# category（繁体）→ 我们的分类名
# 注意：同名 category 在不同角色可能对应不同 animation（如「前哨基地點擊II」→ shy），
# 所以 animation 必须逐条从原字段读，不能按 category 统一。
CATEGORY_MAP = {
    '在大廳點擊時I': 'Lobby_Touch_1',
    '在大廳點擊時II': 'Lobby_Touch_2',
    '在大廳點擊時III': 'Lobby_Touch_3',
    '在大廳點擊時（好感度10）I': 'Lobby_Touch_Love_1',
    '在大廳點擊時（好感度10）II': 'Lobby_Touch_Love_2',
    '在大廳點擊時（好感度10）III': 'Lobby_Touch_Love_3',
    '在大廳閒置時': 'Lobby_Stay_1',
    '在大廳閒置時（好感度10）I': 'Lobby_Stay_Love_1',
    '在前哨基地點擊I': 'Outpost_Touch_1',
    '在前哨基地點擊II': 'Outpost_Touch_2',
    '在前哨基地點擊III': 'Outpost_Touch_3',
    '初次見面': 'Gacha_Get_1',
    '戰鬥力提升I': 'Reinforce_1',
    '戰鬥力提升II': 'Reinforce_2',
    '戰鬥力提升III': 'Reinforce_3',
    '禮物I': 'Gift_1',
    '禮物II': 'Gift_2',
    '禮物III': 'Gift_3',
    '好感度提升I': 'Attractive_Up_1',
    '好感度提升II': 'Attractive_Up_2',
    '好感度提升III': 'Attractive_Up_3',
    '編隊': 'Party_Deploy_1',
    '在野外點擊部隊': 'Field_Touch_1',
    '在野外移動部隊': 'Field_Go_1',
    '進入戰鬥': 'Battle_Enter_1',
    '開始戰鬥I': 'Battle_Ready_1',
    '開始戰鬥II': 'Battle_Ready_2',
    '開始戰鬥III': 'Battle_Ready_3',
    '換彈I': 'Reload_1',
    '換彈II': 'Reload_2',
    '換彈III': 'Reload_3',
    '可使用爆裂技能': 'Ult_Skill_Ready_1',
    '爆裂技能': 'Ult_Skill_1',
    '全爆裂': 'FullBurst_1',
    '消滅敵人I': 'Kill_Monster_1',
    '消滅敵人II': 'Kill_Monster_2',
    '消滅敵人III': 'Kill_Monster_3',
    '被擊I': 'Damage_1',
    '被擊II': 'Damage_2',
    '被擊III': 'Damage_3',
    '無法戰鬥': 'Die_1',
    '戰鬥勝利': 'Win_1',
}


def fetch(url, retries=3, timeout=25):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'nikke-gallery/1.0',
                'Accept': 'application/vnd.github+json',
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 403 or e.code == 429:
                # rate limit：等久一点
                wait = 30 * (i + 1)
                print('    限速，等 %ds 重试...' % wait)
                time.sleep(wait)
                continue
            if i == retries - 1:
                raise
        except Exception:
            if i == retries - 1:
                raise
        time.sleep(1.5 * (i + 1))
    return None


def list_dirs():
    """用 Git Trees API 一次拿到所有目录（避免 contents API 的分页+限速）。

    trees API 只需 1 次请求，且返回全部路径。
    """
    # 先拿 main 分支的 tree sha
    raw = fetch('https://api.github.com/repos/PlumButa/'
                'Nikke-L2D-Desktop-Viewer/git/trees/main')
    if not raw:
        print('  取 tree 失败')
        return []
    try:
        tree = json.loads(raw.decode('utf-8'))
    except Exception:
        return []
    entries = tree.get('tree') or []
    # 找出 assets/characters 那一层的 sha
    sha = None
    for e in entries:
        if e.get('path') == 'assets' and e.get('type') == 'tree':
            # 需要再进一层
            sha = e.get('sha')
            break
    if not sha:
        return []

    def walk(parent_sha, depth=0):
        if depth > 3:
            return []
        r = fetch('https://api.github.com/repos/PlumButa/'
                  'Nikke-L2D-Desktop-Viewer/git/trees/' + parent_sha)
        if not r:
            return []
        try:
            t = json.loads(r.decode('utf-8'))
        except Exception:
            return []
        out = []
        for e in t.get('tree') or []:
            if e.get('type') != 'tree':
                continue
            p = e.get('path', '')
            if depth == 2 and p.startswith('char_'):
                out.append(p)
            elif depth < 2:
                out.extend(walk(e.get('sha'), depth + 1))
        return out

    # assets(tree) → characters(tree) → char_*(tree)
    r2 = fetch('https://api.github.com/repos/PlumButa/'
               'Nikke-L2D-Desktop-Viewer/git/trees/' + sha)
    if not r2:
        return []
    t2 = json.loads(r2.decode('utf-8'))
    chars_sha = None
    for e in t2.get('tree') or []:
        if e.get('path') == 'characters' and e.get('type') == 'tree':
            chars_sha = e.get('sha')
            break
    if not chars_sha:
        return []
    return walk(chars_sha, 2)


def dir_to_cid(name):
    """char_108001 → c080；char_108001_2 → c080_01

    规则（从实测推导）：
      resourceId = int(id[3:]) // 1000   → 108001 // 1000 = 108，但文件名是 c080
      实际：skel 名 c080_00.skel，所以 resourceId=80 来自 config 的 resourceId 字段。
      命名后缀 _2/_3 → 我们的 _01/_02
    """
    m = re.match(r'^char_(\d+?)(?:_(\d+))?$', name)
    if not m:
        return None
    rid, suffix = m.group(1), m.group(2)
    # 不能只靠目录名推 cid，必须读 config 里的 resourceId（后面会做）
    return (rid, int(suffix) if suffix else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 个（调试）')
    ap.add_argument('--force', action='store_true', help='忽略缓存')
    args = ap.parse_args()

    os.makedirs(CACHE, exist_ok=True)

    # 1. 拿目录列表（Git Trees API，一次性）
    print('=== 列目录 ===')
    dirs = list_dirs()
    print('目录总数: %d' % len(dirs))
    if args.limit:
        dirs = dirs[:args.limit]

    # 2. 逐个抓 config.json
    print()
    print('=== 抓取 config.json ===')
    subtitles = {}
    stat = {'ok': 0, 'cache': 0, 'fail': 0, 'no_inter': 0}
    unmapped = {}

    for i, d in enumerate(dirs, 1):
        cp = os.path.join(CACHE, d + '.json')
        js = None
        if os.path.exists(cp) and not args.force:
            try:
                js = json.load(open(cp, encoding='utf-8'))
                stat['cache'] += 1
            except Exception:
                js = None
        if js is None:
            url = '%s/assets/characters/%s/config.json' % (RAW, d)
            raw = fetch(url)
            if not raw:
                stat['fail'] += 1
                continue
            try:
                js = json.loads(raw.decode('utf-8'))
                open(cp, 'w', encoding='utf-8').write(json.dumps(js, ensure_ascii=False))
                stat['ok'] += 1
            except Exception as e:
                stat['fail'] += 1
                continue

        # 3. 映射到我们的 cid
        rid = js.get('resourceId')
        if rid is None:
            stat['no_inter'] += 1
            continue
        suffix = ''
        m = re.search(r'_(\d)$', d)
        if m and m.group(1) in '2345':
            suffix = '_%02d' % (int(m.group(1)) - 1)
        cid = 'c%03d%s' % (int(rid), suffix)

        inters = js.get('interactions') or []
        if not inters:
            stat['no_inter'] += 1
            continue

        bucket = subtitles.setdefault(cid, {})
        for it in inters:
            cat = (it.get('category') or '').strip()
            text = (it.get('text') or '').strip()
            anim = (it.get('animation') or '').strip()
            if not cat or not text:
                continue
            key = CATEGORY_MAP.get(cat)
            if not key:
                unmapped[cat] = unmapped.get(cat, 0) + 1
                continue
            bucket[key] = {
                'text': text,
                'category': cat,
                'animation': anim,
            }

        if i % 100 == 0:
            print('  [%d/%d] 角色 %d, 缓存 %d, 失败 %d' % (
                i, len(dirs), len(subtitles), stat['cache'], stat['fail']), flush=True)

    print()
    print('=' * 66)
    print('抓取结果')
    print('=' * 66)
    print('  新抓: %d   缓存: %d   失败: %d   无 interactions: %d' % (
        stat['ok'], stat['cache'], stat['fail'], stat['no_inter']))
    print('  角色数: %d' % len(subtitles))
    total = sum(len(v) for v in subtitles.values())
    print('  字幕条目: %d' % total)
    if unmapped:
        print('  未映射 category (%d):' % len(unmapped))
        for k, v in sorted(unmapped.items(), key=lambda x: -x[1])[:20]:
            print('    %-32s %d' % (k, v))

    # 4. 写出
    out = {
        '_meta': {
            'source': 'https://github.com/PlumButa/Nikke-L2D-Desktop-Viewer',
            'license': '素材版权属 SHIFT UP / Level Infinite，仅供个人学习研究',
            'characters': len(subtitles),
            'entries': total,
            'category_map': CATEGORY_MAP,
        },
    }
    out.update(subtitles)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    print()
    print('已写出: %s (%.1f KB)' % (OUT, os.path.getsize(OUT) / 1024))

    # 抽样
    for cid in ('c010', 'c080', 'c521'):
        if cid in subtitles:
            print()
            print('  %s: %d 条' % (cid, len(subtitles[cid])))
            for k in ('Lobby_Touch_1', 'Lobby_Touch_Love_1', 'Gift_1'):
                if k in subtitles[cid]:
                    print('    %-22s %s' % (k, subtitles[cid][k]['text'][:32]))


if __name__ == '__main__':
    main()
