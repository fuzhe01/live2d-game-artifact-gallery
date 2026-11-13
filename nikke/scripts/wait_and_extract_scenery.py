"""等 NIKKE 退出后自动提取纯场景背景（472 张）并重建 manifest。

store.cdb 在游戏运行期间被独占锁定，无法读取。本脚本轮询进程，
检测到 nikke 退出后立即执行提取 + 重建，然后退出。

用法：
  python wait_and_extract_scenery.py          # 等待并提取
  python wait_and_extract_scenery.py --force  # 不等，立刻跑（游戏已关时）

完成后输出统计。
"""
import os
import subprocess
import sys
import time

TOOL = os.path.dirname(os.path.abspath(__file__))
EXTRACT = os.path.join(TOOL, 'extract_all_bg.py')
BUILD = os.path.join(TOOL, 'build_gallery_manifest.py')
PY = r'C:\Python315\python.exe'


def nikke_running():
    """检测 nikke 进程是否在跑。"""
    try:
        out = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq nikke.exe', '/NH'],
            capture_output=True, text=True, timeout=15)
        return 'nikke.exe' in (out.stdout or '').lower()
    except Exception:
        return False


def cdb_readable(pak='dp'):
    p = os.path.join(r'D:\nikke\Unity\com_proximabeta_NIKKE\com.shiftup.patch',
                     pak, 'chunk', 'store.cdb')
    if not os.path.exists(p):
        return False
    try:
        f = open(p, 'rb')
        f.read(4)
        f.close()
        return True
    except Exception:
        return False


def main():
    force = '--force' in sys.argv
    if not force:
        print('等待 NIKKE 退出（每 10 秒检查一次）...')
        waited = 0
        while nikke_running() or not cdb_readable('dp'):
            time.sleep(10)
            waited += 10
            if waited % 60 == 0:
                print('  已等待 %d 分钟；nikke=%s cdb可读=%s' % (
                    waited // 60, nikke_running(), cdb_readable('dp')))
            if waited > 6 * 3600:
                print('❌ 等待超 6 小时，放弃')
                return 1
        print('✅ 游戏已退出，store.cdb 可读，开始提取')

    if not cdb_readable('dp'):
        print('❌ store.cdb 仍不可读（游戏可能还在运行），请关闭游戏后重试')
        return 1

    print()
    print('=== 提取（跳过已存在的 1237 张，只补纯场景）===')
    r = subprocess.run([PY, '-u', EXTRACT], timeout=3600)
    if r.returncode != 0:
        print('❌ 提取失败 rc=%d' % r.returncode)
        return r.returncode

    print()
    print('=== 重建 manifest ===')
    r2 = subprocess.run([PY, '-u', BUILD], timeout=1800)
    return r2.returncode


if __name__ == '__main__':
    sys.exit(main())