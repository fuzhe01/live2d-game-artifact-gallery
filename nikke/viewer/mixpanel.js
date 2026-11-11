/**
 * 动画混合面板（文档 §4.6）。
 *
 * 作用：调整动画之间的过渡时长（mix duration）。
 *   - 游戏本身的默认混合时长是 0.25s（AnimationStateData.defaultMix）
 *   - 画廊里可以让用户自由调 0~1s，观察骨架如何平滑过渡
 *   - 也可以为特定动画对单独设 mix（例如 idle→action 快一点，action→idle 慢一点）
 *
 * API 要点（实测 spine-player 4.x）：
 *   animationState.data.setMix(fromName, toName, duration)   // 接受字符串
 *   animationState.data.getMix(from, to)                     // 注意：接受动画【对象】，不是字符串！
 *   animationState.data.defaultMix = 0.2                     // 全局默认
 *   animationState.data.animationToMixTime                   // 内部字典 { "from.to": duration }
 */
class MixPanel {
  constructor(opts = {}) {
    this.player = null;
    this.panel = null;
    this.onStatus = opts.onStatus || (() => {});
    this.anims = [];
  }

  attach(player) {
    this.player = player;
    this.anims = this._listAnims();
    this.refresh();
  }

  _listAnims() {
    try {
      return this.player.animationState.data.skeletonData.animations.map(a => a.name);
    } catch (e) {
      return [];
    }
  }

  data() {
    try { return this.player.animationState.data; } catch (e) { return null; }
  }

  /** 读取全局默认混合时长 */
  getDefault() {
    const d = this.data();
    return d ? (typeof d.defaultMix === 'number' ? d.defaultMix : 0) : 0;
  }

  /** 设置全局默认混合时长（秒） */
  setDefault(sec) {
    const d = this.data();
    if (!d) return false;
    d.defaultMix = Math.max(0, Math.min(2, sec));
    return true;
  }

  /** 读取特定动画对的混合时长（必须传动画对象） */
  getMix(fromName, toName) {
    const d = this.data();
    if (!d) return null;
    try {
      const sd = this.player.animationState.data.skeletonData;
      const from = sd.findAnimation(fromName);
      const to = sd.findAnimation(toName);
      if (!from || !to) return null;
      return d.getMix(from, to);
    } catch (e) {
      return null;
    }
  }

  /** 设置特定动画对的混合时长 */
  setMix(fromName, toName, sec) {
    const d = this.data();
    if (!d) return false;
    try {
      d.setMix(fromName, toName, Math.max(0, Math.min(2, sec)));
      return true;
    } catch (e) {
      console.warn('[MixPanel] setMix 失败', fromName, toName, e);
      return false;
    }
  }

  /** 清空所有自定义 mix，回到 defaultMix */
  resetAll() {
    const d = this.data();
    if (!d) return false;
    try {
      d.animationToMixTime = {};
      return true;
    } catch (e) {
      return false;
    }
  }

  /** 当前自定义的 mix 条目数 */
  customCount() {
    const d = this.data();
    if (!d || !d.animationToMixTime) return 0;
    return Object.keys(d.animationToMixTime).length;
  }

  /** 列出所有已自定义的 mix */
  customList() {
    const d = this.data();
    if (!d || !d.animationToMixTime) return [];
    const out = [];
    for (const [k, v] of Object.entries(d.animationToMixTime)) {
      // key 格式 "from.to"
      const i = k.indexOf('.');
      out.push({
        from: i >= 0 ? k.slice(0, i) : k,
        to: i >= 0 ? k.slice(i + 1) : '',
        dur: v,
      });
    }
    return out.sort((a, b) => (a.from + a.to).localeCompare(b.from + b.to));
  }

  /** 创建/刷新面板 */
  mount(host) {
    if (!this.panel) {
      this.panel = document.createElement('div');
      this.panel.className = 'mx-panel';
      this.panel.innerHTML = `
        <div class="mx-head">
          <span class="mx-title">动画混合</span>
          <span class="mx-count" id="mx-count"></span>
          <button class="mx-close" id="mx-close">✕</button>
        </div>
        <div class="mx-body">
          <div class="mx-row">
            <label class="mx-label">默认混合</label>
            <input type="range" class="mx-range" id="mx-default" min="0" max="1" step="0.01">
            <span class="mx-val" id="mx-default-val">0.25s</span>
          </div>
          <div class="mx-sep">单对覆盖</div>
          <div class="mx-row">
            <select class="mx-select" id="mx-from"></select>
            <span class="mx-arrow">→</span>
            <select class="mx-select" id="mx-to"></select>
          </div>
          <div class="mx-row">
            <input type="range" class="mx-range" id="mx-pair" min="0" max="1" step="0.01">
            <span class="mx-val" id="mx-pair-val">—</span>
          </div>
          <div class="mx-row mx-actions">
            <button class="mx-btn" id="mx-apply">应用</button>
            <button class="mx-btn" id="mx-test">试播</button>
            <button class="mx-btn" id="mx-reset">清空覆盖</button>
          </div>
          <div class="mx-list" id="mx-list"></div>
        </div>`;
      host.appendChild(this.panel);

      this.panel.querySelector('#mx-close').onclick = () => this.hide();

      const defRange = this.panel.querySelector('#mx-default');
      const defVal = this.panel.querySelector('#mx-default-val');
      defRange.addEventListener('input', () => {
        const v = parseFloat(defRange.value);
        this.setDefault(v);
        defVal.textContent = v.toFixed(2) + 's';
      });

      const fromSel = this.panel.querySelector('#mx-from');
      const toSel = this.panel.querySelector('#mx-to');
      const pairRange = this.panel.querySelector('#mx-pair');
      const pairVal = this.panel.querySelector('#mx-pair-val');

      const syncPairVal = () => {
        const cur = this.getMix(fromSel.value, toSel.value);
        if (cur === null) {
          pairVal.textContent = '—';
        } else {
          pairRange.value = cur;
          pairVal.textContent = cur.toFixed(2) + 's';
        }
      };
      fromSel.onchange = syncPairVal;
      toSel.onchange = syncPairVal;
      pairRange.addEventListener('input', () => {
        pairVal.textContent = parseFloat(pairRange.value).toFixed(2) + 's';
      });

      this.panel.querySelector('#mx-apply').onclick = () => {
        const ok = this.setMix(fromSel.value, toSel.value, parseFloat(pairRange.value));
        this.onStatus(ok
          ? `混合 ${fromSel.value} → ${toSel.value} = ${parseFloat(pairRange.value).toFixed(2)}s`
          : '设置失败');
        this.refresh();
      };

      this.panel.querySelector('#mx-test').onclick = () => {
        // 按设定时长试播：先切 from，再排队 to
        try {
          const st = this.player.animationState;
          st.setAnimation(0, fromSel.value, true);
          st.addAnimation(0, toSel.value, true, 0);
          this.player.play();
          this.onStatus(`试播 ${fromSel.value} → ${toSel.value}`);
        } catch (e) {
          this.onStatus('试播失败: ' + e.message);
        }
      };

      this.panel.querySelector('#mx-reset').onclick = () => {
        this.resetAll();
        this.onStatus('已清空所有单对覆盖');
        this.refresh();
      };
    }

    this.anims = this._listAnims();
    const fromSel = this.panel.querySelector('#mx-from');
    const toSel = this.panel.querySelector('#mx-to');
    const opts = this.anims.map(a => `<option value="${a}">${a}</option>`).join('');
    fromSel.innerHTML = opts;
    toSel.innerHTML = opts;
    // 默认选 idle → 第一个非 idle 动画
    const idleIdx = this.anims.findIndex(a => /idle/i.test(a));
    if (idleIdx >= 0) fromSel.selectedIndex = idleIdx;
    const otherIdx = this.anims.findIndex((a, i) => i !== idleIdx);
    if (otherIdx >= 0) toSel.selectedIndex = otherIdx;

    this.refresh();
    return this.panel;
  }

  refresh() {
    if (!this.panel) return;
    const d = this.getDefault();
    const defRange = this.panel.querySelector('#mx-default');
    const defVal = this.panel.querySelector('#mx-default-val');
    if (defRange) defRange.value = d;
    if (defVal) defVal.textContent = d.toFixed(2) + 's';

    const cnt = this.panel.querySelector('#mx-count');
    if (cnt) {
      const n = this.customCount();
      cnt.textContent = n ? `${n} 条覆盖` : '默认';
    }

    const list = this.panel.querySelector('#mx-list');
    if (list) {
      const customs = this.customList();
      if (!customs.length) {
        list.innerHTML = '';
      } else {
        list.innerHTML = customs.map(c =>
          `<div class="mx-item"><span>${c.from} → ${c.to}</span>` +
          `<b>${c.dur.toFixed(2)}s</b></div>`).join('');
      }
    }

    // 同步单对滑块的当前值
    const fromSel = this.panel.querySelector('#mx-from');
    const toSel = this.panel.querySelector('#mx-to');
    const pairRange = this.panel.querySelector('#mx-pair');
    const pairVal = this.panel.querySelector('#mx-pair-val');
    if (fromSel && toSel && pairRange && pairVal) {
      const cur = this.getMix(fromSel.value, toSel.value);
      if (cur !== null) {
        pairRange.value = cur;
        pairVal.textContent = cur.toFixed(2) + 's';
      }
    }
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
  window.MixPanel = MixPanel;
}
