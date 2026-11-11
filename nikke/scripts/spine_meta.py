"""从 Spine bundle 提取元数据：SoundEvents 音效时间轴 + SkeletonData 动画清单。

产出（每个角色/姿态目录下）：
    meta.json
    {
      "id": "c010",
      "pose": "fb",
      "spine_version": "4.1",
      "animations": ["action","angry",...],          # 来自 skeletonBinaryMetadata
      "animation_sizes": {"action": 133137, ...},     # 每个动画的 payload 字节长度
      "sounds": {                                     # 来自 SoundEvents
        "action": [
          {"name":"c010_00_action_1","time":0.4},     # time 单位 = 秒
          ...
        ]
      },
      "scale": 0.01,                                  # Unity 导入缩放（点击命中换算必需）
      "_source_key": "spinestandingcharactergroup(hd)..."
    }

字段来源（已实测 + 审核确认）：
  SkeletonData.read_typetree()['skeletonBinaryMetadata']
      eachAnimationStartIndexKeys / eachAnimationLengthValues
        → 动画名与每动画的 payload 字节数
        （审计验证：sum(lengthValues) == skel 文件长度 == totalLength）
  SkeletonData.read_typetree()['scale']
      → Unity 缩放系数（约 0.01）
  SoundEvents.read_typetree()['customEventTimelineDataList']
      [{animationName, customEventDataList:[{name,time,...}]}]
      → time 单位为秒（审计：值域 0.002~2.13，视作帧不可能）

注意：
  同一 bundle 内可能有多条相同 animationName 的 SoundEvents 条目，
  必须合并而不是覆盖，否则会静默丢事件。
  skin 相关字段不在 skeletonBinaryMetadata 里，需另行从 skel 字节解析
  （见 spine_skel.py）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import UnityPy

import nikke_cdb_lib as L


def read_mono_typetree(obj):
    try:
        return obj.read_typetree()
    except Exception:
        return None


def extract_meta(bundle_bytes):
    """从 bundle 提取元数据。返回 dict 或 None。"""
    env = UnityPy.load(bundle_bytes)

    skeleton_meta = None
    animations = []
    anim_sizes = {}
    scale = None
    sound_events = {}

    for o in env.objects:
        if o.type.name != 'MonoBehaviour':
            continue
        tree = read_mono_typetree(o)
        if not tree:
            continue
        name = tree.get('m_Name', '')

        if 'SkeletonData' in name and 'ExtraData' not in name:
            meta = tree.get('skeletonBinaryMetadata')
            if isinstance(meta, dict):
                skeleton_meta = meta
                keys = meta.get('eachAnimationStartIndexKeys') or []
                lens = meta.get('eachAnimationLengthValues') or []
                animations = [str(k) for k in keys]
                if len(keys) == len(lens):
                    anim_sizes = {str(k): int(v) for k, v in zip(keys, lens)}
                skel_bytes_for_verify = None   # 后面从 TextAsset 拿
            if isinstance(tree.get('scale'), (int, float)):
                scale = tree['scale']

        elif 'SoundEvents' in name:
            lst = tree.get('customEventTimelineDataList') or []
            for entry in lst:
                if not isinstance(entry, dict):
                    continue
                anim = entry.get('animationName')
                if not anim:
                    continue
                evs = []
                for ev in entry.get('customEventDataList') or []:
                    if not isinstance(ev, dict):
                        continue
                    evs.append({
                        'name': ev.get('name', ''),
                        'time': round(float(ev.get('time', 0.0)), 4),
                        'particle': ev.get('particleAddress', '') or '',
                        'attach': ev.get('targetPointAttachmentName', '') or '',
                    })
                if not evs:
                    continue
                # 同一 animationName 可能出现多条；合并而非覆盖。
                # 同一 (name, time) 视为同一发声点，去重；
                # 同 name 不同 time 是不同发声点，保留。
                bucket = sound_events.setdefault(str(anim), [])
                seen = {(e['name'], e['time']) for e in bucket}
                for e in evs:
                    key2 = (e['name'], e['time'])
                    if key2 not in seen:
                        bucket.append(e)
                        seen.add(key2)
                bucket.sort(key=lambda x: x['time'])

    # 从 skel 头部读版本，并拿到完整字节用于验证动画名
    version = None
    skel_bytes = None
    for o in env.objects:
        if o.type.name == 'TextAsset':
            try:
                d = o.read()
                if not d.m_Name.endswith('.skel'):
                    continue
                raw = d.m_Script
                # ⚠️ UnityPy 把这个 TextAsset 解成 str，但内容其实不是合法 UTF-8
                # （前 2 字节 e7 9b 而非 Spine magic 10 9b）。
                # 直接 .encode('utf-8') 会把非ASCII字节重编码，导致 find() 位置错误。
                # 正确做法：用 surrogateescape 编回原始字节序列——
                # 这正是 UnityPy 解码时用的 error handler，能 1:1 还原。
                if isinstance(raw, str):
                    raw = raw.encode('utf-8', 'surrogateescape')
                skel_bytes = raw
                version = L.spine_version(raw)
                break
            except Exception:
                pass

    # ═══ 用 skel 字节验证动画名 ═══════════════════════════════════
    #
    # ⚠️ 重要结论修正（2026-11-11 全量实测）：
    #   `eachAnimationStartIndexKeys` 返回的动画名列表是【完全正确】的。
    #   全量 749 个 meta 用 UnityPy 解码字节校验，0 个含不存在的名字。
    #
    #   曾被误判为"幽灵"的 talk_a / talk_close / talk_i / talk_u，
    #   实际上全部真实存在于 skel 中，且带合法的 varint(len+1) 前缀。
    #
    #   误判的根源是【验证方法错了】：
    #     .skel 文件里有 8 字节加密头，与 UnityPy 解码出的字节不一致。
    #     c010: 文件 930074 B  vs  Unity 933413 B（差 3339 B）
    #     c017: 文件 = Unity（恰好没加密）
    #   直接 open('.skel').read() 再 find(名字) 会在加密过的文件上失败，
    #   从而把真实动画误报为"幽灵"。
    #
    #   本段校验保留作为防御（万一将来某个版本真的出现名池与实体不一致），
    #   判据：名字必须以 varint(len+1) 前缀形式出现在 skel 字节中，
    #   且要扫描【所有】出现位置（strings 池里可能有同名 entry 干扰）。
    verified = []
    dropped = []
    if skel_bytes:
        for a in animations:
            enc = a.encode('utf-8')
            n = len(enc) + 1
            if n < 128:
                prefix_byte = bytes([n])
            else:
                prefix_byte = bytes([(n & 0x7F) | 0x80])
            # 扫描【所有】出现位置，只要有一处前缀是 varint(len+1) 就算真动画。
            # 不能只看 find() 的第一个结果 —— 例如 `idle`/`no` 在 strings 池里
            # 也有同名 entry（前缀不合法），但真正的动画定义在后面。
            found = False
            start = 0
            while True:
                p = skel_bytes.find(enc, start)
                if p < 0:
                    break
                if p > 0 and skel_bytes[p - 1:p] == prefix_byte:
                    found = True
                    break
                start = p + 1
            if found:
                verified.append(a)
            else:
                dropped.append(a)
    else:
        # 拿不到 skel 字节时无法验证，保守返回原始列表
        verified = animations

    if dropped:
        # 兜底：如果全部被判掉（说明验证逻辑对这批数据不适用），保留原列表
        if not verified:
            verified = animations
            dropped = []

    animations = verified
    if dropped:
        anim_sizes = {k: v for k, v in anim_sizes.items() if k in verified}

    if not animations and not sound_events:
        return None

    result = {
        'spine_version': version,
        'animations': animations,
        'animation_sizes': anim_sizes,
        'animation_count': len(animations),
        'scale': scale,
        'sounds': sound_events,
        'sound_animation_count': len(sound_events),
        'has_skeleton_meta': bool(animations),
        # ⚠️ 语义说明（2026-11-11 实测确认）：
        #   animations 来自 skeletonBinaryMetadata，是「Unity 导出 skel 时的设计清单」。
        #   它可能比 runtime 实际可播的多几个 —— 实测 c010 有 16 个，
        #   而 spine-player 的 skeletonData.animations 只有 12 个。
        #   差的 4 个（talk_a 269B / talk_close 158B / talk_i 330B / talk_u 729B）
        #   在 skel 字节里确实存在（有合法 varint 前缀 + 非零 payload），
        #   但被 Unity 的 Spine 导入器丢弃了（推测是只含 attachment 切换、
        #   没有骨骼/槽位 timeline 的动画被视为"无实质内容"）。
        #   所以：需要「按清单列动画」时可直接用本字段；
        #        需要「确认能否 setAnimation」时应查 runtime 的 skeletonData.animations。
        'animations_note': 'design-time list from skeletonBinaryMetadata; '
                           'runtime skeletonData.animations may be fewer',
    }
    if dropped:
        # 留痕：原始列表里有、但 skel 字节验证不存在的名字
        result['_dropped_animations'] = dropped
        result['_raw_animation_count'] = len(verified) + len(dropped)
    return result
