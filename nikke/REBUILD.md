# 资源重建指南（2026-11-19 清理后）

清理后画廊只剩 **17.4 MB**（代码 + 索引 + 清单），所有美术资产已删除。
本文档说明如何按需重建。

## 目录现状

```
nikke/
├── data/          仅剩索引 json（3.4 MB）
│   ├── _l2d_index.json      l2d 资源索引
│   ├── voiceIndex.json      语音索引（角色 → 类型 → 片段）
│   ├── subtitles.json       语音字幕
│   └── reactions.json       角色表情反应
├── scripts/       29 个构建脚本（全部保留）
└── viewer/        前端（index.html + 各 js）
```

## 三类资源，三条重建路径

### A. 角色 Spine 立绘与骨骼动画

**来源**：CDN `nikke-db-legacy.pages.dev/l2d`（可直接访问，无需游戏客户端）

```powershell
python nikke/scripts/fetch_nikke.py
```

产出 `data/cXXX/`、`data/cXXX_01/`（皮肤变体），每个目录含
`.skel`（骨骼）、`.atlas`（图集描述）、`.png`（贴图）、`cover/`、`aim/`。

体积参考：532 个目录约 4.67 GB。

### B. 语音

**来源**：CDN `ja_audio_index.json` + 音频文件

```powershell
python nikke/scripts/export_voice.py
python nikke/scripts/build_voice_index.py
```

产出 `data/voice/*.wav`（12,938 个，约 4.51 GB）与 `data/voiceIndex.json`。

> `voiceIndex.json` 已保留，所以只跑 `export_voice.py` 也能直接用。

### C. Gallery 插画与活动场景图

**来源**：游戏客户端的 `com.shiftup.patch` 包（需先装好 NIKKE 客户端）

```powershell
# 1) 确保客户端资源在：
#    <游戏目录>\Unity\com_proximabeta_NIKKE\com.shiftup.patch\{dp,core,ja,fd,...}\chunk\store.cdb
# 2) 提取（脚本会自行解密 catalog.ndb 并解包）
python nikke/scripts/extract_all_bg.py
# 3) 生成清单与分组
python nikke/scripts/build_gallery_manifest.py
```

产出 `data/gallery/*.webp`（1,709 张，约 1.00 GB）与 `data/gallery/manifest.json`。

> 场景背景（`scenariobackground(hd)` 里的非 eventscene 部分）也在这一步提取，
> 归入 `scenery` 组。

### D. 字幕与表情反应

**来源**：GitHub `PlumButa/Nikke-L2D-Desktop-Viewer`

```powershell
python nikke/scripts/fetch_subtitles.py
python nikke/scripts/build_subtitles.py
python nikke/scripts/build_reactions.py
```

### E. 图标（列表封面）

```powershell
python nikke/scripts/render_icons.py
```

## 关键技术备忘

### store.cdb 的偏移不能信

catalog 里的 `pak_offset` 对 >4GB 的包不准（增量补丁导致 chunk 去重/覆盖：
catalog 描述 16.85 GB，实际文件 13.47 GB）。必须用 `store.cdb.idx`：

```
CIDX 每条 28B = md5(16) + u32_1(段内偏移) + u32_2(段号) + u32_3(压缩大小)
真实文件偏移 = u32_2 * 2^32 + u32_1
```

实测：回绕 3 次，真实偏移范围 256 ~ 14,461,284,434，与文件大小完全吻合；
段号 `u32_2` 与回绕段 0/260,902 不一致。

`cdb_lib.py` 已实现该逻辑（`CidxIndex` / `Cdb.read_chunk`）。

### .nab 是 UnityFS bundle

语言包里的 2,190 个 `.nab` 每个都是 UnityFS bundle（不是裸 DLL），
里面装着 sprite 表和本地化资源。

### TEA 算法 cryptography 没有

NIKKE 的 CDN manifest / path table 用 TEA 加密，
`cryptography.hazmat.primitives.ciphers.algorithms` 与 `decrepit` 模块都没有 TEA。
自带实现见 `nikke-toolchain/10-tool/tea.py`。

### 语言包缺失会导致大厅无 UI

客户端的 `en`/`ko` 语言包从未被下载（启动器 CDN 只提供游戏本体 584 MB，
语言包要靠服务端 `/v1/resourcehosts2` 返回补丁源后由游戏自行下载）。
私服该接口返回空对象 → 客户端读不到 sprite 表 →
`FileNotFoundException: Localized sprite table not found: event_XXXXXX`
→ Home 视图 Prepare 失败 → 有背景无 UI。

绕开：把 `sail_api_impl64.dll` 里的硬编码 `"English"` 改成读取
loginData 的 `"language"` 字段，切到本地有数据的语言包（ja / fd）。

游戏内置映射：`en.English` / `ja.Japanese` / `ko.Korean` / `fr.French` / `de.German`。

## 清理记录

`nikke-toolchain/01-audit/gallery_cleanup_manifest.json` 记录了被删目录的
路径、文件数与体积，可据此按需重建。
