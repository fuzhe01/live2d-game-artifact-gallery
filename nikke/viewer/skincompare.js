/**
 * 皮肤对比同屏（文档 §4.2）。
 *
 * 同一基础角色（如 c140）的多个皮肤（c140 / c140_01 / c140_02 / c140_03）
 * 并排渲染，动画同步：任一播放器切动画，其它播放器同步切同名动画。
 *
 * 设计要点：
 *   - 不复用主 player，独立建 N 个 SpinePlayer 到 N 个容器
 *   - 每个容器各自 waitForSize，避免 0 尺寸退化成 300x150
 *   - 退出对比时 dispose 所有 player，恢复单角色模式
 *   - 动画同步用「同名动画交集」：只播所有皮肤都存在的动画名
 *
 * 依赖：宿主页面提供
 *   - getManifest(): 返回 manifest.characters 数组
 *   - getRuntime(info): 按版本返回 spine40 / spine41
 *   - blobToDataURL(blob), waitForSize(id)
 *   - findIdle(anims), isLoopAnim(name)
 *   - onStatus(msg)
 */
class SkinCompare {
  constructor(opts = {}) {
    this.getManifest = opts.getManifest || (() => []);
    this.getRuntime = opts.getRuntime || (() => null);
    this.blobToDataURL = opts.blobToDataURL;
    this.waitForSize = opts.waitForSize;
    this.findIdle = opts.findIdle || ((a) => a.find(x => /idle/i.test(x)) || a[0]);
    this.onStatus = opts.onStatus || (() => {});

    this.players = [];        // [{cid, containerId, player, anims, info}]
    this.panel = null;        // 对比面板 DOM
    this.active = false;
    this.rootId = null;       // 基础角色 id，如 c140
    this.basePose = 'fb';
    this.speed = 1;
    this._syncing = false;
    this._token = 0;
  }

  /** 找出某角色的所有皮肤（含自身），返回 manifest 条目 */
  skinsOf(cid) {
    const m = cid.match(/^(c\d{3})(?:_\d{2})?$/);
    if (!m) return [cid ? this._find(cid) : null].filter(Boolean);
    const root = m[1];
    const all = this.getManifest();
    const out = all.filter(c => {
      const mm = c.id.match(/^(c\d{3})(?:_\d{2})?$/);
      return mm && mm[1] === root && c.poses && c.poses[this.basePose];
    });
    // 至少要有 2 个才有对比意义
    return out;
  }

  _find(cid) {
    return this.getManifest().find(c => c.id === cid);
  }

  /** 可同步播放的动画交集 */
  _commonAnims() {
    if (!this.players.length) return [];
    let common = null;
    for (const p of this.players) {
      const s = new Set(p.anims);
      if (common === null) common = s;
      else common = new Set([...common].filter(x => s.has(x)));
    }
    return common ? [...common].sort() : [];
  }

  /** 入口：切换对比模式 */
  async toggle(cid, pose) {
    if (this.active) {
      this.close();
      return false;
    }
    this.basePose = pose || 'fb';
    const skins = this.skinsOf(cid);
    if (skins.length < 2) {
      this.onStatus('该角色没有可比对的皮肤（需要 ≥2 个）');
      return false;
    }
    // 最多同时 4 个（再多画布太小看不清）
    const list = skins.slice(0, 4);
    this.rootId = cid.replace(/^(c\d{3}).*$/, '$1');
    await this._open(list);
    return true;
  }

  async _open(skins) {
    const tk = ++this._token;
    this.active = true;
    this.players = [];

    // 建 DOM
    const host = document.getElementById('stage-view');
    if (!this.panel) {
      this.panel = document.createElement('div');
      this.panel.className = 'sc-panel';
      this.panel.innerHTML = `
        <div class="sc-head">
          <span class="sc-title">皮肤对比</span>
          <span class="sc-count" id="sc-count"></span>
          <span class="sc-close" id="sc-close" role="button" tabindex="0">✕ 退出对比</span>
        </div>
        <div class="sc-grid" id="sc-grid"></div>
        <div class="sc-bar">
          <span class="sc-label">同步动画</span>
          <div class="sc-anims" id="sc-anims"></div>
        </div>`;
      host.appendChild(this.panel);
      const closeEl = this.panel.querySelector('#sc-close');
      closeEl.onclick = () => this.close();
      closeEl.onkeydown = (e) => {
        if (e.key === 'Enter' || e.key === ' ') this.close();
      };
    }
    this.panel.classList.add('on');

    const grid = this.panel.querySelector('#sc-grid');
    grid.innerHTML = '';
    grid.style.gridTemplateColumns = `repeat(${skins.length}, minmax(0, 1fr))`;
    const cnt = this.panel.querySelector('#sc-count');
    if (cnt) cnt.textContent = `${this.rootId} · ${skins.length} 个皮肤`;

    // 主舞台的普通视图隐藏（但不清空，退出时恢复）
    this._hideMainStage();

    // 逐个容器
    for (let i = 0; i < skins.length; i++) {
      const c = skins[i];
      const cid = 'sc-cell-' + i;
      const cell = document.createElement('div');
      cell.className = 'sc-cell';
      cell.innerHTML = `
        <div class="sc-cell-name">${c.name || c.id}</div>
        <div class="sc-cell-id">${c.id}</div>
        <div class="sc-canvas" id="${cid}"></div>
        <div class="sc-cell-state" id="${cid}-state">加载中…</div>`;
      grid.appendChild(cell);
    }

    // 逐个加载 player
    let okCount = 0;
    for (let i = 0; i < skins.length; i++) {
      if (tk !== this._token) return;   // 期间被关闭
      const c = skins[i];
      const cid = 'sc-cell-' + i;
      const state = document.getElementById(cid + '-state');
      try {
        const info = c.poses[this.basePose];
        const sub = info.sub ? info.sub + '/' : '';
        const base = `${c.dir}/${sub}`;
        const skelUrl = base + info.skel;
        const atlasUrl = base + info.atlas;

        const buf = await (await fetch(skelUrl)).arrayBuffer();
        if (tk !== this._token) return;
        const versionString = new TextDecoder()
          .decode(new Uint8Array(buf).slice(0, 16)).replace(/\0/g, '');
        let runtime;
        if (/4\.0\.\d+/.test(versionString)) runtime = this.getRuntime('4.0');
        else if (/4\.1\.\d+/.test(versionString)) runtime = this.getRuntime('4.1');
        else runtime = this.getRuntime(info.version);

        const dataUri = await this.blobToDataURL(new Blob([buf]));
        if (tk !== this._token) return;
        await this.waitForSize(cid);
        if (tk !== this._token) return;

        const key = `${c.id}_${this.basePose}`;
        const pl = await new Promise((resolve, reject) => {
          let settled = false;
          const inst = new runtime.SpinePlayer(cid, {
            skelUrl: key,
            rawDataURIs: { [key]: dataUri },
            atlasUrl,
            premultipliedAlpha: true,
            alpha: true,
            backgroundColor: '#00000000',
            mipmaps: false,
            preserveDrawingBuffer: true,
            showControls: false,
            defaultMix: 0.25,
            success: (p) => { if (!settled) { settled = true; resolve(p); } },
            error: (p, msg) => { if (!settled) { settled = true; reject(new Error(msg || 'load failed')); } },
          });
          // 兜底超时
          setTimeout(() => {
            if (!settled) { settled = true; reject(new Error('timeout')); }
          }, 20000);
        });

        const anims = pl.animationState.data.skeletonData.animations.map(a => a.name);
        this.players.push({ cid: c.id, containerId: cid, player: pl,
                            anims, info, charName: c.name || c.id });
        // 与主播放器同一机制：只写 speed，timeScale 恒 1
        pl.speed = this.speed;
        pl.animationState.timeScale = 1;
        if (state) state.textContent = `${anims.length} 个动画`;
        okCount++;
      } catch (e) {
        if (state) state.textContent = '加载失败: ' + (e.message || e);
      }
    }

    if (tk !== this._token) return;
    if (!this.players.length) {
      this.onStatus('对比模式：所有皮肤加载失败');
      return;
    }

    // 同步动画按钮（用交集）
    this._buildAnimButtons();
    // 所有 player 播同一个默认动画
    const common = this._commonAnims();
    const idle = this.findIdle(common);
    if (idle) this.playAll(idle);

    this.onStatus(`皮肤对比：${this.players.length}/${skins.length} 个已加载，动画已同步`);
  }

  _hideMainStage() {
    // 把原来的 player-container / 面板隐藏，避免和对比面板叠在一起
    const ids = ['player-container', 'motion-panel', 'interact-bar',
                 'pose-tabs', 'ver-badge'];
    this._hidden = [];
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) {
        this._hidden.push({ el, display: el.style.display });
        el.style.display = 'none';
      }
    }
    // 浮层面板也收起
    for (const cls of ['vb-panel', 'mx-panel', 'dbg-panel',
                       'pose-panel', 'sub-panel', 'tl-panel']) {
      document.querySelectorAll('.' + cls).forEach(el => el.classList.remove('on'));
    }
    if (window.__sub) window.__sub.hide();

    // 暂停主 player：display:none 会让它的 canvas resize 成 0×0，
    // 但 spine-player 的渲染循环仍在跑（实测 600ms 内 trackTime +0.573），
    // 白白占用一个 WebGL 上下文和 RAF 回调。这里显式暂停，退出时恢复。
    this._mainWasPaused = null;
    try {
      const mp = (window.__ia && window.__ia.player) || null;
      if (mp) {
        this._mainPlayerRef = mp;
        this._mainWasPaused = !!mp.paused;
        mp.paused = true;
      }
    } catch (e) {}
  }

  _restoreMainStage() {
    if (this._hidden) {
      for (const { el, display } of this._hidden) el.style.display = display;
      this._hidden = null;
    }
    // 恢复主 player 的播放状态（_hideMainStage 里可能暂停过）
    try {
      const mp = this._mainPlayerRef;
      if (mp && this._mainWasPaused === false) mp.paused = false;
    } catch (e) {}
    this._mainPlayerRef = null;
    this._mainWasPaused = null;
  }

  /** 同步动画按钮 */
  _buildAnimButtons() {
    const box = this.panel.querySelector('#sc-anims');
    if (!box) return;
    const common = this._commonAnims();
    box.innerHTML = '';
    if (!common.length) {
      box.innerHTML = '<span class="sc-none">无共同动画</span>';
      return;
    }
    const order = { idle: 0 };
    const sorted = [...common].sort((a, b) => {
      const pa = /idle/i.test(a) ? -1 : 0;
      const pb = /idle/i.test(b) ? -1 : 0;
      if (pa !== pb) return pa - pb;
      return a.localeCompare(b);
    });
    for (const a of sorted) {
      const b = document.createElement('button');
      b.className = 'sc-anim-btn';
      b.textContent = a;
      b.dataset.anim = a;
      b.onclick = () => this.playAll(a);
      box.appendChild(b);
    }
  }

  /** 所有 player 播同名动画（同步） */
  playAll(name) {
    if (this._syncing) return;
    this._syncing = true;
    let applied = 0;
    for (const p of this.players) {
      if (!p.anims.includes(name)) continue;
      try {
        p.player.animationState.setAnimation(0, name, /idle|stand|wait|loop/i.test(name));
        p.player.speed = this.speed;            // 与主播放器同一机制
        p.player.animationState.timeScale = 1;  // 恒 1，避免平方减速
        p.player.play();
        applied++;
      } catch (e) {}
    }
    this.panel.querySelectorAll('.sc-anim-btn').forEach(b =>
      b.classList.toggle('on', b.dataset.anim === name));
    this._syncing = false;
    this.onStatus(`同步播放 ${name}（${applied}/${this.players.length} 个皮肤）`);
    return applied;
  }

  /**
   * 设置所有 player 播放速度。
   *
   * ⚠️ 必须与主播放器用同一机制：只写 `player.speed`，`timeScale` 恒 1。
   * 实测 pl.speed 与 animationState.timeScale 都会乘进渲染 delta，
   * 双写会平方减速（0.5×0.5=0.25）。
   * 早期版本这里只写 timeScale，导致主 player 用 speed、对比 player 用
   * timeScale，两套机制分裂，对比中调速度不生效或不一致。
   */
  setSpeed(v) {
    this.speed = v;
    for (const p of this.players) {
      try {
        p.player.speed = v;
        p.player.animationState.timeScale = 1;
      } catch (e) {}
    }
  }

  /** 关闭对比，恢复单角色模式 */
  close() {
    this._token++;
    if (!this.active) return;
    this.active = false;
    for (const p of this.players) {
      try { p.player.dispose(); } catch (e) {}
    }
    this.players = [];
    if (this.panel) this.panel.classList.remove('on');
    this._restoreMainStage();
    // 同步入口按钮高亮：不管是点面板里的「✕ 退出对比」还是 exitStage()，
    // 都必须清掉 #ia-skin-compare 的 on 类，否则按钮会一直显示激活态。
    const btn = document.getElementById('ia-skin-compare');
    if (btn) btn.classList.remove('on');
    this.onStatus('已退出皮肤对比');
  }

  isActive() { return this.active; }
}

if (typeof window !== 'undefined') {
  window.SkinCompare = SkinCompare;
}
