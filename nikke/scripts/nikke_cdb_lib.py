"""NIKKE 新版客户端（store.cdb / catalog.ndb）Spine 资源提取器。

═══════════════════════════════════════════════════════════════════════
格式解析（全部经过实测验证）
═══════════════════════════════════════════════════════════════════════

【catalog.ndb】NKDB 目录数据库
    Magic "NKDB" | u32BE version | 16B AES key（明文存放！）
    | u32BE segment_size | u32BE segment_count
    之后 segment_count+1 个大端 offset（4 或 5 字节）
    每段：AES-OFB(key, iv=index_LE+offset_LE+8*0) 解密 → zlib 解压
    拼接 = SQLite（3 张核心表）
        files_chunktype(file_id, key, group_id)   逻辑文件名
        chunks(chunk_id, hash, original_size, compressed_size, pak_id, pak_offset)
        chunk_file_map(chunk_id, file_id, file_offset)  文件内偏移

【store.cdb.idx】CIDX chunk 索引，明文
    头：Magic "CIDX" | u32 version | u32 count
    每条 28 字节：[16B md5][u32 file_off][u32 reserved][u32 comp_size]
    ★ 关键：file_off 是 uint32，超过 4 GB 会回绕。
      真实偏移 = 段号 × 2^32 + file_off（段号按 file_off 递减次数累加）

【store.cdb】CBLB 容器
    头：Magic "CBLB" | ... 前 0x100 字节
    数据：连续 zstd 帧（无加密）
    按 CIDX 给的绝对偏移 + 压缩大小直接定位解压

【解压产物】UnityFS 明文 Unity 包 → UnityPy 解析
    TextAsset(*.skel / *.atlas) + Texture2D(贴图)

═══════════════════════════════════════════════════════════════════════
命名规律（决定输出目录）
═══════════════════════════════════════════════════════════════════════
    spine/standing/c010/00_hd          → id=c010, pose=fb（立绘全身）
    spine/combat/c010/00/cover_hd      → id=c010, pose=cover（半身）
    spine/combat/c010/00/aim_hd        → id=c010, pose=aim（瞄准）
    其它 spine/*                       → 按 stem 归类
"""
import io
import os
import re
import struct
import sys
import zlib
from collections import Counter

import zstandard as zstd

# ── 常量 ────────────────────────────────────────────────────────────────
CBLB_HEADER = 0x100
U32 = 1 << 32
ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'


# ══════════════════════════════════════════════════════════════════════
#  catalog.ndb 解密
# ══════════════════════════════════════════════════════════════════════
def decrypt_nkdb_bytes(data):
    """NKDB → SQLite bytes。"""
    if data[:4] != b'NKDB':
        raise ValueError('NKDB magic 不匹配: %s' % data[:4])
    version = struct.unpack('>I', data[4:8])[0]
    if version != 1:
        raise ValueError('不支持的 NKDB 版本 %d' % version)

    aes_key = data[8:24]
    segment_size = struct.unpack('>I', data[24:28])[0]
    segment_count = struct.unpack('>I', data[28:32])[0]
    length_bytes = 5 if segment_size * segment_count > 0xFFFFFFFF else 4

    pos = 32
    offsets = []
    for _ in range(segment_count + 1):
        v = 0
        for b in data[pos:pos + length_bytes]:
            v = (v << 8) | b
        offsets.append(v)
        pos += length_bytes

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    import warnings
    warnings.filterwarnings('ignore', message='.*OFB.*')

    out = bytearray()
    for i in range(segment_count):
        start, end = offsets[i], offsets[i + 1]
        if end <= start:
            continue
        seg = data[start:end]
        iv = struct.pack('<II', i, start & 0xFFFFFFFF) + b'\x00' * 8
        dec = Cipher(algorithms.AES(aes_key), modes.OFB(iv)).decryptor()
        plain = dec.update(seg) + dec.finalize()
        try:
            out += zlib.decompress(plain)
        except zlib.error:
            out += plain
    return bytes(out)


def open_catalog(pak_dir, work_dir):
    """解密 catalog.ndb 并返回 sqlite3 连接（带缓存）。"""
    import sqlite3
    src = os.path.join(pak_dir, 'catalog.ndb')
    tag = os.path.basename(os.path.normpath(pak_dir))
    dst = os.path.join(work_dir, tag + '_catalog.db')
    if not os.path.exists(dst) or os.path.getsize(dst) < 65536:
        blob = decrypt_nkdb_bytes(open(src, 'rb').read())
        if blob[:15] != b'SQLite format 3':
            raise ValueError('解密结果非 SQLite')
        open(dst, 'wb').write(blob)
    return sqlite3.connect(dst)


# ══════════════════════════════════════════════════════════════════════
#  CIDX 索引（含 uint32 回绕修复）
# ══════════════════════════════════════════════════════════════════════
def load_cidx(idx_path):
    """读 CIDX → {md5_bytes: (absolute_offset, comp_size)}。

    file_off 是 uint32，超大文件会回绕。按出现递减即判定进入下一段，
    绝对偏移 = 段号 * 2^32 + file_off。
    """
    data = open(idx_path, 'rb').read()
    if data[:4] != b'CIDX':
        raise ValueError('CIDX magic 不匹配: %s' % data[:4])
    count = struct.unpack('<I', data[8:12])[0]
    result = {}
    seg = 0
    prev = -1
    for i in range(count):
        o = 12 + i * 28
        if o + 28 > len(data):
            break
        md5 = data[o:o + 16]
        file_off, _reserved, comp = struct.unpack('<III', data[o + 16:o + 28])
        if file_off < prev:
            seg += 1
        prev = file_off
        abs_off = seg * U32 + file_off
        result[md5] = (abs_off, comp)
    return result


class CdbReader:
    """按 chunks.hash 查 CIDX 得绝对偏移，读 zstd 帧解压。"""

    def __init__(self, cdb_path, cidx_map):
        self.f = open(cdb_path, 'rb')
        self.map = cidx_map
        self.dctx = zstd.ZstdDecompressor()
        self.cache_hits = 0
        self.cache_miss = 0

    def chunk_by_hash(self, h, expect_size=None):
        ent = self.map.get(h)
        if ent is None:
            self.cache_miss += 1
            raise KeyError('chunk hash %s 不在 CIDX 中' % h.hex())
        self.cache_hits += 1
        abs_off, comp = ent
        self.f.seek(abs_off)
        blob = self.f.read(comp)
        if blob[:4] != ZSTD_MAGIC:
            raise ValueError('非 zstd 帧 @%d' % abs_off)
        data = self.dctx.decompress(blob)
        if expect_size is not None and len(data) != expect_size:
            raise ValueError('chunk 大小 %d != %d' % (len(data), expect_size))
        return data

    def close(self):
        self.f.close()


def reassemble(con, reader, file_id):
    """按 chunk_file_map 拼接出完整文件。"""
    rows = con.execute(
        'SELECT cm.file_offset, c.hash, c.original_size '
        'FROM chunk_file_map cm JOIN chunks c ON c.chunk_id = cm.chunk_id '
        'WHERE cm.file_id = ? ORDER BY cm.file_offset', (file_id,)).fetchall()
    if not rows:
        raise ValueError('file %d 无 chunk' % file_id)
    buf = bytearray()
    for foff, h, orig in rows:
        data = reader.chunk_by_hash(h, orig)
        if foff != len(buf):
            raise ValueError('偏移断裂 %d != %d' % (foff, len(buf)))
        buf += data
    return bytes(buf)


# ══════════════════════════════════════════════════════════════════════
#  Spine 归类（与 extract_local.py / Nikke-db 命名对齐）
# ══════════════════════════════════════════════════════════════════════
def classify_key(key):
    """从 bundle key 推出 (id, pose, stem_hint)。

    key 例：
      spinestandingcharactergroup(hd)_assets_spine/standing/c010/00_hd_<hash>.bundle
      spinecombatcharactergroup(hd)_assets_spine/combat/c010/00/cover_hd_<hash>.bundle
      spineeventscenesgroup_assets_spine/events/eventscene_xxx_<hash>.bundle
    """
    m = re.search(r'_assets_spine/standing/(c\d{3})/(\d{2})', key)
    if m:
        cid, skin = m.group(1), m.group(2)
        return (cid if skin == '00' else '%s_%s' % (cid, skin)), 'fb'

    m = re.search(r'_assets_spine/combat/(c\d{3})/(\d{2})/(aim|cover)', key)
    if m:
        cid, skin, pose = m.group(1), m.group(2), m.group(3)
        return (cid if skin == '00' else '%s_%s' % (cid, skin)), pose

    m = re.search(r'_assets_spine/events/(.+?)_[0-9a-f]{32}\.bundle$', key)
    if m:
        return m.group(1).lower(), 'fb'

    m = re.search(r'_assets_spine/mini/(c\d{3})/', key)
    if m:
        return m.group(1) + '_mini', 'fb'

    m = re.search(r'_assets_spine/smol/(c\d{3})/', key)
    if m:
        return m.group(1) + '_smol', 'fb'

    m = re.search(r'_assets_spine/monster/(.+?)_[0-9a-f]{32}\.bundle$', key)
    if m:
        return 'monster_' + m.group(1).lower(), 'fb'

    m = re.search(r'_assets_spine/misc/(.+?)_[0-9a-f]{32}\.bundle$', key)
    if m:
        return m.group(1).lower(), 'fb'

    # 通用兜底：取 spine 后第一段
    m = re.search(r'_assets_spine/(.+?)/', key)
    if m:
        return re.sub(r'[^a-z0-9_]+', '_', m.group(1).lower()), 'fb'
    return None, None


def atlas_pages(atlas_bytes):
    pages = []
    for line in atlas_bytes.decode('utf-8', 'ignore').splitlines():
        line = line.strip().lstrip('\ufeff')
        if line.endswith('.png') and line not in pages:
            pages.append(line)
    return pages


def spine_version(skel_bytes):
    m = re.search(rb'([34]\.[0-9]+\.[0-9]+)', skel_bytes[:64])
    if m:
        full = m.group(1).decode()
        return '.'.join(full.split('.')[:2])
    return None


# ══════════════════════════════════════════════════════════════════════
#  UnityPy 提取
# ══════════════════════════════════════════════════════════════════════
def extract_spine_from_bundle(bundle_bytes, out_dir):
    """把 bundle 里的 skel/atlas/texture 写到 out_dir。返回统计 dict。"""
    import UnityPy

    env = UnityPy.load(bundle_bytes)
    text_assets = {}
    textures = {}
    for o in env.objects:
        t = o.type.name
        try:
            if t == 'TextAsset':
                d = o.read()
                raw = d.m_Script
                if isinstance(raw, str):
                    raw = raw.encode('utf-8', 'surrogateescape')
                text_assets[d.m_Name] = raw
            elif t == 'Texture2D':
                d = o.read()
                textures[d.m_Name] = d.image
        except Exception:
            pass

    if not text_assets:
        return None

    os.makedirs(out_dir, exist_ok=True)
    written = []

    # 写所有 skel / atlas
    for name, raw in text_assets.items():
        if name.endswith('.skel') or name.endswith('.atlas'):
            p = os.path.join(out_dir, name)
            open(p, 'wb').write(raw)
            written.append(name)

    # 按 atlas 引用的 png 名写贴图
    for name, raw in text_assets.items():
        if not name.endswith('.atlas'):
            continue
        for png in atlas_pages(raw):
            key = png[:-4] if png.endswith('.png') else png
            img = textures.get(key) or textures.get(png) or textures.get(name.replace('.atlas', ''))
            if img is not None:
                try:
                    img.save(os.path.join(out_dir, png))
                    written.append(png)
                except Exception:
                    pass

    # 兜底：如果 atlas 页一张都没写出来，把全部贴图存下来
    if not any(w.endswith('.png') for w in written):
        for tname, img in textures.items():
            p = os.path.join(out_dir, tname + '.png')
            try:
                img.save(p)
                written.append(tname + '.png')
            except Exception:
                pass

    skels = [w for w in written if w.endswith('.skel')]
    return {
        'skel': skels,
        'atlas': [w for w in written if w.endswith('.atlas')],
        'png': [w for w in written if w.endswith('.png')],
        'version': spine_version(text_assets[skels[0]]) if skels else None,
    }
