"""从游戏 store.cdb 提取 scenariobackground（剧情插画）并导出 PNG。

关键：catalog 的 pak_offset 因增量补丁不可靠，改用 CIDX 索引：
  CIDX 条目 = md5(16) + u32_1(段内偏移) + u32_2(段号) + u32_3(压缩大小)
  真实文件偏移 = u32_2 * 2^32 + u32_1
  （u32_1 含 0x100 头；u32_2 与回绕段完全一致，已实测 0/260902 不符）
"""
import os
import sqlite3
import struct
import sys

import zstandard as zstd

PATCH = r'D:\nikke\Unity\com_proximabeta_NIKKE\com.shiftup.patch'
WORK = r'D:\nikke-toolchain\_work'


class CidxIndex:
    """CIDX → hash 到真实偏移的索引。"""

    def __init__(self, pak):
        idx_path = os.path.join(PATCH, pak, 'chunk', 'store.cdb.idx')
        data = open(idx_path, 'rb').read()
        if data[:4] != b'CIDX':
            raise ValueError('bad CIDX magic')
        self.count = struct.unpack('<I', data[8:12])[0]
        self.map = {}
        for i in range(self.count):
            e = data[12 + i * 28: 12 + (i + 1) * 28]
            md5 = e[:16]
            u1, u2, u3 = struct.unpack('<III', e[16:28])
            self.map[md5] = (u2 * 0x100000000 + u1, u3)

    def locate(self, h):
        return self.map.get(bytes(h))


class Cdb:
    def __init__(self, pak):
        self.f = open(os.path.join(PATCH, pak, 'chunk', 'store.cdb'), 'rb')
        self.dctx = zstd.ZstdDecompressor()

    def read_chunk(self, real_off, comp):
        self.f.seek(real_off)
        blob = self.f.read(comp)
        if blob[:4] != b'\x28\xb5\x2f\xfd':
            raise ValueError('非 zstd @%d: %s' % (real_off, blob[:8].hex()))
        return self.dctx.decompress(blob)

    def close(self):
        self.f.close()


def open_catalog(pak):
    from importlib import util
    sys.path.insert(0, r'D:\live2d-game-artifact-gallery\nikke\scripts')
    import cdb_lib
    return cdb_lib.open_catalog(os.path.join(PATCH, pak), WORK)


def reassemble(con, cidx, cdb, file_id):
    """按 CIDX 重组一个文件，返回 bytes。"""
    rows = con.execute(
        "SELECT cm.file_offset, c.hash, c.compressed_size "
        "FROM chunk_file_map cm JOIN chunks c ON c.chunk_id=cm.chunk_id "
        "WHERE cm.file_id=? ORDER BY cm.file_offset", (file_id,)).fetchall()
    buf = bytearray()
    for foff, h, comp in rows:
        ent = cidx.locate(h)
        if not ent:
            raise ValueError('chunk 不在 CIDX: %s' % bytes(h).hex()[:16])
        real, csz = ent
        data = cdb.read_chunk(real, csz)
        if foff != len(buf):
            raise ValueError('file_offset 断裂: 期望 %d 实际 %d' % (len(buf), foff))
        buf += data
    return bytes(buf)


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else \
        'scenariobackground(hd)_assets_eventscene_absolute_01_1'
    outdir = r'D:\nikke-toolchain\_work\gallery_extract'
    os.makedirs(outdir, exist_ok=True)

    import UnityPy
    from collections import Counter

    # 跨包：chunk 可能在 dp 也可能在 core
    paks = ['dp', 'core']
    cidx = {p: CidxIndex(p) for p in paks}
    cdbs = {p: Cdb(p) for p in paks}
    cons = {p: open_catalog(p) for p in paks}
    for p in paks:
        print('%s CIDX 条目 %d' % (p, cidx[p].count))

    row = None
    for p in paks:
        row = cons[p].execute(
            "SELECT file_id, key FROM files_chunktype WHERE key LIKE ?",
            (target + '%',)).fetchone()
        if row:
            print('catalog 命中: pak=%s file_id=%d' % (p, row[0]))
            break
    if not row:
        print('❌ catalog 找不到 %s' % target)
        return
    if not row:
        return
    fid, key = row
    print('key=%s' % key)

    # 逐 chunk 跨包定位
    allrows = []
    for p in paks:
        rs = cons[p].execute(
            "SELECT cm.file_offset, c.hash, c.compressed_size "
            "FROM chunk_file_map cm JOIN chunks c ON c.chunk_id=cm.chunk_id "
            "WHERE cm.file_id=? ORDER BY cm.file_offset", (fid,)).fetchall()
        if rs:
            allrows = rs
            src = p
            break
    if not allrows:
        print('❌ 找不到 chunk 列表')
        return

    buf = bytearray()
    miss = []
    for foff, h, comp in allrows:
        ent = None
        use = None
        for p in paks:
            ent = cidx[p].locate(h)
            if ent:
                use = p
                break
        if not ent:
            miss.append(bytes(h).hex()[:16])
            continue
        real, csz = ent
        data = cdbs[use].read_chunk(real, csz)
        if foff != len(buf):
            print('  ⚠️ file_offset 断裂: 期望 %d 实际 %d（跳过缺失 chunk）' % (
                len(buf), foff))
        buf += data
    if miss:
        print('  ⚠️ %d 个 chunk 在所有包里都找不到: %s' % (len(miss), miss[:3]))

    data = bytes(buf)
    print('重组 %d B  头: %r' % (len(data), data[:16]))

    env = UnityPy.load(data)
    types = Counter(o.type.name for o in env.objects)
    print('对象: %s' % dict(types))

    saved = 0
    for o in env.objects:
        if o.type.name == 'Texture2D':
            d = o.read()
            img = d.image
            nm = getattr(d, 'm_Name', 'tex')
            p = os.path.join(outdir, '%s.png' % nm)
            img.save(p)
            print('  ✅ %s  %dx%d  %d B' % (
                os.path.basename(p), d.m_Width, d.m_Height, os.path.getsize(p)))
            saved += 1
    if not saved:
        print('  ⚠️ 无 Texture2D，对象列表:')
        for o in env.objects:
            try:
                d = o.read()
                print('    %-16s %s' % (o.type.name, getattr(d, 'm_Name', '?')))
            except Exception:
                print('    %-16s <err>' % o.type.name)

    for p in paks:
        cdbs[p].close()
        cons[p].close()


if __name__ == '__main__':
    main()