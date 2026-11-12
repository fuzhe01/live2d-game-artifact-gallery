/**
 * 字幕气泡（文档 §3.2）。
 *
 * 数据源：data/subtitles.json
 *   { "c010": { "Lobby_Touch_1": {"text": "...", "category": "在大廳點擊時I",
 *                                 "animation": "action"}, ... }, "_meta": {...} }
 *
 * 行为：
 *   - 播放语音时，按 reaction_id / 语音 clip 名查字幕并显示
 *   - 气泡跟随角色头顶（用 spine 的骨骼世界坐标换算到屏幕）
 *   - 提供 X/Y 偏移与字号调节（文档要求的滑杆）
 */
class SubtitleBubble {
  constructor(opts = {}) {
    this.dataDir = opts.dataDir || '../data';
    this.getPlayer = opts.getPlayer || (() => null);
    this.onStatus = opts.onStatus || (() => {});
    this.subs = null;
    this.el = null;

    this.offsetX = 0;
    this.offsetY = 0;
    this.fontSize = 13;
    this.enabled = true;
    this.hideTimer = null;
    this.holdMs = opts.holdMs || 4000;   // 字幕停留时长

    this._rafId = null;
  }

  async load() {
    if (this.subs) return this.subs;
    try {
      const r = await fetch(`${this.dataDir}/subtitles.json`);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      this.subs = await r.json();
    } catch (e) {
      console.warn('[Subtitle] 加载失败', e);
      this.subs = {};
    }
    return this.subs;
  }

  /** 创建 DOM */
  mount(host) {
    if (this.el) return this.el;
    this.el = document.createElement('div');
    this.el.className = 'sub-bubble';
    this.el.innerHTML = `
      <div class="sub-text" id="sub-text"></div>
      <div class="sub-cat" id="sub-cat"></div>`;
    host.appendChild(this.el);
    return this.el;
  }

  /** 查某角色的某条语音字幕 */
  async lookup(cid, clipName) {
    await this.load();
    const c = this.subs[cid];
    if (!c) return null;
    // clipName 形如 c010_Lobby_Touch_1 → 取 c010_ 之后的部分
    const key = clipName.replace(/^c\d{3}(?:_\d{2})?_/, '');
    // 直接命中
    if (c[key]) return c[key];
    // 尝试再剥一层（c010_01_Lobby_Touch_1 → Lobby_Touch_1）
    const m = clipName.match(/^c\d{3}(?:_\d{2})?_(.+)$/);
    if (m && c[m[1]]) return c[m[1]];
    return null;
  }

  /** 显示字幕 */
  async show(cid, clipName) {
    if (!this.enabled) return null;
    const item = await this.lookup(cid, clipName);
    if (!item) return null;
    if (!this.el) return null;

    const t = this.el.querySelector('#sub-text');
    const cat = this.el.querySelector('#sub-cat');
    if (t) t.textContent = item.text;
    if (cat) cat.textContent = item.category || '';
    this.el.classList.add('on');
    this.el.style.fontSize = this.fontSize + 'px';

    if (this.hideTimer) clearTimeout(this.hideTimer);
    this.hideTimer = setTimeout(() => this.hide(), this.holdMs);

    this._startFollow();
    return item;
  }

  /** 显示任意文本（不查表） */
  showText(text, category) {
    if (!this.enabled || !this.el) return;
    const t = this.el.querySelector('#sub-text');
    const cat = this.el.querySelector('#sub-cat');
    if (t) t.textContent = text || '';
    if (cat) cat.textContent = category || '';
    this.el.classList.add('on');
    this.el.style.fontSize = this.fontSize + 'px';
    if (this.hideTimer) clearTimeout(this.hideTimer);
    this.hideTimer = setTimeout(() => this.hide(), this.holdMs);
    this._startFollow();
  }

  hide() {
    if (this.el) this.el.classList.remove('on');
    this._stopFollow();
  }

  /** 气泡跟随角色头顶 */
  _startFollow() {
    if (this._rafId) return;
    const tick = () => {
      this._position();
      this._rafId = requestAnimationFrame(tick);
    };
    this._rafId = requestAnimationFrame(tick);
    this._position();
  }

  _stopFollow() {
    if (this._rafId) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
  }

  /**
   * 气泡跟随角色头顶。
   *
   * ⚠️ 位置算法（2026-11-12 重构）：
   *   旧版按硬编码名字 ['head','neck_1','body_3','center'] 找骨骼，但各角色
   *   骨骼命名完全不同：c010 有 head、c011 只有 body_3、c020 只有 center
   *   （腰部，气泡会跑到画面中间）、c030 四个都没有（直接不定位）。
   *   这是「字幕位置不对」的根因。
   *   现改为用【骨架世界坐标包围盒】：取所有骨骼 worldY 的 P95 作为头顶高度、
   *   worldX 的中位数作为水平中心，与骨骼命名无关，全角色一致。
   *
   * 投影：cam.projectionView.values（4x4 列主序）做 clip = M*[x,y,0,1]。
   *   ⚠️ 不能用 cam.worldToScreen(x,y) —— 会抛
   *   `TypeError: worldCoords.project is not a function`。
   */
  _position() {
    if (!this.el || !this.el.classList.contains('on')) return;
    const pl = this.getPlayer();
    if (!pl || !pl.skeleton) return;
    try {
      const sk = pl.skeleton;
      const cam = pl.sceneRenderer && pl.sceneRenderer.camera;
      const canvas = pl.canvas;
      const m = cam && cam.projectionView && cam.projectionView.values;
      if (!m || m.length < 16 || !canvas) return;

      // ── 用骨骼包围盒求头顶，不依赖骨骼命名 ──
      const ys = [], xs = [];
      for (const b of sk.bones) {
        if (!Number.isFinite(b.worldX) || !Number.isFinite(b.worldY)) continue;
        xs.push(b.worldX);
        ys.push(b.worldY);
      }
      if (!ys.length) return;
      ys.sort((a, b) => a - b);
      xs.sort((a, b) => a - b);
      // P90 排除武器/特效等离群高骨骼；水平用中位数
      const topY = ys[Math.min(ys.length - 1, Math.floor(ys.length * 0.90))];
      const midX = xs[Math.floor(xs.length / 2)];
      const x = midX;
      const y = topY;
      // 列主序 4x4：index = col*4 + row
      const cx = m[0] * x + m[4] * y + m[12];
      const cy = m[1] * x + m[5] * y + m[13];
      const cw = m[3] * x + m[7] * y + m[15] || 1;
      const ndcX = cx / cw;
      const ndcY = cy / cw;

      const sx = (ndcX * 0.5 + 0.5) * (canvas.width || 1);
      let sy = (1 - (ndcY * 0.5 + 0.5)) * (canvas.height || 1);
      // 屏幕空间钳制：不同角色骨架缩放差异极大，投影出的 sy 可能贴死顶边
      // （负值）或落到画面中部。统一夹到画布上部 8%~38% 区间，
      // 保证气泡始终在角色头顶上方、不越界、不压脸。
      // 注意 el 带 translate(-50%,-100%)，其 top 比 sy 再上移一个气泡高度，
      // 因此下限要留足（12%），否则气泡会贴出 stage 顶边。
      const ch = canvas.height || 884;
      sy = Math.max(ch * 0.14, Math.min(ch * 0.38, sy));

      // ⚠️ 不能用 canvas.getBoundingClientRect() 当基准：旋转/镜像 CSS transform
      // 会让它跟着变形导致跳位。用未被变换的 stage-view rect + canvas 内部像素尺寸。
      const stage = document.getElementById('stage-view');
      const base = stage ? stage.getBoundingClientRect() : null;
      if (!base) return;
      const scaleX = base.width / (canvas.width || base.width || 1);
      const scaleY = base.height / (canvas.height || base.height || 1);

      const px = base.left + sx * scaleX + this.offsetX;
      const py = base.top + sy * scaleY + this.offsetY;

      this.el.style.left = Math.round(px) + 'px';
      this.el.style.top = Math.round(py) + 'px';
      this.el.style.transform = 'translate(-50%, -100%)';
    } catch (e) {
      // 静默
    }
  }

  setOffset(x, y) { this.offsetX = x; this.offsetY = y; }
  setFontSize(px) {
    this.fontSize = px;
    if (this.el) this.el.style.fontSize = px + 'px';
  }
  setEnabled(on) {
    this.enabled = !!on;
    if (!on) this.hide();
  }

  /** 建控制面板（偏移/字号） */
  mountControls(host) {
    if (this.panel) return this.panel;
    this.panel = document.createElement('div');
    this.panel.className = 'sub-panel';
    this.panel.innerHTML = `
      <div class="sub-head">
        <span class="sub-title">字幕气泡</span>
        <span class="sub-count" id="sub-count"></span>
        <span class="sub-close" id="sub-close" role="button" tabindex="0">✕</span>
      </div>
      <div class="sub-body">
        <div class="sub-row">
          <label class="sub-label">X 偏移</label>
          <input type="range" class="sub-range" id="sub-ox" min="-200" max="200" step="1" value="0">
          <span class="sub-val" id="sub-ox-v">0</span>
        </div>
        <div class="sub-row">
          <label class="sub-label">Y 偏移</label>
          <input type="range" class="sub-range" id="sub-oy" min="-300" max="100" step="1" value="-30">
          <span class="sub-val" id="sub-oy-v">-30</span>
        </div>
        <div class="sub-row">
          <label class="sub-label">字号</label>
          <input type="range" class="sub-range" id="sub-fs" min="10" max="28" step="1" value="13">
          <span class="sub-val" id="sub-fs-v">13</span>
        </div>
        <div class="sub-row">
          <button class="sub-btn" id="sub-test">测试显示</button>
        </div>
      </div>`;
    host.appendChild(this.panel);

    const closeEl = this.panel.querySelector('#sub-close');
    closeEl.onclick = () => this.hidePanel();
    closeEl.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') this.hidePanel();
    };

    const ox = this.panel.querySelector('#sub-ox');
    const oxv = this.panel.querySelector('#sub-ox-v');
    ox.oninput = () => {
      this.offsetX = parseInt(ox.value, 10);
      oxv.textContent = this.offsetX;
      this._position();
    };
    const oy = this.panel.querySelector('#sub-oy');
    const oyv = this.panel.querySelector('#sub-oy-v');
    oy.oninput = () => {
      this.offsetY = parseInt(oy.value, 10);
      oyv.textContent = this.offsetY;
      this._position();
    };
    const fs = this.panel.querySelector('#sub-fs');
    const fsv = this.panel.querySelector('#sub-fs-v');
    fs.oninput = () => {
      this.setFontSize(parseInt(fs.value, 10));
      fsv.textContent = this.fontSize;
    };
    this.panel.querySelector('#sub-test').onclick = () => {
      this.showText('这是一条测试字幕，用于调整位置和字号。', '测试');
    };

    // 初始值
    this.offsetY = parseInt(oy.value, 10);
    return this.panel;
  }

  updateCount() {
    if (!this.panel || !this.subs) return;
    const el = this.panel.querySelector('#sub-count');
    if (!el) return;
    const n = Object.keys(this.subs).filter(k => k !== '_meta').length;
    el.textContent = `${n} 角色`;
  }

  showPanel() {
    if (this.panel) { this.panel.classList.add('on'); this.updateCount(); }
  }
  hidePanel() { if (this.panel) this.panel.classList.remove('on'); }
  togglePanel() {
    if (!this.panel) return;
    const on = !this.panel.classList.contains('on');
    this.panel.classList.toggle('on', on);
    if (on) this.updateCount();
  }
}

if (typeof window !== 'undefined') {
  window.SubtitleBubble = SubtitleBubble;
}
