"""StaticData.pack 解密库：双层加密 → 内存中的表字典。

用法：
    import staticdata_lib as S
    tables = S.load_tables()               # {表名: bytes}
    raw = tables['CharacterReactionTable.mpk']

或按需加载单个表（省内存）：
    raw = S.load_table('CharacterReactionTable.mpk')
"""
import base64
import hashlib
import io
import os
import zipfile

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SMALL = base64.b64decode(
    'y8Icb/P1B/UFusrUmCiEH/DROMdh39bmZJqFEz4aagxoDivE33L4xlXkexQ2GDun0SCBItGpGIRlEwvtowDl2Q==')
SALT2 = base64.b64decode('BoyYXFlcWbIzIfzw9/yq34XiSg7iKeCQPQL4+tMII5o=')
SALT1 = base64.b64decode('XebMuvW+XhATlhrm+tQo9OEjkbPPSpY+EZWUmrEWNJQ=')

# 优先用 EpinelPS 缓存的那份；其次用客户端可能存在的路径
CANDIDATES = [
    r'D:\EpinelPS\out\cache\prdenv\153-cb882d4b30\staticdata\data\qa-261029-10b\578481\mpk\StaticData.pack',
]

_CACHE = {'inner': None, 'path': None}


def find_pack():
    for p in CANDIDATES:
        if os.path.exists(p):
            return p
    # 尝试环境变量
    p = os.environ.get('NIKKE_STATICDATA_PACK')
    if p and os.path.exists(p):
        return p
    raise FileNotFoundError('找不到 StaticData.pack，请设置 NIKKE_STATICDATA_PACK')


def _pbkdf2(pw, salt):
    return hashlib.pbkdf2_hmac('sha256', pw, salt, 10000, 32)


def _aes_cbc_decrypt(key, iv, data):
    c = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    d = c.decryptor()
    return d.update(data) + d.finalize()


def _ctr_transform(key, counter, data):
    key = key[:16]
    counter = bytearray(counter[:16])
    out = bytearray(len(data))
    pos = 0
    while pos < len(data):
        c = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
        e = c.encryptor()
        blk = e.update(bytes(counter)) + e.finalize()
        n = min(16, len(data) - pos)
        for i in range(n):
            out[pos + i] = data[pos + i] ^ blk[i]
        pos += n
        for k in range(15, -1, -1):
            counter[k] = (counter[k] + 1) & 0xFF
            if counter[k] != 0:
                break
    return bytes(out)


def _open_inner(path):
    raw = open(path, 'rb').read()
    k2 = _pbkdf2(SMALL, SALT2)
    dec = _aes_cbc_decrypt(k2[:16], k2[16:32], raw)
    outer = zipfile.ZipFile(io.BytesIO(dec))
    data = outer.read('data')
    k3 = _pbkdf2(SMALL, SALT1)
    inner_bytes = _ctr_transform(k3[:16], k3[16:32], data)
    return zipfile.ZipFile(io.BytesIO(inner_bytes))


def load_table(name):
    """解密并返回单个表的 bytes。"""
    if _CACHE['inner'] is None:
        path = find_pack()
        _CACHE['inner'] = _open_inner(path)
        _CACHE['path'] = path
    z = _CACHE['inner']
    if name not in z.namelist():
        raise KeyError('%s 不存在' % name)
    return z.read(name)


def load_tables(names=None):
    """批量加载。names=None 时加载全部（内存占用大，约 200MB+）。"""
    if _CACHE['inner'] is None:
        path = find_pack()
        _CACHE['inner'] = _open_inner(path)
        _CACHE['path'] = path
    z = _CACHE['inner']
    if names is None:
        names = [n for n in z.namelist() if n.endswith('.mpk')]
    return {n: z.read(n) for n in names if n in z.namelist()}


def list_tables():
    if _CACHE['inner'] is None:
        path = find_pack()
        _CACHE['inner'] = _open_inner(path)
        _CACHE['path'] = path
    return _CACHE['inner'].namelist()
