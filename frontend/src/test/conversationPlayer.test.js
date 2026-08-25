import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ConversationPlayer } from '../utils/conversationPlayer';
import { subscribeFarEnd } from '../utils/aec/farEndBus';

/**
 * The player is the primitive whose failures are silent.
 *
 * If gapless scheduling regresses the agent merely sounds hesitant. If far-end
 * publication regresses, the microphone hears the agent through the speakers,
 * the ASR transcribes it, and the agent interrupts itself — a bug that presents
 * as "barge-in is broken" and is actually a missing reference signal. Neither
 * shows up in a render test, so both are pinned here.
 */

/** Minimal AudioContext: records what was scheduled and when. */
function fakeContext() {
  const started = [];
  const ctx = {
    state: 'running',
    currentTime: 0,
    resume: vi.fn(() => Promise.resolve()),
    close: vi.fn(() => Promise.resolve()),
    destination: {},
    createBuffer(channels, length, sampleRate) {
      return {
        length,
        sampleRate,
        duration: length / sampleRate,
        _data: null,
        copyToChannel(data) {
          this._data = data;
        },
      };
    },
    createBufferSource() {
      const source = {
        buffer: null,
        onended: null,
        connect: vi.fn(),
        stop: vi.fn(function stop() {
          this.stopped = true;
        }),
        start: vi.fn(function start(at) {
          started.push({ at, duration: this.buffer.duration });
        }),
        stopped: false,
      };
      ctx._sources.push(source);
      return source;
    },
    _sources: [],
  };
  ctx.started = started;
  return ctx;
}

/** `n` samples of PCM16 as an ArrayBuffer. */
function pcm(n) {
  return new Int16Array(n).fill(1000).buffer;
}

let ctx;
let player;

beforeEach(() => {
  vi.useFakeTimers();
  ctx = fakeContext();
  player = new ConversationPlayer({ audioContext: ctx });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('ConversationPlayer scheduling', () => {
  it('schedules consecutive chunks back to back, with no gap', () => {
    player.enqueue(pcm(2400), 24000); // 100 ms
    player.enqueue(pcm(2400), 24000); // 100 ms

    const [first, second] = ctx.started;
    expect(second.at).toBeCloseTo(first.at + first.duration, 6);
  });

  it('does not schedule the first chunk at exactly currentTime', () => {
    // Scheduling at currentTime races the audio thread and clips the head of
    // the first word.
    player.enqueue(pcm(2400), 24000);
    expect(ctx.started[0].at).toBeGreaterThan(ctx.currentTime);
  });

  it('never schedules in the past after a long silence', () => {
    player.enqueue(pcm(2400), 24000);
    // The conversation goes quiet; the clock moves well past the playhead.
    ctx.currentTime = 60;
    player.enqueue(pcm(2400), 24000);
    expect(ctx.started[1].at).toBeGreaterThanOrEqual(60);
  });

  it('ignores an empty chunk', () => {
    player.enqueue(new ArrayBuffer(0), 24000);
    player.enqueue(null, 24000);
    expect(ctx.started).toHaveLength(0);
  });

  it('resumes a suspended context', () => {
    ctx.state = 'suspended';
    player.enqueue(pcm(480), 24000);
    expect(ctx.resume).toHaveBeenCalled();
  });

  it('converts PCM16 so the negative rail cannot clip', () => {
    const samples = new Int16Array([-32768, 0, 32767]);
    player.enqueue(samples.buffer, 24000);
    const written = ctx._sources[0].buffer._data;
    expect(written[0]).toBe(-1);
    expect(written[1]).toBe(0);
    expect(written[2]).toBeLessThan(1);
  });
});

describe('ConversationPlayer far-end reference', () => {
  it('publishes played audio to the AEC bus', () => {
    const frames = [];
    const unsubscribe = subscribeFarEnd((f) => frames.push(f));
    try {
      player.enqueue(pcm(2400), 24000);
      vi.advanceTimersByTime(500);
      expect(frames.length).toBeGreaterThan(0);
      // 2400 samples at a 480-sample frame = 5 frames.
      expect(frames).toHaveLength(5);
      expect(frames[0]).toBeInstanceOf(Float32Array);
    } finally {
      unsubscribe();
    }
  });

  it('releases frames over time rather than all at once', () => {
    // Publishing the whole chunk immediately would hand the echo canceller
    // audio that has not been played yet, so the reference would be out of
    // phase with the microphone and cancel the wrong thing.
    const frames = [];
    const unsubscribe = subscribeFarEnd((f) => frames.push(f));
    try {
      player.enqueue(pcm(2400), 24000);
      vi.advanceTimersByTime(0);
      const immediately = frames.length;
      vi.advanceTimersByTime(500);
      expect(immediately).toBeLessThan(frames.length);
    } finally {
      unsubscribe();
    }
  });

  it('stops publishing once flushed', () => {
    const frames = [];
    const unsubscribe = subscribeFarEnd((f) => frames.push(f));
    try {
      player.enqueue(pcm(24000), 24000); // 1 s
      vi.advanceTimersByTime(0);
      const beforeFlush = frames.length;
      player.flush();
      vi.advanceTimersByTime(2000);
      // A reference for audio that is no longer playing would make the AEC
      // subtract the user's own voice — the opposite of what it is for.
      expect(frames).toHaveLength(beforeFlush);
    } finally {
      unsubscribe();
    }
  });
});

describe('ConversationPlayer barge-in', () => {
  it('stops every queued source', () => {
    player.enqueue(pcm(2400), 24000);
    player.enqueue(pcm(2400), 24000);
    player.flush();
    expect(ctx._sources.every((s) => s.stopped)).toBe(true);
  });

  it('resets the playhead so the next reply starts immediately', () => {
    player.enqueue(pcm(24000), 24000); // 1 s queued
    ctx.currentTime = 0.1;
    player.flush();
    player.enqueue(pcm(2400), 24000);
    // Without the reset, the next reply would wait out the flushed second.
    expect(ctx.started[1].at).toBeLessThan(0.5);
  });

  it('survives flushing twice and flushing an idle player', () => {
    expect(() => {
      player.flush();
      player.enqueue(pcm(480), 24000);
      player.flush();
      player.flush();
    }).not.toThrow();
  });

  it('reports speaking state on both edges', () => {
    const seen = [];
    player.onSpeakingChange((v) => seen.push(v));

    player.enqueue(pcm(2400), 24000);
    expect(seen).toEqual([true]);

    player.flush();
    expect(seen).toEqual([true, false]);
  });
});

describe('ConversationPlayer lifecycle', () => {
  it('does not close a context it did not create', () => {
    // The caller owns an injected context; closing it would break whatever
    // else is using it.
    return player.close().then(() => {
      expect(ctx.close).not.toHaveBeenCalled();
    });
  });
});
