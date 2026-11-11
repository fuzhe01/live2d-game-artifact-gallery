"""全量扫描 ja pak 的 .nab，建立 AudioClip 名字 → (file_id, key) 索引。

.jab/.nab = 明文 UnityFS bundle，含多个 AudioClip。
扫描 2190 个文件，提取所有 AudioClip 名。
"""
import json
import os
import sys
import time

sys.path.insert(0, r'D:\live2d-game-artifact-gallery\nikke\scripts')
import nikke_cdb_lib as L

WORK = r'D:\live2d-game-artifact-gallery\nikke\cache\cdb'
PATCH = r'D:\nikke\Unity\com_proximabeta_NIKKE\com.shiftup.patch'
OUT = r'D:\live2d-game-artifact-gallery\nikke\cache\ja_audio_index.json'

import UnityPy


def main():
    con = L.open_catalog(os.path.join(PATCH, 'ja'), WORK)
    cidx = L.load_cidx(os.path.join(PATCH, 'ja', 'chunk', 'store.cdb.idx'))
    reader = L.CdbReader(os.path.join(PATCH, 'ja', 'chunk', 'store.cdb'), cidx)

    rows = con.execute(
        "SELECT file_id, key FROM files_chunktype ORDER BY file_id").fetchall()
    print('ja pak 文件总数: %d' % len(rows))

    index = {}   # clipName -> {file_id, key, channels, freq, length}
    t0 = time.time()
    fail = 0
    empty = 0
    fail_keys = []

    for i, (fid, key) in enumerate(rows, 1):
        try:
            b = L.reassemble(con, reader, fid)
            env = UnityPy.load(b)
            n_clip = 0
            for o in env.objects:
                if o.type.name != 'AudioClip':
                    continue
                d = o.read()
                nm = getattr(d, 'm_Name', '')
                if not nm:
                    continue
                index[nm] = {
                    'file_id': fid,
                    'key': key,
                    'channels': getattr(d, 'm_Channels', 0),
                    'frequency': getattr(d, 'm_Frequency', 0),
                    'bits': getattr(d, 'm_BitsPerSample', 0),
                    'length': round(float(getattr(d, 'm_Length', 0) or 0), 3),
                }
                n_clip += 1
            if n_clip == 0:
                empty += 1
        except Exception as e:
            fail += 1
            if len(fail_keys) < 20:
                fail_keys.append((key, '%s: %s' % (type(e).__name__, e)))
        if i % 200 == 0:
            print('  [%d/%d] 已索引 %d clips, 失败 %d, 空 %d, %.1fs' % (
                i, len(rows), len(index), fail, empty, time.time() - t0), flush=True)

    reader.close()
    con.close()

    print()
    print('=' * 70)
    print('扫描完成 %.1fs' % (time.time() - t0))
    print('  AudioClip 总数: %d' % len(index))
    print('  失败 bundle : %d' % fail)
    print('  空 bundle   : %d' % empty)
    if fail_keys:
        print('  失败明细（前 20）:')
        for k, msg in fail_keys:
            print('    %s' % k[:60])
            print('      %s' % msg[:120])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(index, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    print('  索引已写出: %s (%d KB)' % (OUT, os.path.getsize(OUT) // 1024))

    # 检查 c010 的 Lobby 语音是否在索引里
    print()
    print('=== c010 的 Lobby 语音 ===')
    hits = [k for k in index if k.startswith('c010_Lobby_')]
    for h in sorted(hits):
        e = index[h]
        print('  %-32s %.2fs  %s' % (h, e['length'], e['key'][:40]))

    # 统计有多少角色有 Lobby_Touch
    chars_with_lobby = set()
    for k in index:
        if '_Lobby_Touch_' in k or '_Lobby_Stay_' in k:
            chars_with_lobby.add(k.split('_')[0])
    print()
    print('  含 Lobby 语音的角色数: %d' % len(chars_with_lobby))


if __name__ == '__main__':
    main()
