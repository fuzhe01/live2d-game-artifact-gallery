"""把提取出的插画整理成 manifest.json，并按活动分组（用 nikke-db 的组名）。

输入：_work/gallery_webp/*.webp（extract_all_bg.py 产物）
      _work/nikkedb_gallery.json（nikke-db 组名映射）
输出：<画廊>/nikke/data/gallery/manifest.json + 复制 webp
"""
import json
import os
import re
import shutil
import sys

WORK = r'D:\nikke-toolchain\_work'
SRC = os.path.join(WORK, 'gallery_webp')
GAL = os.path.join(WORK, 'nikkedb_gallery.json')
DST = r'D:\live2d-game-artifact-gallery\nikke\data\gallery'
# 优先用随画廊提交的副本（可重建）
GAL_TRACKED = r'D:\live2d-game-artifact-gallery\nikke\scripts\names\nikkedb_gallery.json'


def load_group_titles():
    """gallery id → title（规范化匹配用）"""
    titles = {}
    path = GAL_TRACKED if os.path.exists(GAL_TRACKED) else GAL
    if not os.path.exists(path):
        return titles
    try:
        g = json.load(open(path, encoding='utf-8'))['gallery']
        for x in g:
            gid = (x.get('id') or '').lower()
            if gid and x.get('title'):
                titles[gid] = x['title']
    except Exception as e:
        print('  读取 gallery 索引失败: %s' % str(e)[:50])
    return titles


def norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def group_of(name):
    """从资源名推分组：
       eventscene_absolute_01_1     → absolute
       eventscene_arkguardian_01     → arkguardian
       eventscene_chap_49_01_1       → chap_49      （保留章号）
       eventscene_side_01_02         → side_01      （保留支线号）
       eventscene_main_02_01         → main_02
       非 eventscene（纯场景背景）    → scenery（统一一组）
    """
    if not re.match(r'^eventscene_', name, re.I):
        return 'scenery'
    m = re.match(r'^eventscene_(.+)$', name, re.I)
    rest = m.group(1) if m else name
    # 章节/支线/主线：保留「前缀_编号」两段，去掉其后的图序号
    m2 = re.match(r'^(chap|main|side)_(\d+)(?:_|$)', rest, re.I)
    if m2:
        return '%s_%s' % (m2.group(1).lower(), m2.group(2))
    # 其余：去掉所有尾部 _数字
    rest = re.sub(r'(_\d+)+$', '', rest)
    rest = re.sub(r'_bg$', '', rest, flags=re.I)
    return rest.lower() or 'other'


def title_of(group, titles):
    """组名 → 展示标题：先查 nikke-db，再按规则美化"""
    g = group.lower()
    # 纯场景背景统一一组
    if g == 'scenery':
        return '场景背景'
    if g in titles:
        return titles[g]
    # 规范化匹配（ark_guardian ↔ arkguardian）
    ng = norm(g)
    for k, v in titles.items():
        if norm(k) == ng:
            return v
    # 章节组：chap_49 → Chapter 49 / main_02 → Main 02
    m = re.match(r'^(chap|main|side)_(\d+)$', g)
    if m:
        prefix = {'chap': 'Chapter', 'main': 'Main', 'side': 'Side Story'}[m.group(1)]
        return '%s %s' % (prefix, int(m.group(2)))
    # 回退：连字符拆分 + 首字母大写
    return ' '.join(w.capitalize() for w in re.split(r'[_-]+', g) if w)


def main():
    if not os.path.isdir(SRC):
        print('❌ 源目录不存在: %s' % SRC)
        return
    files = sorted(f for f in os.listdir(SRC) if f.lower().endswith('.webp'))
    print('找到 webp: %d 个' % len(files))

    titles = load_group_titles()
    print('gallery 组名映射: %d 条' % len(titles))

    # 读提取脚本产出的尺寸信息（若有）
    meta = {}
    mp = os.path.join(SRC, 'manifest.json')
    if os.path.exists(mp):
        try:
            for it in json.load(open(mp, encoding='utf-8')).get('items') or []:
                meta[it['name']] = it
        except Exception:
            pass
    print('已有尺寸信息: %d 条' % len(meta))

    items = []
    for f in files:
        name = f[:-5]                      # 去 .webp
        g = group_of(name)
        it = {
            'name': name,
            'file': f,
            'group': g,
            'title': title_of(g, titles),
        }
        if name in meta:
            it['w'] = meta[name].get('w')
            it['h'] = meta[name].get('h')
        it['bytes'] = os.path.getsize(os.path.join(SRC, f))
        items.append(it)

    # 分组汇总
    from collections import Counter
    cnt = Counter(x['group'] for x in items)
    groups = []
    for g, n in sorted(cnt.items()):
        groups.append({'id': g, 'title': title_of(g, titles), 'count': n})

    os.makedirs(DST, exist_ok=True)
    # 复制图片
    print('复制图片到 %s ...' % DST)
    copied = 0
    for f in files:
        dst = os.path.join(DST, f)
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(os.path.join(SRC, f)):
            shutil.copy2(os.path.join(SRC, f), dst)
            copied += 1
    print('  复制 %d 个（已存在的跳过）' % copied)

    out = {'count': len(items), 'groups': groups, 'items': items}
    json.dump(out, open(os.path.join(DST, 'manifest.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    total = sum(x['bytes'] for x in items)
    print()
    print('manifest: %d 张 / %d 组 / %.1f MB' % (
        len(items), len(groups), total / 1024**2))
    print('  组规模前 12:')
    for g in sorted(groups, key=lambda x: -x['count'])[:12]:
        print('    %-28s %3d 张  %s' % (g['id'], g['count'], g['title']))
    print()
    print('输出: %s' % os.path.join(DST, 'manifest.json'))


if __name__ == '__main__':
    main()