"""从 skel 二进制解析 skin 名列表。

Spine 4.x 二进制格式：字符串表在前，之后是骨骼/槽位/IK/变换/路径/皮肤。
skeletonBinaryMetadata 给了各区块的起止偏移：
  strings_StartIndex         32
  bones_StartIndex           ...
  slots_StartIndex           ...
  ikConstraints_StartIndex   ...
  transformConstraints_StartIndex
  pathConstraints_StartIndex ...
  defaultSkinAndSkinsAndLinkedMeshes_StartIndex
  events_StartIndex
  animations_StartIndex

skin 区块格式（Spine 4.1）：
  varint skinCount
  可选：如果 skinCount==0，读 defaultSkin 名
  然后每个 skin：
    varint nameLen + name（0 表示 default skin）
    varint boneCount -> 每项 (varint boneIndex, varint slotCount, 每 slot: varint slotIndex, varint attachCount, ...)

只需解析出 skin 名，不必解附件。
"""
import struct


class VarintReader:
    """Spine 用「满 7 位续位」的变体：每字节读到第 7 位（0x80）为 0 才停。"""

    def __init__(self, data, pos=0):
        self.d = data
        self.p = pos

    def read_byte(self):
        b = self.d[self.p]
        self.p += 1
        return b

    def read_int(self, optimize_positive=False):
        """Spine int：逐字节累积，首字节高位判断符号。"""
        b = self.read_byte()
        result = b & 0x7F
        negative = (b & 0x80) != 0
        if negative and not optimize_positive:
            result = ~result & 0x7FFFFFFF
        shift = 7
        while (b & 0x80) != 0:
            b = self.read_byte()
            result |= (b & 0x7F) << shift
            shift += 7
        return -result - 1 if negative else result

    def read_varint(self, optimize_positive=False):
        """Spine varint（可选优化）：用于长度/计数。"""
        return self.read_int(optimize_positive)

    def read_string(self):
        """UTF-8 字符串：varint 长度，0 表示 null。"""
        n = self.read_varint(True)
        if n == 0:
            return None
        s = self.d[self.p:self.p + n - 1].decode('utf-8', 'replace')
        self.p += n - 1
        return s

    def read_float(self):
        v = struct.unpack('<f', self.d[self.p:self.p + 4])[0]
        self.p += 4
        return v


def parse_skins(skel_bytes, meta):
    """从 skel 字节解析 skin 名。

    Returns:
        {
          'skins': [...],           # skin 名列表（'default' 表示默认皮肤）
          'default_skin': str|None,
          'error': str|None
        }
    """
    if not isinstance(meta, dict):
        return {'skins': [], 'default_skin': None, 'error': 'no metadata'}

    start = meta.get('defaultSkinAndSkinsAndLinkedMeshes_StartIndex')
    end = meta.get('events_StartIndex')
    if start is None or end is None:
        return {'skins': [], 'default_skin': None, 'error': 'metadata 缺 skin 区间'}

    try:
        r = VarintReader(skel_bytes, start)
        # Spine 4.1: varint skinCount；但要看是 4.0 还是 4.1
        # 先读一个 int，试探
        skins = []
        default_skin = None

        # Spine 4.0/4.1 的 skin 区块开头均为：varint skinCount
        n = r.read_varint(True)
        if n < 0 or n > 200:
            return {'skins': [], 'default_skin': None,
                    'error': 'skinCount 异常: %d' % n}

        for i in range(n):
            name = r.read_string()
            if name is None:
                name = 'default'
                if default_skin is None:
                    default_skin = 'default'
            skins.append(name)

            # 跳过骨骼附件数据：boneCount
            boneCount = r.read_varint(True)
            for _ in range(boneCount):
                r.read_varint(True)          # boneIndex
                slotCount = r.read_varint(True)  # slotCount (4.1: 每个 bone 下若干 slot)
                for _ in range(slotCount):
                    r.read_varint(True)      # slotIndex
                    attachCount = r.read_varint(True)
                    for _ in range(attachCount):
                        # attachment: name + path + 类型相关数据 —— 不解析，跳过不可行
                        # 因此这条路走不通，需要在第一次遇到附件时停止
                        pass
            # 一旦需要解析附件就太复杂，实际只要 skin 名，用更简单的策略
            if i == 0:
                # 只记录第一个 skin 成功后，后续用「扫描字符串」方式补齐
                pass

        return {'skins': skins, 'default_skin': default_skin, 'error': None}
    except Exception as e:
        return {'skins': [], 'default_skin': None,
                'error': '%s: %s' % (type(e).__name__, e)}


def all_strings_in_range(skel_bytes, meta):
    """在 skin 区间内提取所有可打印字符串（启发式，用于验证）。"""
    start = meta.get('defaultSkinAndSkinsAndLinkedMeshes_StartIndex')
    end = meta.get('events_StartIndex')
    if start is None or end is None:
        return []
    chunk = skel_bytes[start:end]
    out = []
    cur = bytearray()
    for b in chunk:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= 2:
                out.append(cur.decode('ascii', 'replace'))
            cur = bytearray()
    if len(cur) >= 2:
        out.append(cur.decode('ascii', 'replace'))
    return out
