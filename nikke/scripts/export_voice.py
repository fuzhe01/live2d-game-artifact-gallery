"""导出互动语音为 WAV。

只导出 reactions.json 里实际用到的语音（211 个角色的 Lobby_Touch / Lobby_Stay），
避免导出全部 14 万个 AudioClip。

产出：nikke/data/voice/<clipName>.wav
"""
import json
import os
import sys
import time

sys.path.insert(0, r'D:\live2d-game-artifact-gallery\nikke\scripts')
import nikke_cdb_lib as L

WORK = r'D:\live2d-game-artifact-gallery\nikke\cache\cdb'
PATCH = r'D:\nikke\Unity\com_proximabeta_NIKKE\com.shiftup.patch'
INDEX = r'D:\live2d-game-artifact-gallery\nikke\cache\ja_audio_index.json'
DATA = r'D:\live2d-game-artifact-gallery\nikke\data'
VOICE_DIR = os.path.join(DATA, 'voice')

import UnityPy


def main():
    print('=== 载入索引 ===')
    index = json.load(open(INDEX, encoding='utf-8'))
    print('  总 AudioClip: %d' % len(index))

    print('=== 载入 reactions ===')
    rpath = os.path.join(DATA, 'reactions.json')
    reactions = json.load(open(rpath, encoding='utf-8'))
    chars = reactions['characters']
    print('  角色数: %d' % len(chars))

    # 收集需要的 AudioClip 名
    # 反应表 voice_key = Locale_Character:10_speech_01
    # 对应 AudioClip: c010_Lobby_Touch_1
    # 命名映射规则：
    #   speech_01..03      -> Lobby_Touch_1..3
    #   speech_04..06      -> Lobby_Touch_Love_1..3
    #   stay_01            -> Lobby_Stay_1
    #   stay_02            -> Lobby_Stay_Love_1
    need = {}   # clipName -> set(reaction_ids using it)
    mapping_stats = {'mapped': 0, 'missing': 0}
    missing_detail = []

    for cid, d in chars.items():
        for kind, items in d.items():
            for it in items:
                rid = it['reaction_id']
                # 直接用 reaction_id 当 clip 名（实测就是这个规律）
                clip = rid
                if clip in index:
                    need.setdefault(clip, set()).add(rid)
                    mapping_stats['mapped'] += 1
                else:
                    mapping_stats['missing'] += 1
                    if len(missing_detail) < 20:
                        missing_detail.append((cid, rid))

    print()
    print('=== 语音映射 ===')
    print('  需要的 AudioClip(唯一): %d' % len(need))
    print('  匹配成功: %d 条反应' % mapping_stats['mapped'])
    print('  未找到  : %d 条反应' % mapping_stats['missing'])
    if missing_detail:
        print('  未找到样例:')
        for cid, rid in missing_detail[:10]:
            print('    %s -> %s' % (cid, rid))

    # 按 bundle 分组，减少重复读取
    by_bundle = {}
    for clip in need:
        e = index[clip]
        by_bundle.setdefault(e['file_id'], []).append(clip)
    print('  涉及 bundle: %d 个' % len(by_bundle))

    print()
    print('=== 开始导出 ===')
    os.makedirs(VOICE_DIR, exist_ok=True)
    con = L.open_catalog(os.path.join(PATCH, 'ja'), WORK)
    cidx = L.load_cidx(os.path.join(PATCH, 'ja', 'chunk', 'store.cdb.idx'))
    reader = L.CdbReader(os.path.join(PATCH, 'ja', 'chunk', 'store.cdb'), cidx)

    # file_id -> key 查表
    fid2key = {}
    for fid in by_bundle:
        r = con.execute('SELECT key FROM files_chunktype WHERE file_id=?', (fid,)).fetchone()
        if r:
            fid2key[fid] = r[0]

    t0 = time.time()
    ok = 0
    fail = 0
    for n, (fid, clips) in enumerate(by_bundle.items(), 1):
        try:
            b = L.reassemble(con, reader, fid)
            env = UnityPy.load(b)
            want = set(clips)
            for o in env.objects:
                if o.type.name != 'AudioClip':
                    continue
                d = o.read()
                nm = getattr(d, 'm_Name', '')
                if nm not in want:
                    continue
                try:
                    samples = d.samples
                except Exception:
                    fail += 1
                    continue
                if isinstance(samples, dict):
                    # 正常情况下 UnityPy 对单文件 AudioClip 返回 {name.wav: bytes}（len=1）。
                    # 若出现多项，说明该 clip 由多段组成，必须全部写出，否则静默丢数据。
                    if len(samples) > 1:
                        print('  WARN %s 含 %d 个 sample 文件，全部写出' % (nm, len(samples)))
                    for si, (fname, data) in enumerate(samples.items()):
                        if len(samples) == 1:
                            p = os.path.join(VOICE_DIR, nm + '.wav')
                        else:
                            base = nm + '_' + str(si)
                            p = os.path.join(VOICE_DIR, base + '.wav')
                        open(p, 'wb').write(data)
                        if len(data) > 44 and data[:4] == b'RIFF':
                            ok += 1
                        else:
                            fail += 1
            if n % 50 == 0:
                print('  [%d/%d] 已导出 %d, 失败 %d, %.1fs' % (
                    n, len(by_bundle), ok, fail, time.time() - t0), flush=True)
        except Exception as e:
            fail += len(clips)
            if n <= 5:
                print('  bundle %d FAIL: %s' % (fid, e))

    reader.close()
    con.close()

    print()
    print('=' * 70)
    print('语音导出完成 %.1fs' % (time.time() - t0))
    print('  成功 WAV: %d' % ok)
    print('  失败    : %d' % fail)

    total_sz = sum(os.path.getsize(os.path.join(VOICE_DIR, f))
                   for f in os.listdir(VOICE_DIR) if f.endswith('.wav'))
    print('  目录大小: %.1f MB' % (total_sz / 1048576))

    # 抽样验证
    print()
    print('=== 抽样检查 ===')
    for cid in ('c010', 'c011', 'c101'):
        for suffix in ('Lobby_Touch_1', 'Lobby_Stay_1'):
            name = '%s_%s.wav' % (cid, suffix)
            p = os.path.join(VOICE_DIR, name)
            if os.path.exists(p):
                sz = os.path.getsize(p)
                head = open(p, 'rb').read(4)
                print('  %-32s %8d B  %s' % (name, sz, 'RIFF' if head == b'RIFF' else head.hex()))
            else:
                print('  %-32s 缺失' % name)


if __name__ == '__main__':
    main()
