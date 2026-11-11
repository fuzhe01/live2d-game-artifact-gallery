/**
 * 语音全集面板。
 *
 * 数据来源：data/voiceIndex.json（由 export_voice_index.py 从 ja_audio_index.json 生成）
 *   结构: {"c010": {"Lobby_Touch": [{"id":"c010_Lobby_Touch_1","file":"c010_Lobby_Touch_1.wav","len":1.93}, ...], ...}, ...}
 *
 * 设计目标（文档 §4.3）：
 *   把游戏里按条件触发、画廊里无法复现的语音（战斗/礼物/好感度/编队/野外/技能…）
 *   全部列出来，丢掉触发条件保留可听性 —— 这是画廊相对游戏的最大增值。
 */
class VoiceBrowser {
  constructor(opts = {}) {
    this.dataDir = opts.dataDir || '../data';
    this.voiceDir = opts.voiceDir || '../data/voice';
    this.onPlay = opts.onPlay || (() => {});
    this.lipSync = opts.lipSync || null;   // 可选：语音播放时驱动口型
    this.getPlayer = opts.getPlayer || (() => null);
    this.index = null;          // 全量索引
    this.charId = null;
    this.currentAudio = null;
    this.panel = null;
    this._built = false;
  }

  async load() {
    if (this.index) return this.index;
    try {
      const r = await fetch(`${this.dataDir}/voiceIndex.json`);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      this.index = await r.json();
    } catch (e) {
      console.warn('[VoiceBrowser] 加载 voiceIndex.json 失败', e);
      this.index = {};
    }
    return this.index;
  }

  /** 切到某角色；返回该角色分类数 / 语音总数 */
  async setCharacter(cid) {
    await this.load();
    this.charId = cid;
    const d = this.index[cid] || null;
    if (!d) return { categories: 0, clips: 0 };
    let clips = 0;
    for (const k in d) clips += d[k].length;
    return { categories: Object.keys(d).length, clips };
  }

  /** 创建 / 刷新面板 DOM（挂到宿主元素） */
  async mount(host) {
    await this.load();
    if (!this.panel) {
      this.panel = document.createElement('div');
      this.panel.className = 'vb-panel';
      this.panel.innerHTML = `
        <div class="vb-head">
          <span class="vb-title">语音全集</span>
          <span class="vb-count" id="vb-count"></span>
          <input class="vb-search" id="vb-search" placeholder="筛选分类/文件名…" autocomplete="off">
          <button class="vb-close" id="vb-close">✕</button>
        </div>
        <div class="vb-body" id="vb-body"></div>`;
      host.appendChild(this.panel);
      this.panel.querySelector('#vb-close').onclick = () => this.hide();
      this.panel.querySelector('#vb-search').oninput = (e) => {
        this._filter = e.target.value.trim().toLowerCase();
        this.render();
      };
    }
    this.render();
    return this.panel;
  }

  show() { if (this.panel) { this.panel.classList.add('on'); this.render(); } }
  hide() { if (this.panel) this.panel.classList.remove('on'); this.stop(); }
  toggle() {
    if (!this.panel) return;
    const willShow = !this.panel.classList.contains('on');
    this.panel.classList.toggle('on', willShow);
    if (willShow) this.render();
    else this.stop();
  }

  /** 渲染当前角色的分类列表 */
  render() {
    if (!this.panel) return;
    const body = this.panel.querySelector('#vb-body');
    const cnt = this.panel.querySelector('#vb-count');
    const d = this.index && this.index[this.charId];

    if (!d) {
      body.innerHTML = '<div class="vb-empty">该角色无语音索引</div>';
      if (cnt) cnt.textContent = '';
      return;
    }

    const filter = this._filter || '';
    // 分类排序：Lobby 系列优先，其余按名字
    const keys = Object.keys(d).sort((a, b) => {
      const pa = /^Lobby/.test(a) ? 0 : 1;
      const pb = /^Lobby/.test(b) ? 0 : 1;
      if (pa !== pb) return pa - pb;
      return a.localeCompare(b);
    });

    let total = 0;
    const frag = document.createDocumentFragment();
    for (const k of keys) {
      const items = d[k].filter(it =>
        !filter || k.toLowerCase().includes(filter) || it.id.toLowerCase().includes(filter));
      if (!items.length) continue;
      total += items.length;

      const group = document.createElement('div');
      group.className = 'vb-group';
      const h = document.createElement('div');
      h.className = 'vb-ghead';
      h.innerHTML = `<span>${k}</span><b>${items.length}</b>`;
      group.appendChild(h);

      const grid = document.createElement('div');
      grid.className = 'vb-grid';
      for (const it of items) {
        const b = document.createElement('button');
        b.className = 'vb-item';
        b.title = it.id;
        // 显示精简名：去掉角色前缀
        const short = it.id.replace(/^c\d{3}(?:_\d{2})?_?/, '') || it.id;
        b.innerHTML = `<span class="vb-name">${short}</span>` +
                      `<span class="vb-len">${(it.len || 0).toFixed(1)}s</span>`;
        b.onclick = () => this.play(it);
        grid.appendChild(b);
      }
      group.appendChild(grid);
      frag.appendChild(group);
    }

    body.innerHTML = '';
    if (!total) {
      body.innerHTML = '<div class="vb-empty">无匹配语音</div>';
    } else {
      body.appendChild(frag);
    }
    if (cnt) cnt.textContent = `${total} 条 / ${Object.keys(d).length} 类`;
  }

  play(it) {
    this.stop();
    const url = `${this.voiceDir}/${it.file}`;
    const a = new Audio(url);
    a.volume = 0.9;
    a.addEventListener('error', () => {
      this.onPlay({ ok: false, id: it.id, reason: '404' });
    }, { once: true });
    // 口型同步（与互动播放走同一条链路）
    a.addEventListener('playing', () => {
      if (!this.lipSync) return;
      const pl = this.getPlayer();
      if (!pl) return;
      try {
        const anims = pl.animationState.data.skeletonData.animations.map(x => x.name);
        this.lipSync.attach(pl, anims);
        this.lipSync.start(a);
      } catch (e) {}
    }, { once: true });
    a.addEventListener('ended', () => {
      if (this.lipSync) this.lipSync.stop();
      this.onPlay({ ok: true, id: it.id, done: true });
    }, { once: true });
    const p = a.play();
    if (p && p.then) {
      p.then(() => {
        this.onPlay({ ok: true, id: it.id, audio: a });
      }).catch(err => {
        this.onPlay({ ok: false, id: it.id, reason: String(err) });
      });
    }
    this.currentAudio = a;
    // 高亮
    if (this.panel) {
      this.panel.querySelectorAll('.vb-item.playing').forEach(x => x.classList.remove('playing'));
      const btn = [...this.panel.querySelectorAll('.vb-item')]
        .find(x => x.title === it.id);
      if (btn) btn.classList.add('playing');
    }
  }

  stop() {
    if (this.currentAudio) {
      try { this.currentAudio.pause(); } catch (e) {}
      this.currentAudio = null;
    }
    if (this.panel) {
      this.panel.querySelectorAll('.vb-item.playing').forEach(x => x.classList.remove('playing'));
    }
  }
}

if (typeof window !== 'undefined') {
  window.VoiceBrowser = VoiceBrowser;
}
