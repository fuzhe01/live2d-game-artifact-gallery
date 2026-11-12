/**
 * 三姿态交互模块（文档 §2 + §3.4）。
 *
 * 实测动画清单（审核 + 我独立验证，400/400 角色一致）：
 *   fb    : idle / talk_start / talk_end / action / expression_0 / 大量表情
 *   cover : cover_idle / cover_reload / cover_hit / cover_stun / to_aim / to_cover
 *   aim   : aim_idle / aim_fire / aim_hit / aim_x / aim_y / to_aim / to_cover
 *
 * 各姿态提供不同的交互：
 *   fb    → 大厅互动（触摸台词，已由 interaction.js 负责）
 *   cover → 换弹动作序列（cover_reload_start → loop → end，或单段 cover_reload）
 *   aim   → 射击（aim_fire）+ 瞄准方向（aim_x / aim_y 骨骼偏移）
 *
 * 射击抖动（§3.4）：
 *   aim 姿态下转动鼠标 → 改 aim_x / aim_y 骨骼的 x/y，形成跟手瞄准；
 *   开火时叠加一个短促的后坐力偏移。这是纯参数操作，成本低但手感差异大。
 */
class PoseActions {
  constructor(opts = {}) {
    this.player = null;
    this.getPose = opts.getPose || (() => 'fb');
    this.onStatus = opts.onStatus || (() => {});
    this.panel = null;

    this.anims = [];
    this.hasReload = false;
    this.hasFire = false;
    this.hasAimAxis = false;

    // 射击抖动参数（可调）
    this.aimSensitivity = 1.0;     // 瞄准跟随灵敏度
    this.recoilStrength = 1.0;     // 后坐力强度
    this.aimRange = 120;           // 瞄准最大偏移（skel 单位）

    // 骨骼引用
    this.boneX = null;
    this.boneY = null;
    this.baseX = { x: 0, y: 0 };
    this.baseY = { x: 0, y: 0 };
    this.offsetX = 0;
    this.offsetY = 0;
    this.recoil = 0;
    // 记录「上一次叠加了多少」，用于下次先减回去恢复原始值
    this._appliedX = 0;
    this._appliedY = 0;
    this._appliedBX = 0;
    this._appliedBY = 0;
    this._rafId = null;
    this._mouseBound = false;
  }

  attach(player) {
    // 换 player（切姿态/切皮肤）时先收掉上一轮的连射定时器，避免泄漏
    if (this._fireKickTimer) { clearInterval(this._fireKickTimer); this._fireKickTimer = null; }
    this._firing = false;
    this.player = player;
    this.anims = this._listAnims();
    this.hasReload = this.anims.includes('cover_reload');
    this.hasFire = this.anims.includes('aim_fire');
    this._findAimBones();
    this.offsetX = 0;
    this.offsetY = 0;
    this.recoil = 0;
    this._snapX = null;
    this._snapY = null;
    this._installHook();
    // 快照必须在动画真正 apply 过之后拍，否则拿到的是 attach 瞬间的残留值。
    // 等两帧让渲染循环先跑完 apply，再记录纯净的动画基准。
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (this.player !== player) return;   // 期间已换 player
      this._snapX = this.boneX ? { x: this.boneX.x, y: this.boneX.y } : null;
      this._snapY = this.boneY ? { x: this.boneY.x, y: this.boneY.y } : null;
      // 模式 B：采样 aim_x/aim_y 动画的位移量（无同名骨骼的角色靠它做瞄准）
      if (!this.boneX && !this.boneY) this._sampleAimDelta();
    }));
    this.refresh();
  }

  _listAnims() {
    try {
      return this.player.animationState.data.skeletonData.animations.map(a => a.name);
    } catch (e) {
      return [];
    }
  }

  /**
   * 找瞄准控制源。
   *
   * ⚠️ 重要纠正（2026-11-12 实测）：
   *   aim_x / aim_y 【主要】是**动画名**，不是骨骼名。
   *   全库 401 个有 aim 姿态的条目里，只有 c010 系（4 个）骨骼表里恰好有
   *   同名的 aim_x / aim_y 骨骼；c011/c012/c016 等都没有。
   *   旧实现只 find 骨骼 → hasAimAxis 恒 false → 71% 角色的瞄准功能完全不生效。
   *
   * 正确做法：优先用骨骼（若能找到，可直接改坐标，性能好）；
   *           否则退回播放 aim_x / aim_y 动画，用 trackTime 控制偏移量。
   */
  _findAimBones() {
    this.boneX = null;
    this.boneY = null;
    this.aimAnimX = null;
    this.aimAnimY = null;
    this.hasAimBone = false;
    this.hasAimAnim = false;
    try {
      const sk = this.player.skeleton;
      const sd = this.player.animationState.data.skeletonData;
      const find = (n) => sk.bones.find(b => b.data && b.data.name === n);
      this.boneX = find('aim_x');
      this.boneY = find('aim_y');
      if (this.boneX) {
        this.baseX = { x: this.boneX.x, y: this.boneX.y };
      }
      if (this.boneY) {
        this.baseY = { x: this.boneY.x, y: this.boneY.y };
      }
      this.hasAimBone = !!(this.boneX || this.boneY);

      // 动画回退：aim_x / aim_y 动画驱动整套瞄准偏移
      const fa = (n) => { try { return sd.findAnimation(n); } catch (e) { return null; } };
      this.aimAnimX = fa('aim_x');
      this.aimAnimY = fa('aim_y');
      this.hasAimAnim = !!(this.aimAnimX || this.aimAnimY);
    } catch (e) {
      this.hasAimBone = false;
      this.hasAimAnim = false;
    }
    // 任一可用即视为支持瞄准
    this.hasAimAxis = this.hasAimBone || this.hasAimAnim;
    this.aimMode = this.hasAimBone ? 'bone' : (this.hasAimAnim ? 'anim' : 'none');
  }

  /** 当前姿态下可用的动作 */
  actions() {
    const pose = this.getPose();
    const out = [];
    if (pose === 'cover') {
      if (this.anims.includes('cover_reload')) out.push({ key: 'reload', label: '换弹' });
      if (this.anims.includes('cover_hit')) out.push({ key: 'coverhit', label: '受击' });
      if (this.anims.includes('cover_stun')) out.push({ key: 'stun', label: '眩晕' });
    } else if (pose === 'aim') {
      if (this.anims.includes('aim_fire')) out.push({ key: 'fire', label: '开火' });
      if (this.anims.includes('aim_hit')) out.push({ key: 'aimhit', label: '受击' });
    } else {
      if (this.anims.includes('action')) out.push({ key: 'action', label: '动作' });
      if (this.anims.includes('special')) out.push({ key: 'special', label: '特写' });
    }
    return out;
  }

  /** 触发某个动作。fire 是持续循环开关：再点一次（或 stopFire）停止。 */
  trigger(key) {
    if (!this.player) return false;
    // ④ aim_fire 设为循环动作：第一次点开始连射，第二次点回到 aim_idle。
    if (key === 'fire') {
      if (this._firing) return this.stopFire();
      return this.startFire();
    }
    const idle = this._idleForPose();
    const map = {
      reload: 'cover_reload',
      coverhit: 'cover_hit',
      stun: 'cover_stun',
      aimhit: 'aim_hit',
      action: 'action',
      special: 'special',
    };
    const anim = map[key];
    if (!anim || !this.anims.includes(anim)) return false;

    try {
      const st = this.player.animationState;
      this.player.setAnimation(anim, false);
      // 播完接本姿态 idle（delay 用真实时长，避免被提前顶掉）
      if (idle) {
        let dur = 0.2;
        try {
          const e = st.getCurrent(0);
          if (e && e.animation && e.animation.duration) {
            dur = e.animation.duration;
          } else {
            const found = st.data.skeletonData.findAnimation(anim);
            if (found && found.duration) dur = found.duration;
          }
        } catch (e) {}

        // 极短动作（实测 aim_fire 仅 0.1s、aim_hit 0.167s）单播几乎看不见，
        // 按 repeat 次数连排，让用户能看清。
        // ⚠️ delay 必须【累加】而不是都传 0 —— 传 0 会让所有 addAnimation 同时入队，
        // spine 只保留最后一个，实测 aim_fire 只出现 1 次 42ms（应为 3 次）。
        const animDur = (() => {
          try {
            const a = st.data.skeletonData.findAnimation(anim);
            return a && a.duration ? a.duration : 0.1;
          } catch (e) { return 0.1; }
        })();

        let repeat = 1;
        if (key === 'aimhit') repeat = 2;
        else if (key === 'coverhit') repeat = 2;

        for (let i = 1; i < repeat; i++) {
          try { this.player.addAnimation(anim, false, animDur * i); } catch (e) {}
        }
        // idle 排在所有连发之后
        try {
          this.player.addAnimation(idle, true, animDur * (repeat - 1) + dur);
        } catch (e) {}
      }
      this.player.play();

      // 记录触发痕迹。有些动作极短（实测 aim_hit 只有 0.167s），
      // 外部采样很容易错过，靠这个 lastFired 才能确认确实执行过。
      this.lastFired = {
        key, anim, at: performance.now(),
        duration: (() => {
          try {
            const a = st.data.skeletonData.findAnimation(anim);
            return a ? a.duration : null;
          } catch (e) { return null; }
        })(),
      };
      this.fireCount = (this.fireCount || 0) + 1;

      this.onStatus(`▶ ${anim}`);
      return true;
    } catch (e) {
      console.warn('[PoseActions] 动作失败', anim, e);
      return false;
    }
  }

  /**
   * ④ aim_fire 循环开火。
   * 游戏内是「按住开火」，画廊里用开关：点一次持续连射，再点一次停。
   */
  startFire() {
    if (!this.player || !this.anims.includes('aim_fire')) return false;
    try {
      this._firing = true;
      this.player.setAnimation('aim_fire', true);   // loop=true
      this.player.play();
      this.fireCount = (this.fireCount || 0) + 1;
      this.lastFired = { key: 'fire', anim: 'aim_fire', loop: true,
                         at: performance.now() };
      this.onStatus('▶ 连射中（再点「开火」停止）');
      // 每帧循环一次后坐力，形成持续射击的抖动
      this._fireKickTimer = setInterval(() => { if (this._firing) this.kick(0.5); }, 150);
      return true;
    } catch (e) {
      this._firing = false;
      return false;
    }
  }

  stopFire() {
    this._firing = false;
    if (this._fireKickTimer) { clearInterval(this._fireKickTimer); this._fireKickTimer = null; }
    const idle = this._idleForPose();
    try {
      if (idle) this.player.setAnimation(idle, true);
      this.player.play();
    } catch (e) {}
    this.recoil = 0;
    this.onStatus('■ 停止连射');
    return false;   // 让 UI 取消 toggle 高亮
  }

  _idleForPose() {
    const pose = this.getPose();
    const want = { cover: 'cover_idle', aim: 'aim_idle', fb: 'idle' }[pose] || 'idle';
    if (this.anims.includes(want)) return want;
    return this.anims.find(a => /idle/i.test(a)) || null;
  }

  /** 瞄准：设置偏移（-1..1 归一化） */
  setAim(nx, ny) {
    if (!this.hasAimAxis) return false;
    const r = this.aimRange;
    this.offsetX = Math.max(-1, Math.min(1, nx)) * r;
    this.offsetY = Math.max(-1, Math.min(1, ny)) * r;
    this._applyAim();
    return true;
  }

  /** 开火后坐力 */
  kick(strength) {
    if (!this.hasAimAxis) return false;
    this.recoil = (strength || 1) * 22 * this.recoilStrength;
    if (!this._rafId) this._startRecoilDecay();
    return true;
  }

  _startRecoilDecay() {
    const tick = () => {
      if (Math.abs(this.recoil) < 0.5) {
        this.recoil = 0;
        this._applyAim();
        this._rafId = null;
        return;
      }
      this.recoil *= 0.82;
      this._applyAim();
      this._rafId = requestAnimationFrame(tick);
    };
    this._rafId = requestAnimationFrame(tick);
  }

  /** 装上 apply hook：每帧 apply 之后叠加瞄准/后坐力偏移。
   *
   * 为什么必须用 hook：
   *   aim 姿态下 aim_idle 动画本身每帧都在改写 aim_x / aim_y 骨骼，
   *   手动设一次 x/y 会在下一帧被动画覆盖回去（和配件显隐同一个坑）。
   *   在 apply 之后统一叠加偏移，才能稳定压过动画。
   */
  _installHook() {
    if (!this.player) return;
    const st = this.player.animationState;
    if (this._hookedState === st) return;

    // 换 player：清理旧 hook（旧 player 已被 dispose，无需还原）
    const hook = {
      origApply: st.apply.bind(st),
      self: this,
    };
    st.apply = function (...args) {
      const r = hook.origApply(...args);
      const self = hook.self;
      if (self && self.player === self._hookedPlayer
          && (self.offsetX || self.offsetY || self.recoil)) {
        // 标记：此刻骨骼是刚 apply 完的纯净值，overlay 可直接取基准
        self._inHook = true;
        try { self._overlay(); } finally { self._inHook = false; }
      }
      return r;
    };
    this._hookedState = st;
    this._hookedPlayer = this.player;
    this._hookRef = hook;
  }

  /**
   * 叠加瞄准/后坐力偏移。
   *
   * ⚠️ 踩了五次坑，最终方案（每次都在骨骼的「纯净动画值」上做一次加法）：
   *
   *   aim_y 不在任何 timeline 里 → animationState.apply() 不会重置它
   *   → 必须自己保存快照并在每次叠加前还原
   *
   *   而 aim_x 虽在某些 timeline 里，但【双 hook 场景下】extrasHook 会先跑，
   *   我在 _inHook 分支拿 bx 刷新 _snapX 时会拿到上一帧的脏值
   *   → 实测漂移 ±5.7 单位（审核报的 boneDrift）
   *
   *   修法：_snapX / _snapY 只在 attach() 时拍一次，之后【永不更新】。
   *   aim 姿态下这两根骨骼的动画值本来就不该随时间变（aim_idle 不改它们）。
   */
  _overlay() {
    try {
      const sk = this.player.skeleton;
      if (!sk) return;

      // ── 模式 A：有同名骨骼（c010 系）—— 直接改坐标，最精确 ──
      const bx = this.boneX, by = this.boneY;
      if (bx || by) {
        const baseX = this._snapX;
        const baseY = this._snapY;
        if (!baseX && !baseY) return;
        if (bx && baseX) {
          bx.x = baseX.x + this.offsetX;
          bx.y = baseX.y + this.offsetY - this.recoil;
        }
        if (by && baseY) {
          by.x = baseY.x + this.offsetX * 0.4;
          by.y = baseY.y + this.offsetY * 0.4;
        }
        sk.updateWorldTransform();
        return;
      }

      // ── 模式 B：没有同名骨骼（约 71% 角色）──
      // 用 aim_x / aim_y 动画在 t=0 与 t=duration 的骨骼位移差做线性插值。
      // 差值在 attach 时采样一次（_aimDeltaX/_aimDeltaY），这里是纯加法。
      if (!this._aimDeltaX && !this._aimDeltaY) return;
      const dx = this._aimDeltaX || {};
      const dy = this._aimDeltaY || {};
      // ⚠️ offsetX/offsetY 是「已乘 aimRange(=120) 的 skel 单位」，
      // 不是归一化值。必须先除回 aimRange 才是 -1..1 的插值系数。
      // 之前直接 clamp(offsetX, -1, 1) 会让任何 |nx|>0.0083 都饱和成 ±1，
      // 「按鼠标量插值」退化成二值（审核实测 setAim(0.5,..) 与 setAim(1,..) 同幅）。
      const r = this.aimRange || 120;
      const t = Math.max(-1, Math.min(1, this.offsetX / r));
      const u = Math.max(-1, Math.min(1, this.offsetY / r));

      // ⚠️ dx 和 dy 可能有重叠骨骼（审核实测 c011 重叠 21 根）。
      // 不能「先 applyMap(dx) 再 applyMap(dy)」——后者会直接赋值覆盖前者的贡献。
      // 正确做法：先把两个动画的位移【累加】到同一张表，再一次性写回骨骼。
      // recoil 也只加一次（放在最终写入时），不能每张表都加。
      const delta = {};   // name -> {dx, dy}
      const add = (map, sx, sy) => {
        for (const name in map) {
          const e = delta[name] || (delta[name] = { dx: 0, dy: 0 });
          e.dx += map[name].dx * sx;
          e.dy += map[name].dy * sy;
        }
      };
      add(dx, t, u);
      add(dy, t * 0.4, u * 0.4);

      let applied = 0;
      for (const name in delta) {
        const b = sk.findBone ? sk.findBone(name)
                : sk.bones.find(x => x.data && x.data.name === name);
        const base = this._aimBase ? this._aimBase[name] : null;
        if (!b || !base) continue;
        b.x = base.x + delta[name].dx;
        b.y = base.y + delta[name].dy - this.recoil * 0.3;
        applied++;
      }
      this._appliedBX = applied;
      sk.updateWorldTransform();
    } catch (e) {}
  }

  /**
   * 采样 aim_x / aim_y 动画的位移量（模式 B 用）。
   *
   * 原理：把动画在 t=0 和 t=duration 应用到骨架上，记录各骨骼的坐标差。
   * 这个差就是「瞄准到最大偏移」时每根骨骼该移动多少。
   * 采样后立刻把骨架还原到采样前的状态，不影响正在播放的动画。
   */
  _sampleAimDelta() {
    this._aimDeltaX = null;
    this._aimDeltaY = null;
    this._aimBase = null;
    if (this.boneX || this.boneY) return;      // 模式 A 不需要
    try {
      const pl = this.player;
      const sk = pl.skeleton;
      const sd = pl.animationState.data.skeletonData;

      // 记录采样前所有骨骼的局部变换
      const before = {};
      for (const b of sk.bones) {
        before[b.data.name] = {
          x: b.x, y: b.y,
          rotation: b.rotation, scaleX: b.scaleX, scaleY: b.scaleY,
        };
      }

      const sample = (anim) => {
        if (!anim) return null;
        const res = {};
        // ⚠️ blend 参数：0=Setup(重置为该帧绝对值) 1=Current(在当前值上叠加) 2=Multiply
        // 采样要用 blend=0（拿动画的绝对坐标），否则叠加会失真。
        const applyAt = (t) => {
          for (const b of sk.bones) {
            const b0 = before[b.data.name];
            b.x = b0.x; b.y = b0.y;
            b.rotation = b0.rotation; b.scaleX = b0.scaleX; b.scaleY = b0.scaleY;
          }
          try { anim.apply(sk, 0, t, false, null, 1, 0, 1); } catch (e) {}
        };
        applyAt(0);
        const t0 = {};
        for (const b of sk.bones) t0[b.data.name] = { x: b.x, y: b.y };
        applyAt(anim.duration);
        for (const b of sk.bones) {
          const n = b.data.name;
          const dX = b.x - t0[n].x;
          const dY = b.y - t0[n].y;
          if (Math.abs(dX) > 0.5 || Math.abs(dY) > 0.5) {
            res[n] = { dx: dX, dy: dY };
          }
        }
        return res;
      };

      const dx = sample(this.aimAnimX);
      const dy = sample(this.aimAnimY);

      // 还原
      for (const b of sk.bones) { b.x = before[b.data.name].x; b.y = before[b.data.name].y; }

      this._aimBase = before;
      this._aimDeltaX = dx && Object.keys(dx).length ? dx : null;
      this._aimDeltaY = dy && Object.keys(dy).length ? dy : null;
    } catch (e) {
      this._aimDeltaX = null;
      this._aimDeltaY = null;
    }
  }

  /** 立即应用一次（不等下一帧） */
  _applyAim() {
    try { this._overlay(); } catch (e) {}
  }

  resetAim() {
    this.offsetX = 0;
    this.offsetY = 0;
    this.recoil = 0;
    // 偏移归零后 overlay 会把骨骼还原成「基准 + 0」= 动画原始值
    try { this._overlay(); } catch (e) {}
  }

  /** 绑定鼠标跟随瞄准（只在 aim 姿态生效） */
  bindMouse(hostEl) {
    if (this._mouseBound || !hostEl) return;
    this._mouseBound = true;
    hostEl.addEventListener('pointermove', (e) => {
      if (this.getPose() !== 'aim') return;
      if (!this.hasAimAxis) return;
      const r = hostEl.getBoundingClientRect();
      if (!r.width || !r.height) return;
      // 归一化到 -1..1
      const nx = ((e.clientX - r.left) / r.width) * 2 - 1;
      const ny = ((e.clientY - r.top) / r.height) * 2 - 1;
      this.setAim(nx * this.aimSensitivity, ny * this.aimSensitivity);
    });
    // 离开画布时回中
    hostEl.addEventListener('pointerleave', () => {
      if (this.getPose() === 'aim') this.resetAim();
    });
  }

  /** 创建面板 */
  mount(host) {
    if (!this.panel) {
      this.panel = document.createElement('div');
      this.panel.className = 'pose-panel';
      // 关闭按钮用 <span role="button"> 而不是 <button>，
      // 这样 panel.querySelectorAll('button')[0] 一定是真正的动作按钮，
      // 不会被「关闭」抢到首位（自动化探针踩过这个坑）。
      this.panel.innerHTML = `
        <div class="pose-head">
          <span class="pose-title">姿态动作</span>
          <span class="pose-badge" id="pose-badge">—</span>
          <span class="pose-close" id="pose-close" role="button" tabindex="0">✕</span>
        </div>
        <div class="pose-body">
          <div class="pose-actions" id="pose-actions"></div>
          <div class="pose-sliders" id="pose-sliders"></div>
        </div>`;
      host.appendChild(this.panel);
      const closeEl = this.panel.querySelector('#pose-close');
      closeEl.onclick = () => this.hide();
      closeEl.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') this.hide(); };
    }
    this.rebuild();
    return this.panel;
  }

  /** 按当前姿态重建按钮和滑块 */
  rebuild() {
    if (!this.panel) return;
    const pose = this.getPose();
    const badge = this.panel.querySelector('#pose-badge');
    if (badge) {
      badge.textContent = { fb: '立绘', cover: '掩体', aim: '瞄准' }[pose] || pose;
    }

    const actBox = this.panel.querySelector('#pose-actions');
    if (actBox) {
      const acts = this.actions();
      actBox.innerHTML = acts.length
        ? acts.map(a => `<button class="pose-btn" data-k="${a.key}">${a.label}</button>`).join('')
        : '<span class="pose-none">该姿态无动作</span>';
      actBox.querySelectorAll('.pose-btn').forEach(btn => {
        btn.onclick = () => {
          const r = this.trigger(btn.dataset.k);
          // fire 是循环开关：startFire 返回 true 高亮，stopFire 返回 false 取消
          if (btn.dataset.k === 'fire') btn.classList.toggle('on', !!r);
        };
        if (btn.dataset.k === 'fire' && this._firing) btn.classList.add('on');
      });
    }

    const slBox = this.panel.querySelector('#pose-sliders');
    if (slBox) {
      if (pose === 'aim' && this.hasAimAxis) {
        slBox.innerHTML = `
          <div class="pose-row">
            <label class="pose-label">灵敏度</label>
            <input type="range" class="pose-range" id="pose-sens" min="0" max="3" step="0.1" value="${this.aimSensitivity}">
            <span class="pose-val" id="pose-sens-val">${this.aimSensitivity.toFixed(1)}</span>
          </div>
          <div class="pose-row">
            <label class="pose-label">后坐力</label>
            <input type="range" class="pose-range" id="pose-recoil" min="0" max="3" step="0.1" value="${this.recoilStrength}">
            <span class="pose-val" id="pose-recoil-val">${this.recoilStrength.toFixed(1)}</span>
          </div>
          <div class="pose-row">
            <button class="pose-btn wide" id="pose-center">回中</button>
          </div>
          <div class="pose-hint">鼠标移到画布上可跟随瞄准</div>`;
        const s1 = slBox.querySelector('#pose-sens');
        const v1 = slBox.querySelector('#pose-sens-val');
        s1.oninput = () => {
          this.aimSensitivity = parseFloat(s1.value);
          v1.textContent = this.aimSensitivity.toFixed(1);
        };
        const s2 = slBox.querySelector('#pose-recoil');
        const v2 = slBox.querySelector('#pose-recoil-val');
        s2.oninput = () => {
          this.recoilStrength = parseFloat(s2.value);
          v2.textContent = this.recoilStrength.toFixed(1);
        };
        slBox.querySelector('#pose-center').onclick = () => this.resetAim();
      } else {
        slBox.innerHTML = '';
      }
    }
    this.refresh();
  }

  refresh() {
    if (!this.panel) return;
    this.panel.classList.toggle('aim-pose', this.getPose() === 'aim');
  }

  show() { if (this.panel) { this.panel.classList.add('on'); this.rebuild(); } }
  hide() { if (this.panel) this.panel.classList.remove('on'); }
  toggle() {
    if (!this.panel) return;
    const on = !this.panel.classList.contains('on');
    this.panel.classList.toggle('on', on);
    if (on) this.rebuild();
  }
}

if (typeof window !== 'undefined') {
  window.PoseActions = PoseActions;
}
