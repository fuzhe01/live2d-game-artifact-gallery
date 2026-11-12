/**
 * Web Audio RMS 口型同步。
 *
 * 原理：
 *   把 <audio> 的音源接入 AnalyserNode，每帧算 RMS（均方根振幅），
 *   超过阈值 → 播 talk_start；跌破阈值 → 播 talk_end；随后回到 idle。
 *
 * 注意：
 *   - talk_start / talk_end 是【动画名】，不是骨骼名。
 *   - 77% 的 meta 有这两个动画（495/640），其余静默跳过。
 *   - MediaElementSource 对同一 audio 元素只能创建一次，需缓存。
 */
class LipSync {
  constructor(opts = {}) {
    this.player = null;
    this.animations = [];
    this.enabled = opts.enabled !== false;
    // 滞回阈值：超过 up 才算开始说话，跌破 down 才算停。
    // 单阈值会让 talk_start/talk_end 在阈值附近高频抖动（实测 200~300ms 切一次），
    // 视觉上像抽搐。两个阈值中间留出死区即可稳定。
    this.thresholdUp = opts.thresholdUp || 0.035;
    this.thresholdDown = opts.thresholdDown || 0.015;
    // 最小状态保持时长（毫秒）：避免极短的声音碎片也触发切换
    this.minHoldMs = opts.minHoldMs || 120;
    this.ctx = null;
    this.analyser = null;
    this.sourceNode = null;
    this.audioEl = null;
    this.rafId = null;
    this.wasTalking = false;
    this.lastSwitchAt = 0;
    this.rmsBuf = null;
    this._srcByEl = new WeakMap();
    this.onLevel = opts.onLevel || null;   // 回调：(rms, talking)
  }

  /** 绑定 spine-player 与当前骨架动画列表 */
  attach(player, animations) {
    this.player = player;
    this.animations = animations || [];
    this.stop();
    return this.hasTalkAnims();
  }

  hasTalkAnims() {
    return this.animations.includes('talk_start') && this.animations.includes('talk_end');
  }

  /** 确保 AudioContext / Analyser 就绪，并把 audio 元素接进来 */
  _ensureNodes(audioEl) {
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return false;
      this.ctx = new AC();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 256;
      this.analyser.smoothingTimeConstant = 0.3;
      this.rmsBuf = new Float32Array(this.analyser.fftSize);
    }
    if (this.ctx.state === 'suspended') {
      this.ctx.resume().catch(() => {});
    }
    if (audioEl && this.audioEl !== audioEl) {
      // 同一元素只允许创建一次 MediaElementSource
      let node = this._srcByEl.get(audioEl);
      if (!node) {
        try {
          node = this.ctx.createMediaElementSource(audioEl);
          this._srcByEl.set(audioEl, node);
        } catch (e) {
          // 已被其他 context 占用等情况：跳过口型，避免打断播放
          return false;
        }
      }
      try {
        node.connect(this.analyser);
        node.connect(this.ctx.destination);
      } catch (e) {
        return false;
      }
      this.sourceNode = node;
      this.audioEl = audioEl;
    }
    return true;
  }

  /** 开始同步（audioEl 开始播放后调用） */
  start(audioEl) {
    if (!this.enabled) return false;
    if (!this.player || !this.hasTalkAnims()) return false;
    if (!this._ensureNodes(audioEl)) return false;

    this.wasTalking = false;
    this.lastSwitchAt = 0;
    // 平滑窗口：对 RMS 做指数滑动平均，进一步抑制单帧尖峰
    let smooth = 0;
    const ALPHA = 0.35;

    // 自适应增益：不同语音的录音电平差异很大（实测 p50 从 0.0001 到 0.0992，
    // 差 1000 倍）。用固定阈值会让轻声语音完全不动、巨响语音一直张着嘴。
    // 做法：滚动跟踪最近窗口内的峰值，把阈值按峰值归一化。
    let peak = 0.05;
    const PEAK_DECAY = 0.9995;   // 峰值缓慢衰减，适应音量变化
    const PEAK_FLOOR = 0.02;     // 峰值下限，避免静音段把阈值拉到 0

    const tick = () => {
      if (!this.analyser) return;
      this.analyser.getFloatTimeDomainData(this.rmsBuf);
      let sum = 0;
      for (let i = 0; i < this.rmsBuf.length; i++) {
        const v = this.rmsBuf[i];
        sum += v * v;
      }
      const rms = Math.sqrt(sum / this.rmsBuf.length);
      smooth = smooth * (1 - ALPHA) + rms * ALPHA;

      // 更新峰值跟踪
      peak = Math.max(smooth, peak * PEAK_DECAY, PEAK_FLOOR);

      // 自适应阈值：基准值 × 峰值比例，但夹在合理范围内。
      // 审核实测各音量下的触发率：vol 0.05→0%，0.15→9.2%，0.35→17.3%，
      // 0.70→18.4%，1.00→21.4%。低音量完全不触发，故下限不能太高。
      const up = Math.max(0.02, Math.min(this.thresholdUp * 2.5, peak * 0.15));
      const down = Math.max(0.008, Math.min(this.thresholdDown * 2.5, peak * 0.06));

      // 滞回：向上用 up，向下用 down
      let talking = this.wasTalking;
      if (!this.wasTalking && smooth > up) talking = true;
      else if (this.wasTalking && smooth < down) talking = false;

      if (this.onLevel) {
        try { this.onLevel(smooth, talking, peak, up, down); } catch (e) {}
      }

      const now = performance.now();
      if (talking !== this.wasTalking && (now - this.lastSwitchAt) >= this.minHoldMs) {
        this._playTalk(talking);
        this.wasTalking = talking;
        this.lastSwitchAt = now;
      }
      this.rafId = requestAnimationFrame(tick);
    };
    this.rafId = requestAnimationFrame(tick);
    return true;
  }

  /**
   * 切换口型状态。
   *
   * 审核实测数据（talk_start 标称 0.5s 的可见时长）：
   *   addAnimation(idle, true, 0)    → 201ms  (40.2%)   ← 旧实现
   *   只 setAnimation，不排 idle      → 1169ms 但不回落，卡住
   *   addAnimation(idle, true, 0.5)  → 459ms  (91.8%)   ← 采用
   *
   * ⚠️ 为什么不用 talk_a / talk_i / talk_u 做循环口型：
   *
   *   纠正（2026-11-12 审核实测）：这三个动画在 runtime 的
   *   skeletonData.animations 里【都存在】，不是被导入器丢弃。
   *   findAnimation('talk_a') 返回的是对象而非 null。
   *
   *   真正的问题是【duration 几乎为 0】：
   *     c010 / c011 / c080  → talk_a / talk_i / talk_u 的 duration = 0
   *     c381                → duration = 0.033s（1 帧 @30fps）
   *   duration=0 的动画不能循环播放（除零 / 立即结束），
   *   0.033s 的每帧都抖动，都不适合当口型循环。
   *   所以只能接受 talk_start/talk_end 单帧的覆盖率上限。
   */
  _playTalk(talking) {
    if (!this.player) return;
    // 冲突保护：互动动画（如 expression_0）正在播时不要抢轨道，
    // 否则会把互动动画打断成 talk_*，用户看起来就是"点了没反应/瞬间跳回 idle"。
    try {
      const cur = this.player.animationState.getCurrent(0);
      if (cur && !cur.loop) {
        const name = cur.animation && cur.animation.name;
        if (name && !/^talk_/i.test(name)) return;
      }
    } catch (e) {}

    try {
      const idle = this.animations.find(a => /(^|_)idle($|_)/i.test(a));

      // 停止说话时，如果 talk_end 是空动画（实测 c010 的 talk_end
      // duration=0、timelines=0），播它没有任何效果，直接切 idle 更干净。
      // 否则会出现「trackTime 冻结在 0、名字仍是 talk_end」持续约 1 秒的假象
      // （审核实测 1002ms）。
      if (!talking) {
        const endDur = (() => {
          try {
            const a = this.player.animationState.data.skeletonData
              .findAnimation('talk_end');
            return a ? a.duration : 0;
          } catch (e) { return 0; }
        })();
        if (!this.animations.includes('talk_end') || endDur <= 0) {
          if (idle) { this.player.setAnimation(idle, true); this.player.play(); }
          return;
        }
      }

      const name = talking ? 'talk_start' : 'talk_end';
      if (!this.animations.includes(name)) return;

      this.player.setAnimation(name, false);
      if (idle) {
        // 关键：把 idle 排队到 talk 动画播完之后，delay 传它的真实时长。
        // delay=0 会让 idle 立刻入队，配合 defaultMix=0.25 把 talk 削掉 60%。
        let dur = 0.5;
        try {
          const e = this.player.animationState.getCurrent(0);
          if (e && e.animation && e.animation.duration) {
            dur = e.animation.duration;
          } else {
            const found = this.player.animationState.data.skeletonData
              .findAnimation(name);
            if (found && found.duration) dur = found.duration;
          }
        } catch (e) {}
        try { this.player.addAnimation(idle, true, dur); } catch (e) {}
      }
      this.player.play();
    } catch (e) {
      console.warn('[LipSync] talk 动画失败', e);
    }
  }

  /** 停止口型驱动（不改播放状态） */
  stop() {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    this.wasTalking = false;
  }

  setEnabled(on) {
    this.enabled = !!on;
    if (!this.enabled) this.stop();
  }
}

if (typeof window !== 'undefined') {
  window.LipSync = LipSync;
}
