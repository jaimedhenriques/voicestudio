import { API } from '../api/client';
import { authenticatedWsUrl } from '../api/authSession';
import { ConversationPlayer } from './conversationPlayer';

/**
 * Client for `WS /ws/converse`.
 *
 * The backend announces audio with a JSON frame and then sends exactly one
 * binary frame, so this holds the announced sample rate across that pair. Any
 * other ordering would mean guessing the rate or parsing a container per
 * sentence.
 *
 * Playback is owned here rather than by the calling component, because
 * barge-in has to stop the speaker and tell the server in the same breath — a
 * component that only sent the message would keep talking over the user for as
 * long as the already-queued audio lasted.
 */

export class ConversationSocket {
  constructor({ onEvent } = {}) {
    this._onEvent = onEvent || (() => {});
    this._ws = null;
    this._pendingSampleRate = null;
    this.player = new ConversationPlayer();
  }

  get connected() {
    return this._ws?.readyState === WebSocket.OPEN;
  }

  /** Open the socket and start the agent. Resolves once `start` is sent. */
  async connect(agentId) {
    // Every WebSocket in this app goes through `authenticatedWsUrl`, which
    // mints a short-lived ticket when an admin session exists and is a no-op
    // on loopback. Building the URL by hand here would leave the conversation
    // socket — the one carrying microphone-adjacent audio and the agent's
    // instructions — as the only unauthenticated transport in the app.
    // `src/api/authCredentialHygiene.test.js` enforces this for the whole
    // codebase, not just for the sockets that existed when it was written.
    const endpoint = await authenticatedWsUrl('/ws/converse', { apiBase: API });
    const ws = new WebSocket(endpoint);
    ws.binaryType = 'arraybuffer';
    this._ws = ws;

    await new Promise((resolve, reject) => {
      ws.onopen = () => resolve();
      // A connection that never opens must not leave the caller awaiting
      // forever; both failure paths reject.
      ws.onerror = () => reject(new Error('Could not reach the agent service.'));
      ws.onclose = () => reject(new Error('The agent service closed the connection.'));
    });

    // Replace the bootstrap handlers now the socket is up: onclose above would
    // otherwise reject a promise that has already resolved.
    ws.onerror = null;
    ws.onclose = () => this._onEvent({ type: 'closed' });
    ws.onmessage = (event) => this._receive(event);

    this._send({ type: 'start', agent_id: agentId });
  }

  _send(payload) {
    if (this._ws?.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(payload));
    }
  }

  _receive(event) {
    if (typeof event.data !== 'string') {
      // The binary half of an announced audio pair.
      const sampleRate = this._pendingSampleRate;
      this._pendingSampleRate = null;
      if (sampleRate) this.player.enqueue(event.data, sampleRate);
      return;
    }

    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return; // a frame we cannot parse is not worth tearing the call down for
    }

    if (message.type === 'audio') {
      this._pendingSampleRate = message.sample_rate;
      return; // the bytes arrive next; nothing to surface yet
    }

    this._onEvent(message);
  }

  /** Commit a user turn (an ASR final, or typed text). */
  say(text) {
    const trimmed = (text || '').trim();
    if (trimmed) this._send({ type: 'user', text: trimmed });
  }

  /**
   * The user started talking over the agent.
   *
   * Order matters: stop local playback FIRST so the speaker goes quiet
   * immediately, then tell the server to stop generating. Doing it the other
   * way round leaves already-queued audio playing for the round trip.
   */
  bargeIn() {
    this.player.flush();
    this._send({ type: 'barge_in' });
  }

  async close() {
    this._send({ type: 'end' });
    await this.player.close();
    try {
      this._ws?.close();
    } catch {
      /* already closing */
    }
    this._ws = null;
  }
}
