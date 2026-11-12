"""生成 NIKKE Spine 资源的 manifest。

扫描 data/<id>/ 目录，识别三种姿态：
    fb   : {id}_00.skel|.atlas          （立绘全身，默认）
    cover: cover/{id}_cover_00.skel|.atlas
    aim  : aim/{id}_aim_00.skel|.atlas

同时兼容旧的 EventScene 目录（无姿态子目录，直接放 .skel/.atlas/.png）。
每条记录读取 skel 头部 16 字节，识别 Spine 版本（4.0 / 4.1）供前端选择 runtime。
封面优先用 fb 的首张贴图。
输出 viewer/nikke_manifest.json。
"""
import os
import re
import json
import glob

import config as C

VERSION_RE = re.compile(rb"([34]\.[0-9]+\.[0-9]+)")


def detect_version(skel_path):
    """读 skel 头部识别 Spine 版本，返回 '4.0' / '4.1' / '3.8' 等（主次版本）。"""
    try:
        with open(skel_path, "rb") as f:
            head = f.read(64)
        m = VERSION_RE.search(head)
        if m:
            full = m.group(1).decode()
            return ".".join(full.split(".")[:2])
    except Exception:
        pass
    return "4.1"


def atlas_pages(atlas_path):
    pages = []
    try:
        with open(atlas_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip().lstrip("\ufeff")
                if line.endswith(".png") and line not in pages:
                    pages.append(line)
    except Exception:
        pass
    return pages


def find_pose(rdir, cid, pose):
    """返回该姿态的 {skel, atlas, pages, dir}（相对 rdir），找不到返回 None。

    不假设固定文件名，而是扫描对应目录里实际存在的 skel/atlas，
    以兼容 Nikke-db 命名({cid}_00) 和本地解包命名({cid}_00_skillcut) 等。
    """
    pdir = rdir if pose == "fb" else os.path.join(rdir, pose)
    if not os.path.isdir(pdir):
        return None
    skels = sorted(glob.glob(os.path.join(pdir, "*.skel")))
    atlases = sorted(glob.glob(os.path.join(pdir, "*.atlas")))
    if not skels or not atlases:
        return None
    # fb 目录下可能混有多个 skel（不同来源），优先取与 atlas 同名的一组
    skel = skels[0]
    atlas = atlases[0]
    stem = os.path.splitext(os.path.basename(skel))[0]
    same = os.path.join(pdir, stem + ".atlas")
    if os.path.exists(same):
        atlas = same
    return {
        "sub": "" if pose == "fb" else pose,
        "skel": os.path.basename(skel),
        "atlas": os.path.basename(atlas),
        "pages": atlas_pages(atlas),
        "version": detect_version(skel),
    }


def scan_legacy(rdir, cid):
    """兼容旧 EventScene：任意 .skel + .atlas（无姿态命名规范）。"""
    skels = sorted(glob.glob(os.path.join(rdir, "*.skel")))
    atlases = sorted(glob.glob(os.path.join(rdir, "*.atlas")))
    if not skels or not atlases:
        return None
    skel = os.path.basename(skels[0])
    atlas = os.path.basename(atlases[0])
    return {
        "sub": "",
        "skel": skel,
        "atlas": atlas,
        "pages": atlas_pages(os.path.join(rdir, atlas)),
        "version": detect_version(os.path.join(rdir, skel)),
    }


def base_id_of(cid):
    """皮肤/服装变体归到本体：c010_02 → c010；c010 → c010；其它原样返回。

    这是「同角色多服装合并」的数据基础。只认 cNNN_NN 形态，
    cNNN_mini / eventscene_* / story* 一律独立成组。
    """
    m = re.match(r"^(c\d{3})_\d{2}$", cid)
    return m.group(1) if m else cid


def load_char_db():
    """构建「角色数据库」：id -> {name, nameCn, 多语言, baseId, isSkin}。

    数据源优先级（只采信外部数据，绝不回读现有 manifest 的名字，
    否则会把生成的显示名固化、导致重建漂移）：
      1. _nikkedb_l2d.json  —— Nikke-db 开源的多语言角色表
         （756 条，448 个含简体中文 cn；来源为客户端 Locale_Character SQLite）
      2. _l2d_index.json    —— 旧 CDN 索引（267 条，补齐 l2d 未收录的 id）
      3. _fetched.json      —— 本次抓取

    输出同时写一份 character_db.json 作为「数据契约」，供前端与后续脚本使用。
    """
    db = {}

    def put(cid, **fields):
        cur = db.setdefault(cid, {})
        for k, v in fields.items():
            if v and not cur.get(k):
                cur[k] = v

    # 1. Nikke-db 多语言表（最高优先级）
    #    优先读随仓库提交的 scripts/names/ 副本（可重建）；兼容旧的 data/ 缓存。
    l2d_path = os.path.join(os.path.dirname(__file__), "names", "nikkedb_l2d.json")
    if not os.path.exists(l2d_path):
        l2d_path = os.path.join(C.DATA_DIR, "_nikkedb_l2d.json")
    if os.path.exists(l2d_path):
        try:
            for e in json.load(open(l2d_path, encoding="utf-8")):
                cid = e.get("id")
                if not cid:
                    continue
                put(cid,
                    name=e.get("name"),
                    nameCn=e.get("cn"),
                    nameKo=e.get("ko"), nameJp=e.get("jp"),
                    nameTw=e.get("tw"), nameDe=e.get("de"),
                    nameTh=e.get("th"), nameFr=e.get("fr"))
                if e.get("f"):
                    put(e["f"].lower(),
                        name=e.get("name"), nameCn=e.get("cn"))
        except Exception as ex:
            print(f"  [warn] 读取 _nikkedb_l2d.json 失败: {ex}")

    # 2. 旧 CDN 索引（补漏，不覆盖已有）
    idx_path = os.path.join(C.DATA_DIR, "_l2d_index.json")
    if os.path.exists(idx_path):
        try:
            for e in json.load(open(idx_path, encoding="utf-8")):
                if e.get("name") and e["name"] != e["id"]:
                    put(e["id"], name=e["name"])
                    if e.get("f"):
                        put(e["f"].lower(), name=e["name"])
        except Exception:
            pass

    # 3. _fetched.json —— 仅采用非占位名字
    meta_path = os.path.join(C.DATA_DIR, "_fetched.json")
    if os.path.exists(meta_path):
        try:
            for e in json.load(open(meta_path, encoding="utf-8")):
                nm = e.get("name", e["id"])
                if nm and nm != e["id"]:
                    put(e["id"], name=nm)
        except Exception:
            pass

    # 派生 baseId / isSkin
    for cid, fields in db.items():
        fields["baseId"] = base_id_of(cid)
        fields["isSkin"] = fields["baseId"] != cid

    return db


def write_char_db(db):
    """把角色数据库单独落盘，作为可复用的数据契约（viewer 与脚本共用）。"""
    out = {
        "_source": "Nikke-db/nikke-db-vue src/utils/json/l2d.json (Locale_Character SQLite)",
        "_note": "角色名/多语言/服装分组的单一真相源；由 gen_nikke_manifest.py 生成",
        "characters": {cid: f for cid, f in sorted(db.items())},
    }
    path = os.path.join(C.VIEWER_DIR, "character_db.json")
    os.makedirs(C.VIEWER_DIR, exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"角色数据库 {len(db)} -> {path}")
    return path


def categorize(cid):
    """给条目打分类标签，用于前端导航/筛选。

    character : 角色（c### / c###_皮肤）
    event     : 活动剧情立绘（eventscene*）
    story     : 主线过场/剧情（story*/chapter*/mvg*/*glass*/*chap*/liberation*/timeline*）
    misc      : 其它（活动 key / Q版 / mini / 未归类）
    """
    i = cid.lower()
    if re.match(r"^c\d{3}(_\d+)?$", i):
        return "character"
    if i.startswith("eventscene"):
        return "event"
    # ⚠️ 必须包含 story 前缀：data 里有 23 个 story0001/story2202 条目，
    # 旧规则只判 chapter/mvg/liberation/timeline/glass/chap，
    # 导致它们全落到 misc，「剧情」标签页永远为空。
    if (i.startswith("story") or i.startswith("chapter")
            or i.startswith("mvg") or i.startswith("liberation")
            or i.startswith("timeline") or "glass" in i or "chap" in i
            or "anarchyorr" in i):
        return "story"
    return "misc"


def load_gallery_index():
    """加载 Nikke-db Gallery 分类索引 + 游戏内活动短名，用于替代机械派生分组。

    数据源（随仓库提交，clone 可重建）：
      names/nikkedb_gallery.json  —— Nikke-db Gallery 88 组（id/title/items/bucket）
      names/event_short_names.json —— ArchiveEventStoryTable 提取的 16 条活动短名

    返回 (by_gid, by_norm, by_story, short_names)：
      by_gid      : 规范化 gallery id → (bucket, title)
      by_norm     : 规范化资源名     → (bucket, title)
      by_story    : 'NNNN' 章节键    → (bucket, title)
      short_names : 游戏内部名 → 显示短名（bowwowparadise → bow-wowparadise）
    """
    by_gid, by_norm, by_story = {}, {}, {}
    story_ranges = []      # [(lo, hi, (bucket, title))] 从 title 解析章节区间
    short_names = {}

    gal_path = os.path.join(os.path.dirname(__file__), "names", "ikkedb_gallery.json")
    if not os.path.exists(gal_path):
        gal_path = os.path.join(os.path.dirname(__file__), "names", "nikkedb_gallery.json")
    if os.path.exists(gal_path):
        try:
            gal = json.load(open(gal_path, encoding="utf-8")).get("gallery") or []
            for g in gal:
                key = (g.get("bucket"), g.get("title"))
                gid = g.get("id") or (g.get("file") or "").replace(".json", "")
                if gid:
                    by_gid.setdefault(_norm(gid), key)
                for it in g.get("items") or []:
                    by_norm.setdefault(_norm(it), key)
                if g.get("bucket") == "Story":
                    for it in g.get("items") or []:
                        m = re.search(r"(?:Main|Chap|Story)_?(\d{2})_(\d{2})", it or "", re.I)
                        if m:
                            by_story.setdefault(m.group(1) + m.group(2), key)
                    # 解析 title 里的章节区间："Chapters 0 to 9" / "Chapter 43 to 44"
                    nums = [int(x) for x in re.findall(r"\d+", g.get("title") or "")]
                    if len(nums) >= 2:
                        story_ranges.append((min(nums[0], nums[1]),
                                             max(nums[0], nums[1]), key))
            print(f"  Gallery 分类 {len(gal)} 组（Story 区间 {len(story_ranges)} 段）")
        except Exception as ex:
            print(f"  [warn] 读取 Gallery 索引失败: {ex}")

    sh_path = os.path.join(os.path.dirname(__file__), "names", "event_short_names.json")
    if os.path.exists(sh_path):
        try:
            short_names = json.load(open(sh_path, encoding="utf-8"))
        except Exception:
            pass
    return by_gid, by_norm, by_story, short_names, story_ranges


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def gallery_of(cid, idx):
    """给一个 cid 找它所属的 Nikke-db Gallery 分组 → (bucket, title)。

    匹配顺序：
      1. storyNNNN           → 先按精确 item 命中，再按 title 的章节区间归类
      2. eventscene_xxx_NN   → 用活动名 xxx 查 gallery id（含前缀放宽）
      3. 整 id               → 查 gallery id（777 / bluewaterisland …）
      4. 规范化资源名
    匹配不上返回 (None, None)，前端归入「其它」。
    """
    by_gid, by_norm, by_story, short_names, story_ranges = idx
    hit = None

    m = re.match(r"^story(\d{4})", cid)
    if m:
        hit = by_story.get(m.group(1))
        if not hit:
            # 按章节区间归类（story1 = "Chapters 0 to 9" 覆盖 00~09）
            try:
                ch = int(m.group(1)[:2])
            except ValueError:
                ch = None
            if ch is not None:
                for lo, hi, key in story_ranges:
                    if lo <= ch <= hi:
                        hit = key
                        break

    if not hit and cid.startswith("eventscene_"):
        base = re.sub(r"_\d+$", "", cid[len("eventscene_"):])
        hit = by_gid.get(_norm(base))
        if not hit:
            nb = _norm(base)
            for k, key in by_gid.items():
                if len(k) >= 4 and (k.startswith(nb) or nb.startswith(k)):
                    hit = key
                    break
        # 游戏内短名反查（freezeacpu → acpufreeze 之类）
        if not hit:
            for k, v in short_names.items():
                if _norm(v) == _norm(base) or _norm(k) == _norm(base):
                    # 用短名再试一次 gallery id
                    hit = by_gid.get(_norm(v))
                    if hit:
                        break

    if not hit:
        hit = by_gid.get(_norm(cid))
    if not hit:
        hit = by_norm.get(_norm(cid))
    if not hit and cid.startswith("eventscene_"):
        hit = by_norm.get(_norm(cid[len("eventscene_"):]))

    # 兜底：Nikke-db 未收录、但游戏 ArchiveEventStoryTable 里有正式短名的活动，
    # 仍归入 Events 桶，title 用游戏内短名（bow-wowparadise / acpufreeze …），
    # 避免把它们和被丢进 Other 的 Q版/标题图混在一起。
    if not hit and cid.startswith("eventscene_"):
        base = _norm(re.sub(r"_\d+$", "", cid[len("eventscene_"):]))
        for k, v in short_names.items():
            if base and (_norm(k) == base or _norm(v) == base):
                return ("Events", v)

    return hit if hit else (None, None)


def cover_url(cid, pose_info):
    """封面优先用渲染好的头像图标 _icon.png；否则退回姿态首张贴图（可能是碎片 atlas）。"""
    icon = os.path.join(C.DATA_DIR, cid, "_icon.png")
    if os.path.exists(icon) and os.path.getsize(icon) > 0:
        return f"../data/{cid}/_icon.png"
    if pose_info and pose_info.get("pages"):
        sub = pose_info["sub"]
        prefix = f"../data/{cid}/{sub}/" if sub else f"../data/{cid}/"
        return prefix + pose_info["pages"][0]
    return None


def main():
    db = load_char_db()
    gallery_idx = load_gallery_index()
    chars = []
    if not os.path.isdir(C.DATA_DIR):
        print("无 data 目录")
        return

    for cid in sorted(os.listdir(C.DATA_DIR)):
        rdir = os.path.join(C.DATA_DIR, cid)
        if not os.path.isdir(rdir):
            continue

        poses = {}
        for pose in ("fb", "cover", "aim", "skillcut"):
            info = find_pose(rdir, cid, pose)
            if info:
                poses[pose] = info

        # 若标准命名没找到，尝试旧 EventScene 结构
        if not poses:
            legacy = scan_legacy(rdir, cid)
            if legacy:
                poses["fb"] = legacy

        if not poses:
            continue

        fb = poses.get("fb") or next(iter(poses.values()))
        info = db.get(cid, {})
        rec = {
            "id": cid,
            "name": info.get("name") or cid,
            "dir": f"../data/{cid}",
            "category": categorize(cid),
            "poses": poses,
            "version": fb["version"],
            "cover": cover_url(cid, fb),
            # ── 数据契约字段（⑦ / ① 的基础）──
            "baseId": base_id_of(cid),
            "isSkin": base_id_of(cid) != cid,
        }
        # 中文名与多语言：只在存在时写入，避免污染 442 个占位条目
        for k in ("nameCn", "nameKo", "nameJp", "nameTw",
                  "nameDe", "nameTh", "nameFr"):
            if info.get(k):
                rec[k] = info[k]
        # ⑦ 借鉴 nikke-db gallery 的插画分类：给活动/剧情加副分类，
        #   便于按活动名 / 章节号筛选（不改主分类）。
        cat = rec["category"]
        # ⑦ Gallery 分类：直接采用 Nikke-db 的 gallery 分类体系
        #    （Story / SideStories / Events / Other + 它的 title），
        #    不再自己从 id 机械截取 eventGroup / chapter。
        #    character 不进 gallery 分类（那是角色库，对应 nikke-db 的 visualiser）；
        #    非 character 且未命中 Gallery 的归入 Other，与 nikke-db 的 Other 桶一致。
        if rec["category"] != "character":
            gb, gt = gallery_of(cid, gallery_idx)
            rec["galleryBucket"] = gb or "Other"
            if gt:
                rec["galleryTitle"] = gt
        chars.append(rec)

    os.makedirs(C.VIEWER_DIR, exist_ok=True)
    json.dump({"characters": chars}, open(C.MANIFEST_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"NIKKE 角色 {len(chars)} -> {C.MANIFEST_PATH}")
    write_char_db(db)
    print(f"真名 {sum(1 for c in chars if c['name'] != c['id'])} / "
          f"中文名 {sum(1 for c in chars if c.get('nameCn'))}")
    # 版本 + 分类统计
    from collections import Counter
    vc = Counter(c["version"] for c in chars)
    cc = Counter(c["category"] for c in chars)
    print("版本分布:", dict(vc))
    print("分类分布:", dict(cc))
    _sync_counts(len(chars), cc.get("character", 0))


def _sync_counts(n_total, n_chars):
    """把 manifest 的实际数量回写到 index.html 与 README.md，避免手动改漏。"""
    repo = os.path.dirname(C.BASE)
    idx_path = os.path.join(repo, "index.html")
    readme_path = os.path.join(repo, "README.md")
    if os.path.exists(idx_path):
        try:
            src = open(idx_path, encoding="utf-8").read()
            out = re.sub(r'(<span class="tag">Spine · )\d+(</span>)', rf'\g<1>{n_total}\g<2>', src)
            out = re.sub(r'(<span class="tag">角色 · )\d+(</span>)', rf'\g<1>{n_chars}\g<2>', out)
            if out != src:
                open(idx_path, "w", encoding="utf-8", newline="").write(out)
                print("  计数已同步: index.html")
        except Exception as e:
            print(f"  计数同步失败 (index.html): {e}")

    if os.path.exists(readme_path):
        try:
            src = open(readme_path, encoding="utf-8").read()
            out = re.sub(r'(- \*\*立绘 / 模型\*\*：)\d+( 套 Spine 骨骼动画（其中角色 )\d+( 套）)',
                         rf'\g<1>{n_total}\g<2>{n_chars}\g<3>', src)
            if out != src:
                open(readme_path, "w", encoding="utf-8", newline="").write(out)
                print("  计数已同步: README.md")
        except Exception as e:
            print(f"  计数同步失败 (README.md): {e}")


if __name__ == "__main__":
    main()
