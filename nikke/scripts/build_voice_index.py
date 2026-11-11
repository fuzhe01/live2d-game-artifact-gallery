"""从已导出的 WAV + ja 索引生成 voiceIndex.json（供语音全集面板用）。

输出结构：
  {"c010": {"Lobby_Touch": [{"id":"c010_Lobby_Touch_1","file":"...wav","len":1.93}, ...], ...}, ...}
"""
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C

VOICE_DIR = os.path.join(C.DATA_DIR, 'voice')
INDEX_PATH = os.path.join(C.BASE, 'cache', 'ja_audio_index.json')
OUT_PATH = os.path.join(C.DATA_DIR, 'voiceIndex.json')

CLIP_RE = re.compile(r'^(c\d{3}(?:_\d{2})?)_([A-Za-z0-9_]+?)_(\d+)$')


def main():
    if not os.path.isdir(VOICE_DIR):
        print('无 voice 目录')
        return

    have = [f[:-4] for f in os.listdir(VOICE_DIR) if f.endswith('.wav')]
    print('已导出 WAV: %d' % len(have))

    ja = {}
    if os.path.exists(INDEX_PATH):
        ja = json.load(open(INDEX_PATH, encoding='utf-8'))
        print('ja 索引: %d' % len(ja))
    else:
        print('WARN: 缺 ja 索引，时长将为 0')

    buckets = {}
    unmatched = 0
    for name in have:
        m = CLIP_RE.match(name)
        if not m:
            unmatched += 1
            continue
        cid, cat, num = m.group(1), m.group(2), int(m.group(3))
        e = ja.get(name) or {}
        buckets.setdefault(cid, {}).setdefault(cat, []).append({
            'id': name,
            'file': name + '.wav',
            'len': round(float(e.get('length', 0) or 0), 2),
            '_n': num,
        })

    # 排序并去掉临时字段
    total = 0
    for cid, cats in buckets.items():
        for cat, items in cats.items():
            items.sort(key=lambda x: x['_n'])
            for it in items:
                del it['_n']
            total += len(items)

    print('聚合角色: %d' % len(buckets))
    print('聚合 clip: %d' % total)
    if unmatched:
        print('未匹配命名规则: %d' % unmatched)

    catstat = Counter()
    for cats in buckets.values():
        for k, v in cats.items():
            catstat[k] += len(v)
    print('分类数: %d' % len(catstat))
    print()
    print('分类明细:')
    for k, v in catstat.most_common():
        print('  %-28s %5d' % (k, v))

    json.dump(buckets, open(OUT_PATH, 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    print()
    print('已写出: %s (%.1f KB)' % (OUT_PATH, os.path.getsize(OUT_PATH) / 1024))

    # 抽样
    for cid in ('c010', 'c080', 'c530'):
        if cid in buckets:
            d = buckets[cid]
            n = sum(len(v) for v in d.values())
            print('  %s: %d 类 / %d 条' % (cid, len(d), n))


if __name__ == '__main__':
    main()
