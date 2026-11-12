/**
 * 骨骼可视化面板（文档 §4.7）。
 *
 * 不自己画 canvas overlay —— spine-player 内置 SkeletonDebugRenderer，
 * 直接改它的 draw* 开关即可。
 *
 * 运行时路径：
 *   player.sceneRenderer.skeletonDebugRenderer
 *     .drawBones / .drawRegionAttachments / .drawBoundingBoxes
 *     .drawMeshHull / .drawMeshTriangles / .drawPaths
 *     .drawSkeletonXY / .drawClipping
 *     .scale / .boneWidth
 *     .boneLineColor / .boneOriginColor / .attachmentLineColor ...
 *
 * 注意：这些开关初始值是 null（spine-player 从 config.debug 赋值，
 * 未配置就是 null → falsy）。运行时赋 true 后，每帧的渲染循环
 * （内部调用 renderer.drawSkeletonDebug）就会把调试信息画上去。
 */
class DebugView {
  constructor(opts = {}) {
    this.player = null;
    this.panel = null;
    this.onStatus = opts.onStatus || (() => {});
    this.state = {
      bones: false,
      regions: false,
      meshes: false,
      hulls: false,
      bounds: false,
      paths: false,
      clipping: false,
      origin: false,
    };
    this.scale = 1;
    this.boneWidth = 2;
  }

  attach(player) {
    this.player = player;
    this.apply();
  }

  _dr() {
    try { return this.player.sceneRenderer.skeletonDebugRenderer; } catch (e) { return null; }
  }

  /** 是否可用 */
  available() {
    return !!this._dr();
  }

  /**
   * 把 this.state 应用到调试渲染。
   *
   * ⚠️ 关键（审核实测确认）：不能改 `sceneRenderer.skeletonDebugRenderer.*`。
   * spine-player 的主渲染循环每帧都会执行：
   *   (renderer.skeletonDebugRenderer.drawBones = config.debug.bones) || ...
   * 也就是说它每帧都从 config.debug 重新赋一遍。config.debug 是 {} 时
   * 所有字段被写成 undefined —— 手动设的 true 会在下一帧被抹掉，
   * 画面零变化（实测像素变化 nz=0）。
   *
   * 正确做法：改 player.config.debug。改完读回时保持 true
   * （审核独立复测像素变化 4.174%，此前记录的 1.972% 是不同视口/角色下的测量值，
   *   机制本身有效：关闭后像素差为 0.0%）。
   *
   * 例外：drawSkeletonXY 不在 config.debug 的覆盖列表里（vendor 只处理 7 个键），
   * 这个必须直接改 skeletonDebugRenderer，且不会被覆写。
   */
  apply() {
    const pl = this.player;
    if (!pl) return false;
    const s = this.state;
    try {
      // 1) 走 config.debug —— 这 7 个键每帧会被 vendor 读取
      if (!pl.config.debug) pl.config.debug = {};
      const d = pl.config.debug;
      d.bones = !!s.bones;
      d.regions = !!s.regions;
      d.meshes = !!s.meshes;
      d.hulls = !!s.hulls;
      d.bounds = !!s.bounds;
      d.paths = !!s.paths;
      d.clipping = !!s.clipping;

      // 2) 不在 vendor 覆盖列表里的项，直接改 debugRenderer
      const dr = pl.sceneRenderer && pl.sceneRenderer.skeletonDebugRenderer;
      if (dr) {
        dr.drawSkeletonXY = !!s.origin;
        dr.scale = this.scale;
        dr.boneWidth = this.boneWidth;
      }
      return true;
    } catch (e) {
      console.warn('[DebugView] apply 失败', e);
      return false;
    }
  }

  /** 读回 vendor 实际生效的值（用于验证不被覆写） */
  readBack() {
    const pl = this.player;
    if (!pl) return null;
    const d = (pl.config && pl.config.debug) || {};
    const dr = (pl.sceneRenderer && pl.sceneRenderer.skeletonDebugRenderer) || {};
    return {
      config: {
        bones: d.bones, regions: d.regions, meshes: d.meshes,
        hulls: d.hulls, bounds: d.bounds, paths: d.paths, clipping: d.clipping,
      },
      renderer: {
        drawBones: dr.drawBones,
        drawRegionAttachments: dr.drawRegionAttachments,
        drawMeshTriangles: dr.drawMeshTriangles,
        drawMeshHull: dr.drawMeshHull,
        drawBoundingBoxes: dr.drawBoundingBoxes,
        drawPaths: dr.drawPaths,
        drawClipping: dr.drawClipping,
        drawSkeletonXY: dr.drawSkeletonXY,
      },
    };
  }

  /** 切换某一项 */
  toggle(key) {
    if (!(key in this.state)) return false;
    this.state[key] = !this.state[key];
    this.apply();
    this.refresh();
    return this.state[key];
  }

  /** 全开/全关 */
  setAll(on) {
    for (const k of Object.keys(this.state)) this.state[k] = !!on;
    this.apply();
    this.refresh();
  }

  /** 当前是否任意项开启 */
  anyOn() {
    return Object.values(this.state).some(v => v);
  }

  mount(host) {
    if (!this.panel) {
      this.panel = document.createElement('div');
      this.panel.className = 'dbg-panel';
      this.panel.innerHTML = `
        <div class="dbg-head">
          <span class="dbg-title">骨骼调试</span>
          <span class="dbg-state" id="dbg-state">关</span>
          <button class="dbg-close" id="dbg-close">✕</button>
        </div>
        <div class="dbg-body">
          <div class="dbg-grid">
            <label class="dbg-chk"><input type="checkbox" data-k="bones"><span>骨骼</span></label>
            <label class="dbg-chk"><input type="checkbox" data-k="origin"><span>原点</span></label>
            <label class="dbg-chk"><input type="checkbox" data-k="regions"><span>区域框</span></label>
            <label class="dbg-chk"><input type="checkbox" data-k="bounds"><span>包围盒</span></label>
            <label class="dbg-chk"><input type="checkbox" data-k="meshes"><span>网格</span></label>
            <label class="dbg-chk"><input type="checkbox" data-k="hulls"><span>网格壳</span></label>
            <label class="dbg-chk"><input type="checkbox" data-k="paths"><span>路径</span></label>
            <label class="dbg-chk"><input type="checkbox" data-k="clipping"><span>裁剪</span></label>
          </div>
          <div class="dbg-row">
            <label class="dbg-label">线宽</label>
            <input type="range" class="dbg-range" id="dbg-width" min="1" max="6" step="0.5" value="2">
            <span class="dbg-val" id="dbg-width-val">2.0</span>
          </div>
          <div class="dbg-row">
            <label class="dbg-label">缩放</label>
            <input type="range" class="dbg-range" id="dbg-scale" min="0.2" max="3" step="0.1" value="1">
            <span class="dbg-val" id="dbg-scale-val">1.0</span>
          </div>
          <div class="dbg-row dbg-actions">
            <button class="dbg-btn" id="dbg-all">全开</button>
            <button class="dbg-btn" id="dbg-none">全关</button>
          </div>
        </div>`;
      host.appendChild(this.panel);

      this.panel.querySelector('#dbg-close').onclick = () => this.hide();

      this.panel.querySelectorAll('input[data-k]').forEach(cb => {
        cb.onchange = () => {
          this.state[cb.dataset.k] = cb.checked;
          this.apply();
          this.refresh();
        };
      });

      const wEl = this.panel.querySelector('#dbg-width');
      const wVal = this.panel.querySelector('#dbg-width-val');
      wEl.addEventListener('input', () => {
        this.boneWidth = parseFloat(wEl.value);
        wVal.textContent = this.boneWidth.toFixed(1);
        this.apply();
      });

      const sEl = this.panel.querySelector('#dbg-scale');
      const sVal = this.panel.querySelector('#dbg-scale-val');
      sEl.addEventListener('input', () => {
        this.scale = parseFloat(sEl.value);
        sVal.textContent = this.scale.toFixed(1);
        this.apply();
      });

      this.panel.querySelector('#dbg-all').onclick = () => {
        this.setAll(true);
        this.syncCheckboxes();
        this.onStatus('骨骼调试：全部开启');
      };
      this.panel.querySelector('#dbg-none').onclick = () => {
        this.setAll(false);
        this.syncCheckboxes();
        this.onStatus('骨骼调试：已关闭');
      };
    }

    this.apply();
    this.refresh();
    return this.panel;
  }

  syncCheckboxes() {
    if (!this.panel) return;
    this.panel.querySelectorAll('input[data-k]').forEach(cb => {
      cb.checked = !!this.state[cb.dataset.k];
    });
  }

  refresh() {
    if (!this.panel) return;
    const st = this.panel.querySelector('#dbg-state');
    if (st) {
      const on = Object.entries(this.state).filter(([, v]) => v).map(([k]) => k);
      st.textContent = on.length ? `${on.length} 项` : '关';
      st.classList.toggle('on', on.length > 0);
    }
    this.panel.classList.toggle('active', this.anyOn());
  }

  show() { if (this.panel) { this.panel.classList.add('on'); this.refresh(); } }
  hide() { if (this.panel) this.panel.classList.remove('on'); }
  toggle() {
    if (!this.panel) return;
    const on = !this.panel.classList.contains('on');
    this.panel.classList.toggle('on', on);
    if (on) this.refresh();
  }
}

if (typeof window !== 'undefined') {
  window.DebugView = DebugView;
}
