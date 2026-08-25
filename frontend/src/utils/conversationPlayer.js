import { publishFarEnd } from './aec/farEndBus';

/**
 * Gapless PCM16 queue player for agent speech.
 *
 * This is the one genuinely new client primitive the agents feature needs.
 * Everything else in the app synthesizes a whole WAV and hands it to
 * `playBlobAudio`; a conversation needs playback that is incremental (sentence
 * N plays while N+1 is still synthesizing), gapless (a seam between sentences
 * reads as the agent hesitating), and instantly stoppable (barge-in).
 *
 * Two jobs, and the second is the one that is easy to miss:
 *
 * 1. **Play** — chunks are scheduled on the Web Audio clock rather than fired
 *    on `ended` callbacks. Each buffer starts exactly where the previous one
 *    finished, so there is no event-loop jitter between sentences.
 *
 * 2. **Publish the far-end reference** — every frame played is pushed to
 *    `publishFarEnd`, which is what the dictation AEC subtracts from the
 *    microphone. Without this the microphone hears the agent through the
 *    speakers, the ASR transcribes it, and the agent interrupts itself in a
 *    loop. That failure mode looks like a barge-in bug and is actually a
 *    missing reference signal, so it is wired here, in the player, where it
 *    cannot be forgotten by a caller.
 */

/** Frame size for far-end publication. ~20 ms at 24 kHz — matches the AEC. */
const FAR_END_FRAME = 480;

/**
 * Small lead so the first chunk is scheduled slightly in the future. Scheduling
 * at exactly `currentTime` races the audio thread and drops the head of the
 * first word.
 */
const START_LEAD_S = 0.05;

export class ConversationPlayer {
  constructor({ audioContext } = {}) {
    this._externalContext = Boolean(audioContext);
    this._ctx = audioContext || null;
    /** When the currently-queued audio finishes, on the AudioContext clock. */
    this._playheadAt = 0;
    this._sources = new Set();
    /** Pending far-end publication timers, cleared wholesale on flush(). */
    this._farEndTimers = new Set();
    this._onStateChange = null;
    this._speaking = false;
  }

  /** Called with `true` when speech starts and `false` when the queue drains. */
  onSpeakingChange(cb) {
    this._onStateChange = cb;
  }

  _context() {
    if (!this._ctx) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) throw new Error('Web Audio is not available in this browser.');
      this._ctx = new Ctor();
    }
    return this._ctx;
  }

  _setSpeaking(next) {
    if (this._speaking === next) return;
    this._speaking = next;
    this._onStateChange?.(next);
  }

  /**
   * Queue one PCM16 mono chunk for playback.
   *
   * @param {ArrayBuffer} pcm16 raw little-endian int16 samples
   * @param {number} sampleRate the rate those samples were produced at
   */
  enqueue(pcm16, sampleRate) {
    if (!pcm16 || pcm16.byteLength < 2) return;
    const ctx = this._context();
    // Autoplay policy: a context created before a user gesture starts
    // suspended. Resuming is a no-op when it is already running.
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});

    const samples = new Int16Array(pcm16);
    const float = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i += 1) {
      // 32768 (not 32767) so the negative rail maps to exactly -1.0 and cannot
      // clip on the way back out.
      float[i] = samples[i] / 32768;
    }

    const buffer = ctx.createBuffer(1, float.length, sampleRate);
    buffer.copyToChannel(float, 0);

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);

    // Schedule against the running playhead, not against `currentTime`: that is
    // what makes consecutive sentences seamless rather than separated by
    // however long the event loop took to notice the previous one ended.
    const startAt = Math.max(this._playheadAt, ctx.currentTime + START_LEAD_S);
    source.start(startAt);
    this._playheadAt = startAt + buffer.duration;

    this._sources.add(source);
    this._setSpeaking(true);
    source.onended = () => {
      this._sources.delete(source);
      if (this._sources.size === 0) this._setSpeaking(false);
    };

    this._publishFarEnd(float, startAt, sampleRate, ctx);
  }

  /**
   * Feed the AEC reference in step with playback.
   *
   * Publishing the whole chunk immediately would hand the echo canceller audio
   * that has not been played yet, and it aligns the reference against the mic
   * by arrival time. Frames are therefore released on a timer that tracks the
   * scheduled start, so the reference stays roughly in phase with what the
   * speakers are actually emitting.
   */
  _publishFarEnd(float, startAt, sampleRate, ctx) {
    const frameMs = (FAR_END_FRAME / sampleRate) * 1000;
    const leadMs = Math.max(0, (startAt - ctx.currentTime) * 1000);

    for (let offset = 0, i = 0; offset < float.length; offset += FAR_END_FRAME, i += 1) {
      const frame = float.subarray(offset, Math.min(offset + FAR_END_FRAME, float.length));
      const at = leadMs + i * frameMs;
      const timer = setTimeout(() => {
        this._farEndTimers.delete(timer);
        publishFarEnd(frame);
      }, at);
      this._farEndTimers.add(timer);
    }
  }

  /**
   * Stop everything immediately and drop anything queued. This is barge-in.
   *
   * Sources are stopped rather than disconnected so the audio thread silences
   * them at once; pending far-end timers are cleared too, or the AEC would keep
   * receiving a reference for audio that is no longer playing and start
   * subtracting the user's own voice.
   */
  flush() {
    for (const source of this._sources) {
      try {
        source.onended = null;
        source.stop();
      } catch {
        /* already finished — stopping twice throws, and is harmless */
      }
    }
    this._sources.clear();
    for (const timer of this._farEndTimers) clearTimeout(timer);
    this._farEndTimers.clear();
    this._playheadAt = this._ctx ? this._ctx.currentTime : 0;
    this._setSpeaking(false);
  }

  /** Whether audio is currently queued or playing. */
  get speaking() {
    return this._speaking;
  }

  /** Release the AudioContext. Safe to call twice. */
  async close() {
    this.flush();
    if (this._ctx && !this._externalContext) {
      try {
        await this._ctx.close();
      } catch {
        /* already closed */
      }
      this._ctx = null;
    }
  }
}
