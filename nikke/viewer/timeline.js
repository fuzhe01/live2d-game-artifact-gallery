/**
 * 时间轴 scrub + 帧步进。
 *
 * 文档 §4.4：拖动时间轴到任意帧、逐帧前后步进 —— 截图/研究动画曲线的刚需。
 *
 * 实现要点：
 *   - spine-player 的 AnimationState 每帧由 update(delta) 驱动。
 *     要定格，需把 track.timeScale 设为 0，并手动设置 track.time。
 *   - 恢复播放时把 timeScale 设回 1。
 *   - 帧率取 30fps（游戏常见），步进 1/30 秒。
 *
 * 注意：spine-player 内部自己调 animationState.update()，
 * 所以 timeScale=0 时 track.time 不会被自动推进，手动赋值即可定格。
 */
class TimelineScrubber {
  constructor(opts = {}) {
    this.player = null;
    this.fps = opts.fps || 30;
    this.trackIndex = 0;
    this.panel = null;
    this.scrubbing = false;
    this.savedTimeScale = 1;
    this.onStatus = opts.onStatus || (() => {});
  }

  attach(player) {
    this.player = player;
    this.savedTimeScale = 1;
    this.refresh();
  }

  /** 当前轨迹可用的时长（秒） */
  duration() {
    try {
      const st = this.player.animationState;
      const e = st.getCurrent(this.trackIndex);
      if (!e) return 0;
      const anim = e.animation;
      return anim.duration || 0;
    } catch (e) {
      return 0;
    }
  }

  currentTime() {
    try {
      const st = this.player.animationState;
      const e = st.getCurrent(this.trackIndex);
      if (!e) return 0;
      const dur = e.animation.duration || 0;
      // 循环动画的 trackTime 会一直累加超过 duration，
      // 取模映射回 [0, dur) 才能正确显示滑块位置。
      if (dur > 0 && e.trackTime > dur) {
        return ((e.trackTime - (e.animationStart || 0)) % dur + dur) % dur;
      }
      return e.trackTime;
    } catch (e) {
      return 0;
    }
  }

  currentName() {
    try {
      const e = this.player.animationState.getCurrent(this.trackIndex);
      return e ? e.animation.name : '';
    } catch (e) {
      return '';
    }
  }

  isPaused() {
    try {
      const e = this.player.animationState.getCurrent(this.trackIndex);
      return e ? e.timeScale === 0 : false;
    } catch (e) {
      return false;
    }
  }

  /**
   * 定格到指定秒。
   *
   * 关键时序（实测）：
   *   setAnimation() 之后【立即】seek 是无效的 —— trackEntry 尚未被 apply 过，
   *   骨架还停留在上一个动画的姿势。必须等至少 1 帧（requestAnimationFrame）
   *   让 spine-player 的渲染循环跑一次 apply，seek 才会真正改变姿势。
   *
   *   验证数据：
   *     setAnimation 后立即 seek  → 各时间点姿势指纹完全相同（无效）
   *     setAnimation 后等 2 帧    → 各时间点姿势指纹各不相同（有效）
   */
  seek(sec) {
    try {
      const st = this.player.animationState;
      const sk = this.player.skeleton;
      const e = st.getCurrent(this.trackIndex);
      if (!e) return false;
      const dur = e.animation.duration || 0;
      sec = Math.max(0, Math.min(sec, dur));
      e.timeScale = 0;         // 冻结自动推进
      e.trackTime = sec;       // 直接跳到目标时间
      // 循环动画若之前已累加超过 duration，重置 animationStart 让取模从本次开始
      if (typeof e.animationStart !== 'undefined') {
        e.animationStart = 0;
      }
      st.apply(sk);            // 应用时间轴到骨架
      sk.updateWorldTransform();
      this.refresh();
      return true;
    } catch (err) {
      console.warn('[Scrubber] seek 失败', err);
      return false;
    }
  }

  /**
   * 安全 seek：先确保 trackEntry 已被 apply 过。
   * 用于「刚切换动画后马上要定格」的场景（例如从动作按钮切到某动画再拖时间轴）。
   */
  seekAfterFrame(sec, cb) {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const ok = this.seek(sec);
        if (typeof cb === 'function') cb(ok);
      });
    });
  }

  /** 逐步进 N 帧 */
  step(frames) {
    const t = this.currentTime() + frames / this.fps;
    return this.seek(t);
  }

  /** 恢复播放 */
  resume() {
    try {
      const e = this.player.animationState.getCurrent(this.trackIndex);
      if (!e) return false;
      e.timeScale = this.savedTimeScale || 1;
      this.player.play();
      this.refresh();
      return true;
    } catch (err) {
      return false;
    }
  }

  /** 回到起点并恢复播放 */
  reset() {
    this.seek(0);
    return this.resume();
  }

  /** 创建/刷新面板 */
  mount(host) {
    if (!this.panel) {
      this.panel = document.createElement('div');
      this.panel.className = 'tl-panel';
      this.panel.innerHTML = `
        <div class="tl-row">
          <span class="tl-anim" id="tl-anim">—</span>
          <span class="tl-time" id="tl-time">0.00 / 0.00s</span>
        </div>
        <input type="range" class="tl-range" id="tl-range" min="0" max="1" step="0.001" value="0">
        <div class="tl-row tl-btns">
          <button class="tl-btn" id="tl-prev" title="后退一帧">◀|</button>
          <button class="tl-btn" id="tl-play" title="播放/暂停">▶/❚❚</button>
          <button class="tl-btn" id="tl-next" title="前进一帧">|▶</button>
          <button class="tl-btn" id="tl-reset" title="回到起点">↺</button>
          <span class="tl-hint" id="tl-hint"></span>
        </div>`;
      host.appendChild(this.panel);

      const range = this.panel.querySelector('#tl-range');
      // input 事件在拖动过程中持续触发
      range.addEventListener('input', (e) => {
        const dur = this.duration();
        if (dur <= 0) return;
        this.scrubbing = true;
        this.seek(parseFloat(e.target.value) * dur);
      });
      range.addEventListener('change', () => { this.scrubbing = false; });

      this.panel.querySelector('#tl-prev').onclick = () => this.step(-1);
      this.panel.querySelector('#tl-next').onclick = () => this.step(1);
      this.panel.querySelector('#tl-play').onclick = () => {
        if (this.isPaused()) this.resume();
        else this.seek(this.currentTime());   // 已是冻结态，保持
      };
      this.panel.querySelector('#tl-reset').onclick = () => this.reset();
    }
    this.refresh();
    return this.panel;
  }

  /** 更新 UI（在 RAF 中周期调用） */
  refresh() {
    if (!this.panel) return;
    const dur = this.duration();
    const cur = this.currentTime();
    const name = this.currentName();
    const paused = this.isPaused();

    const animEl = this.panel.querySelector('#tl-anim');
    const timeEl = this.panel.querySelector('#tl-time');
    const range = this.panel.querySelector('#tl-range');
    const hint = this.panel.querySelector('#tl-hint');

    if (animEl) animEl.textContent = name || '—';
    if (timeEl) {
      // 循环动画显示取模后的时间，避免出现 "5.00 / 3.33s" 这种越界显示
      timeEl.textContent = `${cur.toFixed(2)} / ${dur.toFixed(2)}s`;
    }
    if (range && !this.scrubbing) {
      range.max = Math.max(dur, 0.001);
      range.value = Math.min(cur, dur);
    }
    if (hint) {
      const frame = Math.round(cur * this.fps);
      hint.textContent = paused ? `已定格 · 帧 ${frame}` : `${this.fps}fps`;
    }
    this.panel.classList.toggle('paused', paused);
  }

  /** 启动周期性 UI 刷新（播放时同步滑块） */
  startAutoRefresh() {
    if (this._afId) return;
    const tick = () => {
      if (this.panel && this.panel.classList.contains('on') && !this.isPaused()) {
        this.refresh();
      }
      this._afId = requestAnimationFrame(tick);
    };
    this._afId = requestAnimationFrame(tick);
  }

  stopAutoRefresh() {
    if (this._afId) {
      cancelAnimationFrame(this._afId);
      this._afId = null;
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
  window.TimelineScrubber = TimelineScrubber;
}
