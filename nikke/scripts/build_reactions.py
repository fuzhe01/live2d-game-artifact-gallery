"""生成互动配置 reactions.json：合并 CharacterReactionTable + spine meta。

产出：nikke/data/reactions.json
{
  "characters": {
    "c010": {
      "touch":       [{"index":1,"reaction_id":"...","animation":"action","voice_key":"...","group_id":0}, ...],
      "touch_love":  [...],
      "stay":        [...],
      "stay_love":   [...]
    },
    ...
  },
  "_meta": {...统计...}
}
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C
import reaction_table as R
import staticdata_lib as S


def main():
    print('=== 解密 CharacterReactionTable ===')
    raw = S.load_table('CharacterReactionTable.mpk')
    print('  %d bytes' % len(raw))

    print('=== 解析记录 ===')
    records = R.parse_table(raw)
    print('  %d 条' % len(records))

    gids = Counter(r['group_id'] for r in records)
    print('  group_id 分布: %s' % dict(sorted(gids.items())))

    # 载入各角色的 fb meta，用于校验动画名是否存在
    data_dir = C.DATA_DIR
    meta_lookup = {}
    have_spine = set()
    if os.path.isdir(data_dir):
        for n in os.listdir(data_dir):
            p = os.path.join(data_dir, n)
            if not os.path.isdir(p):
                continue
            if not any(f.endswith('.skel') for f in os.listdir(p)):
                continue
            have_spine.add(n)
            mp = os.path.join(p, 'meta.json')
            if os.path.exists(mp):
                try:
                    meta_lookup[n] = json.load(open(mp, encoding='utf-8'))
                except Exception:
                    pass
    print('  有 spine 资源的目录: %d（其中 %d 有 meta）' % (len(have_spine), len(meta_lookup)))

    print('=== 聚合（去重维度 = (reaction_id, skin)，保留变体）===')
    m = R.build_reaction_map(records, prefer_group=0, meta_lookup=meta_lookup)
    print('  角色数: %d' % len(m))

    n_react = 0
    n_variant = 0
    anim_ok = 0
    anim_fallback = 0
    anim_verified_fail = 0
    anim_no_meta = 0
    fallback_list = []
    for cid, kinds in m.items():
        for bkey, items in kinds.items():
            for it in items:
                n_react += 1
                for v in it['variants']:
                    n_variant += 1
                    if v.get('fallback_from'):
                        anim_fallback += 1
                        if len(fallback_list) < 20:
                            fallback_list.append(
                                (cid, it['reaction_id'], v['fallback_from'], v['animation']))
                    elif v.get('animation_exists') is True:
                        anim_ok += 1
                    elif v.get('animation_exists') is False:
                        # meta 存在、明确校验失败但无法降级（罕见）
                        anim_verified_fail += 1
                    else:
                        # 真正无 meta 可校验（当前为 0）
                        anim_no_meta += 1

    print('  反应条目数: %d' % n_react)
    print('  动画变体数: %d' % n_variant)
    print('  原生动画存在      : %d' % anim_ok)
    print('  已降级(留痕)      : %d' % anim_fallback)
    print('  校验失败无法降级  : %d' % anim_verified_fail)
    print('  无 meta 无法校验  : %d' % anim_no_meta)
    if fallback_list:
        print('  fallback 明细（前 20）:')
        for cid, rid, frm, to in fallback_list:
            print('    %-14s %-28s %s -> %s' % (cid, rid, frm, to))

    covered = sum(1 for cid in m if cid in have_spine)
    print('  有 spine 资源的角色: %d / %d' % (covered, len(m)))

    out = {
        'characters': dict(sorted(m.items())),
        '_meta': {
            'source_table': 'CharacterReactionTable.mpk',
            'total_records': len(records),
            'dedup_characters': len(m),
            'reaction_entries': n_react,
            'animation_variants': n_variant,
            'dedup_dimension': '(reaction_id, unity_field_skin)',
            'group_id_distribution': dict(sorted(gids.items())),
            'characters_with_spine': covered,
            'animation_source': 'CharacterReactionTable.skin 字段（实测即动画名）',
            'animation_validation': {
                'variants_total': n_variant,
                'variants_exist': anim_ok,
                'fallback_applied': anim_fallback,
                'verified_fail_no_fallback': anim_verified_fail,
                'unverified_no_meta': anim_no_meta,
                'note': 'fallback 的 28 条是真实校验结果（该角色 skel 无 expression_1），已降级并留 fallback_from；unverified_no_meta 为真正无 meta 的变体数，当前 0',
            },
            'voice_source': 'ja pak .nab -> AudioClip 名 == reaction_id',
            'missing_voice': {
                'c530': '8 条，客户端 ja 语言包未收录该角色语音（spine 资源完整）',
            },
            'record_layout': {
                'header': '[u8 0x0D][9 x u32] = 37 B, gid at header+29',
                'strings': '3 x MemoryPack Utf8String (skin / voiceKey / reactionId)',
                'tail': '2 x f32 (f32#0 恒 0.0；f32#1 ∈ {1.45,1.575,1.65,1.70,1.75,1.875}，疑为时长/权重)',
            },
            'note': '同一 reaction_id 可有多个 animation 变体（variant），游戏按条件/随机播放；不要按 reaction_id 单独去重',
        },
    }

    dest = os.path.join(data_dir, 'reactions.json')
    os.makedirs(data_dir, exist_ok=True)
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print('已写出: %s (%d bytes)' % (dest, os.path.getsize(dest)))

    # 抽样验证
    print()
    print('=== 抽样 ===')
    for cid in ('c010', 'c403', 'c521'):
        if cid not in m:
            continue
        print('  %s:' % cid)
        for k in ('touch', 'touch_love', 'stay', 'stay_love'):
            items = m[cid].get(k)
            if not items:
                continue
            for it in items:
                va = ','.join(v['animation'] for v in it['variants'])
                print('    %-11s #%d %-28s variants=[%s]' % (
                    k, it['index'], it['reaction_id'], va))


if __name__ == '__main__':
    main()
