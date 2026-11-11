"""NIKKE store.cdb / catalog.ndb 解包库。

新版客户端分发格式三层：
  1. catalog.ndb (NKDB)  : AES-OFB + zlib 分段 → SQLite 目录库
  2. chunk/store.cdb     : 连续 zstd 帧（每帧一个 chunk，无加密）
  3. chunk 解压          : UnityFS 明文 Unity 包（UnityPy 可解）

关键表：
  files_chunktype(file_id, key, group_id)        bundle 文件名
  chunks(chunk_id, hash, original_size, compressed_size, pak_id, pak_offset)
  chunk_file_map(chunk_id, file_id, file_offset) chunk 在重组文件中的偏移
"""
import os
import io
import re
import struct
import zlib
import sqlite3

import zstandard as zstd

CBLB_HEADER = 0x100  # store.cdb 头大小（zstd 数据区起点，已实测验证）


def decrypt_nkdb(src, dst=None):
    """解密 catalog.ndb → SQLite 字节/文件。返回 bytes（dst 给定时同时写文件）。"""
    data = open(src, 'rb').read()
    if data[:4] != b'NKDB':
        raise ValueError('bad magic: %s' % data[:4])
    version = struct.unpack('>I', data[4:8])[0]
    if version != 1:
        raise ValueError('unsupported NKDB version %d' % version)
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
    warnings.filterwarnings('ignore')

    out = bytearray()
    for i in range(segment_count):
        start, end = offsets[i], offsets[i + 1]
        if end <= start:
            continue
        seg = data[start:end]
        iv = struct.pack('<II', i, start & 0xFFFFFFFF) + b'\x00' * 8
        cipher = Cipher(algorithms.AES(aes_key), modes.OFB(iv))
        dec = cipher.decryptor()
        plain = dec.update(seg) + dec.finalize()
        try:
            out += zlib.decompress(plain)
        except zlib.error:
            out += plain

    if dst:
        open(dst, 'wb').write(out)
    return bytes(out)


def open_catalog(pak_dir, work_dir):
    """解密 <pak_dir>/catalog.ndb → 临时 sqlite。返回 sqlite3 连接。"""
    src = os.path.join(pak_dir, 'catalog.ndb')
    dst = os.path.join(work_dir, os.path.basename(pak_dir) + '_catalog.db')
    if not os.path.exists(dst) or os.path.getsize(dst) < 4096:
        blob = decrypt_nkdb(src, dst)
        if blob[:15] != b'SQLite format 3':
            raise ValueError('解密结果不是 SQLite: %s' % blob[:16])
    con = sqlite3.connect(dst)
    return con


class CdbReader:
    """store.cdb 随机访问读取器：按 chunks 表的 pak_offset 直接定位 zstd 帧。"""

    def __init__(self, cdb_path):
        self.f = open(cdb_path, 'rb')
        self.dctx = zstd.ZstdDecompressor()

    def read_chunk(self, pak_offset, compressed_size):
        self.f.seek(CBLB_HEADER + pak_offset)
        blob = self.f.read(compressed_size)
        if blob[:4] != b'\x28\xb5\x2f\xfd':
            raise ValueError('非 zstd 帧 @pak_offset=%d: %s' % (pak_offset, blob[:8].hex()))
        return self.dctx.decompress(blob)

    def close(self):
        self.f.close()


def spine_files(con, pattern=None):
    """列出目录库中匹配模式的 bundle 文件。pattern 为 SQL LIKE 模式或 None=全部。
    返回 [(file_id, key)]。"""
    if pattern:
        rows = con.execute(
            "SELECT file_id, key FROM files_chunktype WHERE key LIKE ? ORDER BY file_id",
            (pattern,)).fetchall()
    else:
        rows = con.execute(
            "SELECT file_id, key FROM files_chunktype ORDER BY file_id").fetchall()
    return rows


def reassemble_file(con, reader, file_id):
    """按 chunk_file_map 把一个文件的所有 chunk 拼接还原。返回 bytes。"""
    rows = con.execute(
        "SELECT cm.file_offset, c.pak_offset, c.compressed_size, c.original_size "
        "FROM chunk_file_map cm JOIN chunks c ON c.chunk_id = cm.chunk_id "
        "WHERE cm.file_id = ? ORDER BY cm.file_offset", (file_id,)).fetchall()
    if not rows:
        raise ValueError('file %d 无 chunk' % file_id)
    buf = bytearray()
    for file_offset, pak_offset, comp, orig in rows:
        data = reader.read_chunk(pak_offset, comp)
        if len(data) != orig:
            raise ValueError('chunk 大小不匹配: 期望 %d 实得 %d' % (orig, len(data)))
        # file_offset 应等于当前 buf 长度（顺序拼接）
        if file_offset != len(buf):
            # 理论上按 file_offset 排序后应连续；不允许空洞
            raise ValueError('file_offset 断裂: 期望 %d 实际 %d' % (len(buf), file_offset))
        buf += data
    return bytes(buf)


# ── skel 命名归类（与 extract_local.py 保持一致）──────────────────────────

def classify(skel_stem):
    m = re.match(r'^(c\d{3})(?:_(\d{2}))?_skillcut$', skel_stem)
    if m:
        cid, skin = m.group(1), m.group(2)
        if skin and skin != '00':
            cid = f'{cid}_{skin}'
        return cid, 'skillcut', skel_stem

    m = re.match(r'^(c\d{3})(?:_(\d{2}))?(?:_(cover|aim))?$', skel_stem)
    if m:
        cid, skin, pose = m.group(1), m.group(2), m.group(3)
        if skin and skin != '00':
            cid = f'{cid}_{skin}'
        return cid, pose or 'fb', skel_stem

    m = re.match(r'^(.+?)_(cover|aim)_\d{2}$', skel_stem)
    if m:
        return m.group(1), m.group(2), skel_stem

    return skel_stem.lower(), 'fb', skel_stem


def out_dir_for(data_dir, cid, pose):
    base = os.path.join(data_dir, cid)
    return base if pose == 'fb' else os.path.join(base, pose)


def atlas_pages(atlas_bytes):
    pages = []
    for line in atlas_bytes.decode('utf-8', 'ignore').splitlines():
        line = line.strip().lstrip('\ufeff')
        if line.endswith('.png') and line not in pages:
            pages.append(line)
    return pages
