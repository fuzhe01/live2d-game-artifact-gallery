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
   * 用 spine 骨骼世界坐标 + camera.projectionView 矩阵换算到屏幕位置。
   *
   * ⚠️ 不能用 cam.worldToScreen(x, y) —— 实测会抛
   *   `TypeError: worldCoords.project is not a function`
   * 它要求传入带 project() 方法的 Vector2 对象，而不是两个数字。
   *
   * 正确做法：直接拿 cam.projectionView.values（4x4 列主序矩阵）做投影：
   *   clip = M * [x, y, 0, 1]
   *   ndc  = clip.xy / clip.w
   *   screenX = (ndc.x * 0.5 + 0.5) * canvas.width
   *   screenY = (1 - (ndc.y * 0.5 + 0.5)) * canvas.height   ← y 轴翻转
   * 实测 head(9.42, 1709.43) → screen(726.7, 204.6) 正确落在头顶。
   */
  _position() {
    if (!this.el || !this.el.classList.contains('on')) return;
    const pl = this.getPlayer();
    if (!pl || !pl.skeleton) return;
    try {
      const sk = pl.skeleton;
      let bone = null;
      for (const name of ['head', 'neck_1', 'body_3', 'center']) {
        bone = sk.bones.find(b => b.data && b.data.name === name);
        if (bone) break;
      }
      if (!bone) return;

      const cam = pl.sceneRenderer && pl.sceneRenderer.camera;
      const canvas = pl.canvas;
      if (!cam || !canvas) return;

      const pv = cam.projectionView;
      const m = pv && pv.values;
      if (!m || m.length < 16) return;

      const x = bone.worldX;
      const y = bone.worldY;
      // 列主序 4x4：index = col*4 + row
      const cx = m[0] * x + m[4] * y + m[12];
      const cy = m[1] * x + m[5] * y + m[13];
      const cw = m[3] * x + m[7] * y + m[15] || 1;
      const ndcX = cx / cw;
      const ndcY = cy / cw;

      const sx = (ndcX * 0.5 + 0.5) * (canvas.width || 1);
      const sy = (1 - (ndcY * 0.5 + 0.5)) * (canvas.height || 1);

      const rect = canvas.getBoundingClientRect();
      const scaleX = rect.width / (canvas.width || rect.width || 1);
      const scaleY = rect.height / (canvas.height || rect.height || 1);

      const px = rect.left + sx * scaleX + this.offsetX;
      const py = rect.top + sy * scaleY + this.offsetY;

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
