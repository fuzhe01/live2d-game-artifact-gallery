/**
 * NIKKE 互动模块：点击命中检测 + 播动画 + 播语音
 *
 * 依赖：
 *   - reactions.json  （角色互动配置）
 *   - meta.json       （每姿态的动画清单 + 音效时间轴）
 *   - data/voice/*.wav（互动语音）
 *
 * 工作原理：
 *   1. 用 spine-player 的 skeleton 坐标判断点击落在哪个 slot 的 attachment 上
 *   2. 命中 region attachment → 找到最近的 body 区域 → 映射到 Touch_1/2/3
 *   3. 播对应动画（expression_0 等）+ 播同名 WAV
 *   4. 同时监听 SoundEvents 时间轴，在动作播到指定秒时触发音效
 *
 * 简化策略（第一版）：
 *   不做精确 attachment 命中，改为「点击画布任意位置 = 轮换 Touch_1→2→3」，
 *   同时提供 UI 按钮直接触发。精确命中留作增强。
 */

const InteractionMode = {
  SEQUENTIAL: 'sequential',   // 轮换播放
  RANDOM: 'random',
};

class NikkeInteraction {
  constructor(opts = {}) {
    this.dataDir = opts.dataDir || '../data';
    this.voiceDir = opts.voiceDir || '../data/voice';
    this.reactions = null;      // 全量 reactions.json
    this.currentId = null;      // 当前角色 id
    this.currentPose = 'fb';
    this.currentCharReactions = null;
    this.player = null;
    this.audioCtx = null;
    this.activeAudio = null;
    this.touchCounters = { touch: 0, touch_love: 0 };
    this.loveMode = false;      // 好感模式（切到 _love 变体）
    this.mode = InteractionMode.SEQUENTIAL;
    this.soundTimers = [];
    this.onStatus = opts.onStatus || (() => {});
    // 语音开始播放时的回调 (cid, clipName) —— 供字幕气泡使用
    this.onVoice = opts.onVoice || null;
    // 口型同步（如果 lipsync.js 已加载）
    this.lipSync = (typeof LipSync !== 'undefined')
      ? new LipSync({ enabled: true })
      : null;
    this._voiceKnownMissing = new Set();
  }

  async loadReactions() {
    if (this.reactions) return this.reactions;
    const r = await fetch(`${this.dataDir}/reactions.json`);
    this.reactions = await r.json();
    return this.reactions;
  }

  /** 切换到某角色 */
  async setCharacter(cid, pose = 'fb') {
    await this.loadReactions();
    this.currentId = cid;
    this.currentPose = pose;
    // 皮肤变体（cNNN_XX）在游戏里共用本体的台词池，但 reactions.json 只
    // 收录了部分变体。这里做父角色回退：c010_01 → 查 c010。
    // 实测：231 个无反应的角色里 111 个是这种可继承的变体。
    this.currentCharReactions = this.reactions.characters[cid] || null;
    this._inheritedFrom = null;
    if (!this.currentCharReactions) {
      const mm = /^(c\d{3})_\d{2}$/.exec(cid);
      if (mm) {
        const parentId = mm[1];
        const parent = this.reactions.characters[parentId];
        if (parent) {
          this.currentCharReactions = parent;
          this._inheritedFrom = parentId;
        }
      }
    }
    this.touchCounters = { touch: 0, touch_love: 0 };
    this.clearSoundTimers();
    return this.currentCharReactions;
  }

  /** 绑定 spine-player 实例 */
  attachPlayer(pl) {
    this.player = pl;
    this.clearSoundTimers();
    // 重建口型绑定（切换姿态/角色后 player 是新的）
    if (this.lipSync && pl) {
      const anims = this.getAnimations();
      this.lipSync.attach(pl, anims);
    }
  }

  /** 口型是否可用（当前骨架有 talk_start + talk_end） */
  lipSyncAvailable() {
    if (!this.lipSync) return false;
    return this.lipSync.hasTalkAnims();
  }

  /** 拿当前姿态的 meta（动画清单 + 音效时间轴） */
  async loadMeta(cid, pose) {
    const sub = pose === 'fb' ? '' : `${pose}/`;
    try {
      const r = await fetch(`${this.dataDir}/${cid}/${sub}meta.json`);
      if (!r.ok) return null;
      return await r.json();
    } catch (e) {
      return null;
    }
  }

  /** 播放指定动画。
   * @param {string} name  动画名
   * @param {boolean} loop 是否循环
   * @param {string[]|null} anims 该骨架的完整动画列表（用于找 idle 回落目标）
   *
   * 非循环动画播完后必须接 idle 循环，否则角色会僵在最后一帧。
   * 与 index.html 的 playAnim() 行为保持一致。
   */
  playAnimation(name, loop = false, anims = null) {
    if (!this.player) return false;
    try {
      const list = anims || this.getAnimations();
      // ⚠️ cover / aim 姿态的骨架只有 cover_idle / aim_idle / aim_fire 等，
      // 根本没有 expression_0、idle、talk_* —— fb 的互动动画名在这里不存在。
      // spine-player 的 setAnimation 传入不存在的名字会留下空轨道，
      // 表现就是「点一下播语音，角色画面直接消失」。
      // 修法：目标动画不存在时，只播语音/字幕，不动动画轨道。
      if (!loop && list.length && !list.includes(name)) {
        // 若当前轨道本来就空（罕见），补回本姿态的 idle
        const cur = this.player.animationState.getCurrent(0);
        if (!cur) {
          const idle = list.find(a => /(^|_)idle($|_)/i.test(a));
          if (idle) this.player.setAnimation(idle, true);
        }
        return false;
      }
      this.player.setAnimation(name, loop);
      if (!loop && list.length) {
        const idle = list.find(a => /(^|_)idle($|_)/i.test(a))
                  || list.find(a => /(^|_)(stand|standby|wait|loop)(\b|_|$)/i.test(a));
        if (idle && idle !== name) {
          try { this.player.addAnimation(idle, true, 0); } catch (e) {}
        }
      }
      this.player.play();
      return true;
    } catch (e) {
      console.warn('[NikkeInteraction] setAnimation 失败', name, e);
      return false;
    }
  }

  /** 读当前骨架的动画名列表（带缓存） */
  getAnimations() {
    if (!this.player) return [];
    try {
      return this.player.animationState.data.skeletonData.animations.map(a => a.name);
    } catch (e) {
      return [];
    }
  }

  /** 播放语音 WAV。返回 Audio 对象；资源不存在时返回 null。 */
  playVoice(clipName) {
    if (!clipName) return null;
    // 已知缺失的语音（如 c530 客户端 ja 包未收录）直接跳过，避免 404 噪音。
    // 首次遇缺时向后端探测一次并缓存结果。
    const url = `${this.voiceDir}/${clipName}.wav`;
    if (this._voiceKnownMissing && this._voiceKnownMissing.has(clipName)) {
      this.onStatus(`（${clipName} 无语音资源）`);
      return null;
    }
    try {
      if (this.activeAudio) {
        this.activeAudio.pause();
        this.activeAudio = null;
      }
      const a = new Audio(url);
      a.volume = 0.9;
      a.addEventListener('error', () => {
        if (!this._voiceKnownMissing) this._voiceKnownMissing = new Set();
        this._voiceKnownMissing.add(clipName);
      }, { once: true });
      // 开始播放时启动口型同步（需要用户手势后才能建 AudioContext，此处在 click 链路内）
      a.addEventListener('playing', () => {
        if (this.lipSync) {
          const anims = this.getAnimations();
          // 语音期间可能已换角色，重新对齐一次
          this.lipSync.attach(this.player, anims);
          this.lipSync.start(a);
        }
        // 字幕：把当前角色和 clip 名交给外部回调
        if (this.onVoice) {
          try { this.onVoice(this.currentId, clipName); } catch (e) {}
        }
      }, { once: true });
      a.addEventListener('ended', () => {
        if (this.lipSync) this.lipSync.stop();
      }, { once: true });
      const p = a.play();
      if (p && p.catch) {
        p.catch(err => {
          if (!this._voiceKnownMissing) this._voiceKnownMissing = new Set();
          this._voiceKnownMissing.add(clipName);
        });
      }
      this.activeAudio = a;
      return a;
    } catch (e) {
      console.warn('[NikkeInteraction] 语音异常', e);
      return null;
    }
  }

  /**
   * 触发互动
   * @param {'touch'|'touch_love'|'stay'|'stay_love'} kind
   * @param {number|null} index 1-based；null = 轮换
   */
  async trigger(kind = 'touch', index = null) {
    // ⚠️ 无互动数据时不能返回 null —— 调用方会直接读 res.animOk 导致
    // TypeError: Cannot read properties of null (reading 'animOk')。
    // 统一返回 {ok:false, reason} 让调用方显式判断。
    if (!this.currentCharReactions) {
      this.onStatus('该角色无互动台词');
      return { ok: false, reason: 'no-data', item: null, variant: null,
               animOk: false, audio: null };
    }
    const list = this.currentCharReactions[kind];
    if (!list || !list.length) {
      this.onStatus(`该角色无 ${kind} 台词`);
      return { ok: false, reason: 'no-kind', item: null, variant: null,
               animOk: false, audio: null };
    }

    let item;
    if (index === null) {
      const c = this.touchCounters[kind] || 0;
      item = list[c % list.length];
      this.touchCounters[kind] = c + 1;
    } else {
      item = list.find(x => x.index === index) || list[0];
    }

    // 选取动画变体：
    //   reactions.json 的结构是 {index, reaction_id, voice_key, group_id, variants:[{animation, ...}]}
    //   同一反应可能有多个变体（expression_0 / expression_1），游戏按条件或随机播放。
    //   这里优先挑 animation_exists !== false 的，多个时随机。
    const all = item.variants || [];
    const usable = all.filter(v => v.animation_exists !== false);
    const pool = usable.length ? usable : all;
    const v = pool.length ? pool[Math.floor(Math.random() * pool.length)] : {};
    const animName = v.animation || 'idle';

    // 1. 播动画（传 anims 以便播完回落 idle，避免定格在结束帧）
    const anims = this.getAnimations();
    const animOk = this.playAnimation(animName, false, anims);
    // 2. 播语音（voice 用 reaction_id 命名，与 AudioClip 名一致）
    const audio = this.playVoice(item.reaction_id);
    // 3. 按 SoundEvents 时间轴触发音效
    this.scheduleSoundEvents(animName);

    const tag = v.fallback_from ? ` ← ${v.fallback_from}` : '';
    this.onStatus(`▶ ${item.reaction_id}  (${animName}${tag})`);
    return { ok: true, reason: null, item, variant: v, animOk, audio };
  }

  /** 按 SoundEvents 时间轴触发音效（当前仅提示，未接音效资源） */
  async scheduleSoundEvents(animName) {
    this.clearSoundTimers();
    if (!this.currentId || !animName) return;
    const meta = await this.loadMeta(this.currentId, this.currentPose);
    if (!meta) return;
    // 守卫：SoundEvents 里存在但 skel 已裁剪的僵尸动画名（实测 19 处），
    // 这类条目永远播不到，直接跳过避免误触发。
    const anims = meta.animations || [];
    if (anims.length && !anims.includes(animName)) return;
    if (!meta.sounds) return;
    const evs = meta.sounds[animName];
    if (!evs || !evs.length) return;

    for (const ev of evs) {
      const ms = Math.max(0, ev.time * 1000);
      const tid = setTimeout(() => {
        this.onStatus(`♪ ${ev.name} @${ev.time.toFixed(2)}s`);
      }, ms);
      this.soundTimers.push(tid);
    }
  }

  clearSoundTimers() {
    for (const t of this.soundTimers) clearTimeout(t);
    this.soundTimers = [];
  }

  /** 列出当前角色所有可用反应（给 UI 用） */
  listReactions() {
    if (!this.currentCharReactions) return [];
    const out = [];
    for (const kind of ['touch', 'touch_love', 'stay', 'stay_love']) {
      const list = this.currentCharReactions[kind];
      if (!list) continue;
      for (const it of list) {
        out.push({ kind, ...it });
      }
    }
    return out;
  }

  /** 是否支持互动 */
  isSupported() {
    return !!(this.currentCharReactions && Object.keys(this.currentCharReactions).length);
  }

  stop() {
    this.clearSoundTimers();
    if (this.activeAudio) {
      this.activeAudio.pause();
      this.activeAudio = null;
    }
  }
}

// 导出（浏览器全局）
if (typeof window !== 'undefined') {
  window.NikkeInteraction = NikkeInteraction;
  window.InteractionMode = InteractionMode;
}
