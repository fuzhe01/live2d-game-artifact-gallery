"""从下载好的 ZIP 里提取 config.json，构建 subtitles.json。

数据源：PlumButa/Nikke-L2D-Desktop-Viewer
路径：assets/characters/char_<资源ID>[_N]/config.json

只用 ZIP 里的 294 个 config.json（3.78 MB），不解压 3.6 GB 的模型资源。
"""
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C

ZIP = os.path.join(C.BASE, 'cache', 'plumbuta.zip')
CACHE = os.path.join(C.BASE, 'cache', 'plumbuta_cfg')
OUT = os.path.join(C.DATA_DIR, 'subtitles.json')

# category（繁体）→ 我们的分类名
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


def main():
    if not os.path.exists(ZIP):
        print('缺少 ZIP: %s' % ZIP)
        return
    os.makedirs(CACHE, exist_ok=True)

    print('=== 从 ZIP 提取 config.json ===')
    extracted = {}
    with zipfile.ZipFile(ZIP) as z:
        names = [n for n in z.namelist()
                 if re.search(r'assets/characters/([^/]+)/config\.json$', n)]
        print('  匹配 %d 个' % len(names))
        for n in names:
            m = re.search(r'assets/characters/([^/]+)/config\.json$', n)
            dirname = m.group(1)
            try:
                data = json.loads(z.read(n).decode('utf-8'))
            except Exception as e:
                print('    %s 解析失败: %s' % (dirname, e))
                continue
            extracted[dirname] = data
            # 缓存一份
            with open(os.path.join(CACHE, dirname + '.json'), 'w',
                      encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
    print('  成功解析: %d' % len(extracted))

    # 构建字幕表
    print()
    print('=== 映射到我们的 cid ===')
    subs = {}
    unmapped = {}
    no_rid = 0
    for dirname, js in extracted.items():
        rid = js.get('resourceId')
        if rid is None:
            no_rid += 1
            continue
        # char_108001 → rid=80 → c080；char_108001_2 → c080_01
        m = re.search(r'_(\d)$', dirname)
        suffix = ''
        if m and m.group(1) in '2345':
            suffix = '_%02d' % (int(m.group(1)) - 1)
        cid = 'c%03d%s' % (int(rid), suffix)

        bucket = subs.setdefault(cid, {})
        for it in (js.get('interactions') or []):
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

    # 去掉空桶
    subs = {k: v for k, v in subs.items() if v}

    print('  角色数: %d' % len(subs))
    total = sum(len(v) for v in subs.values())
    print('  字幕条目: %d' % total)
    if no_rid:
        print('  无 resourceId: %d' % no_rid)
    if unmapped:
        print()
        print('  未映射 category (%d 种):' % len(unmapped))
        for k, v in sorted(unmapped.items(), key=lambda x: -x[1])[:15]:
            print('    %-32s %d' % (k, v))

    # 与本地角色对照
    print()
    print('=== 与本地数据对照 ===')
    local_cids = set()
    for d in os.listdir(C.DATA_DIR):
        p = os.path.join(C.DATA_DIR, d)
        if os.path.isdir(p) and d.startswith('c') and d[1:4].isdigit():
            local_cids.add(d)
    have = set(subs.keys())
    print('  本地角色目录: %d' % len(local_cids))
    print('  字幕角色    : %d' % len(have))
    print('  两者都有    : %d' % len(have & local_cids))
    print('  只有字幕    : %d' % len(have - local_cids))
    print('  只有本地    : %d' % len(local_cids - have))

    # 写出
    out = {
        '_meta': {
            'source': 'https://github.com/PlumButa/Nikke-L2D-Desktop-Viewer',
            'license': '素材版权属 SHIFT UP / Level Infinite，仅供个人学习研究',
            'characters': len(subs),
            'entries': total,
            'category_map': CATEGORY_MAP,
        },
    }
    out.update(dict(sorted(subs.items())))
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print()
    print('已写出: %s (%.1f KB)' % (OUT, os.path.getsize(OUT) / 1024))

    # 抽样
    print()
    print('=== 抽样 ===')
    for cid in ('c010', 'c080', 'c521'):
        if cid in subs:
            print('  %s: %d 条' % (cid, len(subs[cid])))
            for k in ('Lobby_Touch_1', 'Lobby_Touch_Love_1', 'Gift_1', 'Win_1'):
                if k in subs[cid]:
                    v = subs[cid][k]
                    print('    %-22s [%s] %s' % (k, v['animation'], v['text'][:34]))


if __name__ == '__main__':
    main()
