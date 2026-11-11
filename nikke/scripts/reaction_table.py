"""解析 CharacterReactionTable → 每角色的互动反应映射。

结构（实测推导）：
  条目由 3 个连续 MemoryPack 字符串组成：
    [i32 len][utf8]  animationName     如 expression_0 / idle / angry
    [i32 len][utf8]  voiceLocaleKey    如 Locale_Character:10_speech_01
    [i32 len][utf8]  reactionId        如 c010_Lobby_Touch_1
  三条之间有 4 字节间隔，最后一条后面约 49 字节尾部数据（含参数）
  reactionId 命名规则：
    c010_Lobby_Touch_{1,2,3}           普通触摸
    c010_Lobby_Touch_Love_{1,2,3}      好感触摸
    c010_Lobby_Stay_{n}                待机
    c010_Lobby_Stay_Love_{n}           好感待机
"""
import re
import struct


def read_utf8(data, p):
    """MemoryPack Utf8String。返回 (str|None, newpos)。"""
    if p + 4 > len(data):
        return None, p
    n = struct.unpack('<i', data[p:p + 4])[0]
    if n <= 0 or n > 4096 or p + 4 + n > len(data):
        return None, p
    raw = data[p + 4:p + 4 + n]
    try:
        s = raw.decode('utf-8')
    except UnicodeDecodeError:
        return None, p
    if not all(32 <= ord(c) < 127 for c in s):
        return None, p
    return s, p + 4 + n


# 匹配三种已知字符串形态
RE_ANIM = re.compile(r'^[a-z][a-z0-9_]{2,40}$')
RE_VOICE = re.compile(r'^Locale_[A-Za-z]+:[A-Za-z0-9_]+$')
RE_REACT = re.compile(r'^(c\d{3}(?:_\d{2})?|smol_[a-z0-9]+)_Lobby_[A-Za-z0-9_]+$')


def parse_table(data):
    """扫描整个表，提取记录。

    记录结构（实测）：
      固定前缀（~40 字节，含 group_id 等字段）
      [i32 len][utf8]  skinName        如 expression_0（表情/皮肤集名，非动画名）
      [i32 len][utf8]  voiceLocaleKey  如 Locale_Character:10_speech_01
      [i32 len][utf8]  reactionId      如 c010_Lobby_Touch_1
      尾部（~49 字节）

    group_id 位于字符串区之前，通过回溯固定偏移定位。
    同一 (reactionId) 在多份 group 里重复出现，调用方需按 group 去重。

    Returns:
        list of dict: {offset, group_id, reaction_id, character_id,
                       skin, voice_key, animation}
        animation 由反应类型推导：touch/action 型为 'action'，其它为 'idle'
    """
    results = []
    n_total = len(data)
    i = 4
    while i < n_total - 12:
        s1, p1 = read_utf8(data, i)
        if s1 is None or not RE_ANIM.match(s1):
            i += 1
            continue
        s2, p2 = read_utf8(data, p1 + 4)
        if s2 is None or not RE_VOICE.match(s2):
            i += 1
            continue
        s3, p3 = read_utf8(data, p2 + 4)
        if s3 is None:
            i += 1
            continue
        m = RE_REACT.match(s3)
        if not m:
            i += 1
            continue

        char_id = m.group(1)

        # 结构化定位 group_id（审计确认的布局）：
        #   记录 = [u8 0x0D][9 × u32 header 剩余] + 3 个字符串 + [2 × f32 tail]
        #   header_start = 字符串起点 - 41
        #   gid 在 header_start + 29（= 1 + 7*4）
        group_id = -1
        header_start = i - 41
        if header_start >= 4 and data[header_start] == 0x0D:
            gid_off = header_start + 29
            if gid_off + 4 <= len(data):
                group_id = struct.unpack('<I', data[gid_off:gid_off + 4])[0]
                if group_id > 64:
                    group_id = -1
        if group_id < 0 and i >= 12:
            # 兜底：沿用 i-12 偏移（实测与 header+29 等价）
            v = struct.unpack('<I', data[i - 12:i - 8])[0]
            if 0 <= v <= 64:
                group_id = v

        results.append({
            'offset': i,
            'group_id': group_id,
            'reaction_id': s3,
            'character_id': char_id,
            'skin': s1,
            'voice_key': s2,
        })
        i = p3

    return results


def classify_reaction(reaction_id):
    """把 reactionId 拆成 (kind, index, love)。

    c010_Lobby_Touch_1        -> ('touch', 1, False)
    c010_Lobby_Touch_Love_1   -> ('touch', 1, True)
    c010_Lobby_Stay_1         -> ('stay', 1, False)
    c010_Lobby_Stay_Love_1    -> ('stay', 1, True)
    """
    tail = reaction_id.split('_Lobby_', 1)[-1]
    love = 'Love' in tail
    m = re.search(r'(Touch|Stay)_(?:Love_)?(\d+)$', tail)
    if not m:
        return tail.lower(), 0, love
    return m.group(1).lower(), int(m.group(2)), love


# 反应类型 → 默认动画名（当表内 skin 字段不可用时兜底）
# 实测：CharacterReactionTable 的 skin 字段（expression_0/expression_1）
# 就是【要播放的动画名】，在 skel 的 animations 列表里确实存在。
# 所以优先用表内值，不依赖此映射。
FALLBACK_ANIMATION = {
    'touch': 'expression_0',
    'stay': 'idle',
}


def build_reaction_map(records, prefer_group=0, meta_lookup=None):
    """按角色聚合。

    ★ P0 修复（审计指出）：去重维度必须是 (reaction_id, skin)，不是 (reaction_id)。
      同一 reaction_id 在同一 group 内可能有 skin=expression_0 和 skin=expression_1
      两条记录，代表同一反应的【两个动画变体】（游戏按条件/随机播放）。
      旧实现把它们当重复数据丢弃，导致 44 条 expression_1 全部丢失。

    聚合后的结构：同一 (reaction_id, index) 下有多个 variants。

    Returns:
        {char_id: {kind: [
            {index, reaction_id, variants:[{animation, animation_exists,
                                           fallback_from?, group_id}],
             voice_key, group_id}
        ]}}
    """
    # 去重维度：(reaction_id, skin)。同一条记录在多个 group 里重复时，
    # 保留最接近 prefer_group 的那条（用于取 voice_key 等标量字段）。
    # 注意：variant 收集阶段【不】按 group 过滤，因为同一反应在不同 group
    # 可能提供不同动画变体，全部保留才能对齐游戏行为。
    dedup = {}
    for r in records:
        key = (r['reaction_id'], r['skin'])
        cur = dedup.get(key)
        if cur is None:
            dedup[key] = r
            continue
        if abs(r['group_id'] - prefer_group) < abs(cur['group_id'] - prefer_group):
            dedup[key] = r

    # 先按 (char, kind, index) 归组，收 variants
    grouped = {}
    for r in dedup.values():
        cid = r['character_id']
        kind, idx, love = classify_reaction(r['reaction_id'])
        bkey = kind + ('_love' if love else '')

        anim = r['skin']               # 表内 skin 字段实测就是动画名
        anim_exists = None
        fallback_from = None
        if meta_lookup is not None:
            meta = meta_lookup.get(cid)
            if meta:
                anims = meta.get('animations') or []
                anim_exists = anim in anims
                if not anim_exists:
                    # 该角色确实无此动画时才降级，并留痕
                    for cand in ('expression_0', 'idle', 'action'):
                        if cand in anims:
                            fallback_from = anim
                            anim = cand
                            break
                    else:
                        # 三个候选都没有：保持原值，标记 false
                        fallback_from = None
            # meta 缺失（该角色无 fb 资源）：anim_exists 保持 None，不降级也不丢弃

        slot = grouped.setdefault(cid, {}).setdefault(bkey, {})
        ent = slot.setdefault(idx, {
            'index': idx,
            'reaction_id': r['reaction_id'],
            'voice_key': r['voice_key'],
            'group_id': r['group_id'],
            'variants': [],
        })
        # 变体去重键 = (animation, fallback_from)。
        # 注意不能只用 animation：降级后的 expression_1→expression_0 必须与
        # 原生 expression_0 区分，否则会被当作重复丢弃，丢失「该反应曾有
        # expression_1 变体但客户端无此动画」这一事实。
        vkey = (anim, fallback_from)
        if not any((v['animation'], v.get('fallback_from')) == vkey
                   for v in ent['variants']):
            v = {
                'animation': anim,
                'animation_exists': anim_exists,
                'group_id': r['group_id'],
                'unity_field_skin': r['skin'],
            }
            if fallback_from:
                v['fallback_from'] = fallback_from
            ent['variants'].append(v)

    # 整理成列表并排序
    out = {}
    for cid, kinds in grouped.items():
        out[cid] = {}
        for bkey, idxmap in kinds.items():
            out[cid][bkey] = [idxmap[i] for i in sorted(idxmap)]
            for ent in out[cid][bkey]:
                ent['variants'].sort(key=lambda v: v['animation'])
    return out
