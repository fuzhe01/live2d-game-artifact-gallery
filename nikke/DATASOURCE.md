# NIKKE（胜利女神）数据源与工具说明

本文说明 NIKKE 美术资源（Spine 骨骼动画立绘/模型）的**数据来源**、**本地文件位置**、
以及**拆解 → 重组**工具链。所有脚本在 `nikke/scripts/`。

> NIKKE 的立绘/模型是 **Spine 骨骼动画**（`.skel` + `.atlas` + `.png`），**不是 Live2D**。
> 每套资源包含骨骼数据 `.skel`、图集描述 `.atlas`、贴图页 `.png`。

---

## 一、数据源（两条路，可二选一或互补）

### 路线 A：社区 Nikke-db（快速，无需游戏本体）

社区项目 [`Nikke-db`](https://github.com/Nikke-db/Nikke-db.github.io) 已把 Spine 资源
解包并托管在 CDN 上，直接按角色 id 下载即可。

| 用途 | 地址 |
|------|------|
| 角色索引 `l2d.json`（name/id/version） | `https://nikke-db-legacy.pages.dev/js/json/l2d.json` |
| 索引镜像 | `https://raw.githubusercontent.com/Nikke-db/Nikke-db.github.io/main/js/json/l2d.json` |
| Spine 资源根 | `https://nikke-db-legacy.pages.dev/l2d` |
| 渲染好的头像图标（列表封面用） | `https://nikke-db-legacy.pages.dev/images/sprite` |
| 资源镜像 | `https://raw.githubusercontent.com/Nikke-db/Nikke-db.github.io/main/l2d` |

配置位置：`nikke/scripts/config.py`

**资源 URL 规律**（三种姿态）：
```
fb   (立绘全身): {CDN}/l2d/{id}/{id}_00.skel | .atlas | .png
cover(半身):     {CDN}/l2d/{id}/cover/{id}_cover_00.skel | .atlas | .png
aim  (瞄准):     {CDN}/l2d/{id}/aim/{id}_aim_00.skel | .atlas | .png
头像图标:        {CDN}/images/sprite/si_{id}_00_s.png
```
- `.atlas` 是文本文件，里面列出引用的所有 `.png` 页，脚本据此逐个下载贴图。
- Cloudflare Pages（`pages.dev`）会对**缺少 UA / Referer 的请求返回 403**，
  抓取时必须伪装浏览器 UA + 带 `Referer: https://nikke-db-legacy.pages.dev/`。

### 路线 B：本地游戏客户端缓存（数据最全最新）

游戏客户端把资源以 **content-addressed** 方式缓存在本地，可直接解包，
覆盖 Nikke-db 未收录的角色/皮肤/剧情演出。

**本地文件位置**（默认，可用环境变量 `NIKKE_LOCAL_ASSETS` 覆盖）：
```
<客户端资源目录>/naps/<hash前2位>/<md5>     # 无扩展名，共 256 个桶(00~ff)
```
默认路径：`Q:/NIKKE_Assets`（Windows PC 客户端资源目录）。

每个缓存文件头分两种：

| 文件头 | 类型 | 占比 | 是否可解 |
|--------|------|------|----------|
| `UnityFS` | 明文 Unity 包 | 约 76%（25266 个） | ✅ UnityPy 直接解 |
| `NKAB` | NIKKE 私有加密包 | 约 24%（8073 个） | ❌ 暂不支持（见下） |

> **重要**：常规立绘 **fb / cover / aim 几乎都在 NKAB 加密包里**，明文包只有
> skillcut / eventscene / 剧情过场。而本项目缓存的 NKAB 全部是 **v4**，
> **目前没有公开可用的 v4 解密器**，故本地拆不出 fb/cover/aim，需走路线 A 从 CDN 获取。
> 详见 [`NKAB_ENCRYPTION.md`](./NKAB_ENCRYPTION.md)。

> 实测一次全盘扫描：25266 个明文包 → `.skel` 共 588 套（skillcut 434 / eventscene 82 /
> 剧情 70 / fb 仅 2），基本不含 fb/cover/aim。

---

## 二、本地文件位置（本项目内）

```
nikke/
├── raw/
│   └── naps/                   # (可选) 本地客户端资源缓存的软链/拷贝
├── data/                       # 还原产物（查看器直接读）
│   └── <id>/                   #   角色 id，如 c010 / c101_01(皮肤) / eventscene_*
│       ├── <stem>.skel         #     骨骼数据（fb 姿态直接放根目录）
│       ├── <stem>.atlas        #     图集描述
│       ├── <page>.png          #     贴图页
│       ├── _icon.png           #     列表封面头像（Nikke-db 下载或本地渲染）
│       ├── cover/              #     半身姿态
│       └── aim/                #     瞄准姿态
│   ├── _fetched.json           #   本次抓取的角色元数据（供 manifest）
│   └── _l2d_index.json         #   (可选) 缓存的 l2d.json 索引
└── viewer/
    └── nikke_manifest.json     # 查看器清单（姿态/Spine版本/分类/封面）
```

**姿态 / 分类命名规律**：

| skel 名 | id | pose |
|---------|-----|------|
| `c010_00` | c010 | fb（立绘全身） |
| `c101_01` | c101_01（皮肤） | fb |
| `c010_cover_00` | c010 | cover（半身） |
| `c010_aim_00` | c010 | aim（瞄准） |
| `c233_00_skillcut` | c233 | skillcut（技能演出立绘，本地解包才有） |
| `EventScene_*` | eventscene_* | fb（活动剧情） |
| `liberation_* / MVG / Glass` | 同名 | fb（过场/剧情） |

manifest 分类（`categorize`）：`character` / `event` / `story` / `misc`。

---

## 三、拆解 → 重组工具链

依赖：`requests`（下载）、`UnityPy`（解本地 Unity 包）、`playwright` + Node（可选，渲染缩略图）。

### 路线 A：从 Nikke-db 拉取

```bash
cd nikke/scripts

# 1. 拉资源（读 l2d.json 索引，按角色下 fb/cover/aim + 头像图标）
python fetch_nikke.py                 # 全部角色
python fetch_nikke.py c010 c470       # 指定角色
python fetch_nikke.py --limit 60      # 前 60 个（快速验证）

# 补漏：l2d.json 索引不全（如 c515 等未收录），但 CDN 上实际存在。
# 扫描本地已有目录，逐个探测 CDN 补抓姿态：
python fetch_nikke.py --scan-existing --only-missing

# 2. 生成查看器 manifest
python gen_nikke_manifest.py

# 3.（可选）为缺头像的角色，用无头浏览器渲染 Spine 生成缩略图
#    需先在仓库根启动 http server，并安装 playwright
CHROMIUM_PATH=<chrome路径> node gen_thumbs.mjs http://127.0.0.1:8000 ../data
```

### 路线 B：从本地客户端缓存解包

```bash
cd nikke/scripts
export NIKKE_LOCAL_ASSETS=Q:/NIKKE_Assets   # 指定客户端资源目录

python extract_local.py               # 扫全部 256 个桶
python extract_local.py --buckets 00 01   # 只扫指定桶
python extract_local.py --limit 30    # 前 N 个桶（快速验证）

python gen_nikke_manifest.py          # 再生成 manifest
```

`extract_local.py` 流程：遍历 `naps/` 下所有桶 → 检测文件头 → 对 `UnityFS` 明文包，
若含 `TextAsset(*.skel)` 则视为 Spine 包 → 提取 `.skel`/`.atlas` + atlas 引用的
`Texture2D` 贴图 → 按 skel 名解析 id/姿态归类。支持断点续跑（skel 已存在则跳过）。

### 脚本一览

| 脚本 | 作用 |
|------|------|
| `fetch_nikke.py` | 路线 A：从 Nikke-db CDN 拉 Spine 资源（多线程，断点续传，补漏扫描） |
| `extract_local.py` | 路线 B：从本地客户端 content-addressed 缓存解包明文 Unity 包 |
| `gen_nikke_manifest.py` | 扫 `data/` → 识别姿态 + Spine 版本 + 分类 + 封面 → 生成 manifest |
| `gen_thumbs.mjs` | 无头浏览器渲染 Spine → 自动裁剪透明边 → 生成头像缩略图 |
| `config.py` | 数据源 URL / 姿态 / 代理 / 本地资源目录配置 |

---

## 四、关键技术点

**1. Spine 版本自适应**
读 `.skel` 头部（前 64 字节内匹配 `[34]\.[0-9]+\.[0-9]+`）拿到版本串（`4.0.x` / `4.1.x`），
只取主次版本（`4.0` / `4.1`）。前端据此选择 `spine-player4.0.js` / `spine-player4.1.js`
（`shared/vendor/`，EsotericSoftware 官方 spine-player）。

**2. 正确渲染（预乘 alpha）**
必须 `premultipliedAlpha: true` + 透明背景。早期用 pixi-spine 未处理预乘 alpha
导致立绘发灰/发黑；改用官方 spine-player 后还原正确。

**3. 动画回退**
部分 skillcut / 剧情场景模型默认动画名不是 `idle`。查看器在 `success` 回调里
按实际可用动画智能选择：默认名 → 含 `idle` 的 → 第一个，避免报错。

**4. 封面缩略图**
优先用 Nikke-db 渲染好的头像图标 `si_{id}_00_s.png`（存为 `_icon.png`）；
缺失时用无头浏览器渲染 Spine、自动裁剪透明边生成缩略图，避免直接显示打乱的 atlas 碎片。
（Cloudflare Pages 对缺失文件返回 200 + HTML，需按 `content-type` 甄别是否真为图片。）

---

## 五、已知边界

以下三点是实测确认的**数据或行为边界**，不是提取缺陷：

**1. c530 无互动语音**

`data/c530/` 的 Spine 资源完整（19 个动画），但 `data/voice/c530_*.wav`
全部缺失。已穷尽验证：`ja` 语言包索引 142,701 个 AudioClip 中，`c530*`
一条都没有 —— 客户端日文语音包未收录该角色。互动时动画正常播放，语音静默跳过
（`interaction.js` 的 `_voiceKnownMissing` 会记住并在状态栏提示）。

**2. 19 条 SoundEvents 僵尸键**

部分 `meta.json` 的 `sounds` 里存在 skel 中并不存在的动画名，例如：

```
c181/cover      cover_reload2 / cover_reload_loop_all
                cover_reload_loop_before / cover_reload_start_before
c080_01/cover   x_cover_reload_strat
c191/cover      cover_skillcut_1
c300/cover      cover_reload1 / cover_reload2
```

回源验证：这些名字在对应 `.skel` 字节里 `find()` 全部返回 -1，是游戏配置里
残留但动画已被裁剪的僵尸条目。`interaction.js` 的 `scheduleSoundEvents`
有 `meta.animations.includes(animName)` 守卫，不会误触发。

**3. 互动为「点击任意位置轮换」，非分部位触发**

点击角色画布任意位置（短按、位移 <6px）会轮换触发
`Touch_1 → Touch_2 → Touch_3`，而不是「点头部播 head 动画」。

依据：穷尽扫描 9808 个静态表后确认，**没有任何表把 `reactionId` 与身体部位关联**。
`CharacterReactionTable` 的 15 个字段里没有部位枚举列（15 个部位关键词出现次数
全部为 0），tail 的 2 个 f32 与反应序号无对应关系（若是部位，Touch_1/2/3 应稳定
对应不同值，实际是混乱分布）。`CharacterVoiceGroupTable` 只是纯语音映射，
`LiveWallpaperReactionTable` 全是剧情 ID，`InteractionObjectTable` 是场景交互物件。

因此轮换实现符合游戏真实行为。

---

## 五之二、CDN 与本地资源的完整性差异（2026-11-11 全量实测）

**背景**：早期从 Nikke-db CDN 下载资源时，**下到 257 个角色就转向了本地解包路线**，
CDN 下载并未完成。因此 `data/` 目录里混着两个来源的资源，需要区分对待。

### 各来源的判定方法

| 来源 | 判定依据 |
|---|---|
| Nikke-db CDN | `.skel` 的 mtime 在 `11-10 15:xx` |
| 本地 cdb 解包 | `.skel` 的 mtime 在 `11-10 17:xx` |

两批 **cid 完全不重叠**（257 vs 440，交集 0），每个目录只有一套来源。

### 四类资源

| 类别 | 数量 | 处理 |
|---|---|---|
| 本地 cdb 解包（当前版本） | 440 | 基准 |
| CDN 已下载 + cdb 也有 | **205** | 可用 cdb 覆盖更新 |
| CDN 已下载 + cdb 没有 | **52** | **唯一副本，必须保留** |
| CDN 索引有 + 文件不存在 | **7** | 永久缺失 |

### 52 个 CDN 独有资源（catalog 里真的没有）

```
c010_03  c350_old  c803_01  c903_01  c907_01  c908
777  bluewaterisland  bowwowparadise  cherryblossom  goldenship
ltk  maidinvalentine  nocallerid  nyanyaparadise  outerautomata
queensorder  schooloflock  seayouagain
smol_anchor  smol_bolt  smol_frima  smol_helm  smol_liter
smol_mary  smol_mast  smol_pepper  smol_rapi  smol_yan
story0001  story0001_2  story0002  story0002_2  story0003
story0201  story0401  story0401_2  story0405  story0702
story1201  story1302  story1303  story1401  story1403
story1405  story1501  story1902  story2002  story2101
story2201  story2202  story2206
```

这些是 CDN 快照里存在、但**当前客户端已移除**的资源（活动下架 / 角色改版 / 剧情资源回收）。

### 7 个永久缺失的角色

| ID | 名录名 | 语音 | 反应 | manifest | ja 音频 clip | CDN 文件 |
|---|---|---|---|---|---|---|
| c053 | **liqueur** | — | — | — | — | HTML 404 |
| c081 | **Acre** | — | — | — | — | HTML 404 |
| c122 | c122 | — | — | — | — | HTML 404 |
| c211 | c211 | — | — | — | — | HTML 404 |
| c320 | **Bibli** | — | — | — | — | HTML 404 |
| c340 | **Siren** | — | — | — | — | HTML 404 |
| c342 | **Pin** | — | — | — | — | HTML 404 |

**结论：这 7 个不是「漏下载」，而是索引里有条目、文件从未存在。**

判据（全部实测）：
- 本地 catalog（core/dp/fd/ja 共 43,168 个 bundle key）深度穷举 → 无对应 spine
- `voiceIndex.json` / `reactions.json` / `ja_audio_index.json`（142,701 clip）→ 全无记录
- `manifest` → 不在列表
- CDN 三个镜像（`nikke-db-legacy.pages.dev` / `nikke-db.pages.dev` /
  `raw.githubusercontent.com`）→ 全部返回 HTML 假响应

> ⚠️ **CDN 对不存在的文件返回 `200 + HTML 404 页`，不是 404 状态码。**
> 实测 c010（真资源）返回 `\xef\xbb\xbfc010_00.png\nsize:2048,2048`；
> c053（假资源）返回 `<!DOCTYPE html>`。
> 因此**不能靠 HTTP 状态码判断文件是否存在**，
> `fetch_nikke.py` 的判据（能否从 atlas 解析出 png 页名）才是正确的。

### 相关脚本

| 脚本 | 用途 |
|---|---|
| `scripts/replace_cdn_with_cdb.py` | 用 cdb 版覆盖 CDN 版（含快照/验证） |
| `scripts/apply_cdn_coverage.py` | 按精确匹配结果执行覆盖 |
| `scripts/render_icons_v2.py` | 用 viewer 渲染 256×512 缩略图 |

---

## 六、版权说明

仅用于个人学习研究。Spine 立绘 / 模型版权归属 NIKKE（SHIFT UP / Level Infinite），
请勿商用或公开再分发。
