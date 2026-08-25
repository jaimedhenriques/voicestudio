import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bot, Phone, PhoneOff, Plus, Trash2 } from 'lucide-react';

import { Button, Field, Input, Panel, Textarea, Select, Badge } from '../ui';
import {
  createAgent,
  deleteAgent,
  getAgentReadiness,
  listAgents,
  preflightCall,
  updateAgent,
} from '../api/agents';
import { ConversationSocket } from '../utils/conversationSocket';

/**
 * The Agents workspace: build an agent, then talk to it.
 *
 * The test conversation is text-in / voice-out. Wiring the microphone means
 * routing `/ws/transcribe` (streaming ASR, already shipped for dictation)
 * through the AEC alongside this socket; that is a real piece of work and it is
 * deliberately not half-done here. What this proves end to end today is the
 * part that is genuinely new: streamed generation, sentence-by-sentence
 * synthesis, gapless playback, and barge-in that actually stops the audio.
 */

const EMPTY_DRAFT = {
  name: '',
  system_prompt: '',
  first_message: '',
  voice_profile: null,
  language: 'en',
  temperature: null,
};

export default function Agents({ profiles = [] }) {
  const { t } = useTranslation();
  const [agents, setAgents] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [readiness, setReadiness] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    try {
      const next = await listAgents();
      setAgents(next);
      return next;
    } catch (err) {
      setError(err?.message || t('agents.loadFailed'));
      return [];
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const selected = agents.find((a) => a.id === selectedId) || null;

  useEffect(() => {
    if (!selected) {
      setReadiness(null);
      return;
    }
    setDraft({
      name: selected.name,
      system_prompt: selected.system_prompt,
      first_message: selected.first_message,
      voice_profile: selected.voice_profile,
      language: selected.language,
      temperature: selected.temperature,
    });
    getAgentReadiness(selected.id)
      .then(setReadiness)
      .catch(() => setReadiness(null));
  }, [selected]);

  const onCreate = async () => {
    setBusy(true);
    setError('');
    try {
      const created = await createAgent({
        ...EMPTY_DRAFT,
        name: t('agents.defaultName'),
        system_prompt: t('agents.defaultPrompt'),
      });
      await refresh();
      setSelectedId(created.id);
    } catch (err) {
      setError(err?.message || t('agents.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const onSave = async () => {
    if (!selected) return;
    setBusy(true);
    setError('');
    try {
      await updateAgent(selected.id, draft);
      await refresh();
    } catch (err) {
      setError(err?.message || t('agents.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await deleteAgent(selected.id);
      setSelectedId(null);
      await refresh();
    } catch (err) {
      setError(err?.message || t('agents.deleteFailed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="agents-page">
      <aside className="agents-list">
        <div className="agents-list-head">
          <h2>{t('agents.title')}</h2>
          <Button size="sm" onClick={onCreate} disabled={busy} title={t('agents.new')}>
            <Plus size={14} /> {t('agents.new')}
          </Button>
        </div>

        {agents.length === 0 ? (
          <p className="agents-empty">{t('agents.empty')}</p>
        ) : (
          <ul>
            {agents.map((agent) => (
              <li key={agent.id}>
                <button
                  type="button"
                  className={agent.id === selectedId ? 'is-selected' : ''}
                  onClick={() => setSelectedId(agent.id)}
                  aria-current={agent.id === selectedId ? 'true' : undefined}
                >
                  <Bot size={14} aria-hidden="true" />
                  <span>{agent.name}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <section className="agents-detail">
        {error && (
          <p className="agents-error" role="alert">
            {error}
          </p>
        )}

        {!selected ? (
          <Panel>
            <p>{t('agents.selectPrompt')}</p>
          </Panel>
        ) : (
          <>
            <Panel title={t('agents.configure')}>
              <Field label={t('agents.name')}>
                <Input
                  autoComplete="off"
                  spellCheck={false}
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                />
              </Field>

              <Field label={t('agents.prompt')} hint={t('agents.promptHint')}>
                <Textarea
                  rows={6}
                  value={draft.system_prompt}
                  onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })}
                />
              </Field>

              <Field label={t('agents.firstMessage')} hint={t('agents.firstMessageHint')}>
                <Input
                  value={draft.first_message}
                  onChange={(e) => setDraft({ ...draft, first_message: e.target.value })}
                />
              </Field>

              <Field label={t('agents.voice')}>
                <Select
                  value={draft.voice_profile || ''}
                  onChange={(e) => setDraft({ ...draft, voice_profile: e.target.value || null })}
                >
                  <option value="">{t('agents.voiceDefault')}</option>
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </Select>
              </Field>

              <div className="agents-actions">
                <Button onClick={onSave} disabled={busy}>
                  {t('agents.save')}
                </Button>
                <Button variant="ghost" onClick={onDelete} disabled={busy}>
                  <Trash2 size={14} /> {t('agents.delete')}
                </Button>
              </div>
            </Panel>

            <ReadinessPanel readiness={readiness} t={t} />
            <TestConversation agent={selected} t={t} />
            <CallPanel agent={selected} t={t} />
          </>
        )}
      </section>
    </div>
  );
}

function ReadinessPanel({ readiness, t }) {
  if (!readiness) return null;
  return (
    <Panel title={t('agents.readiness')}>
      <ul className="agents-readiness">
        <li>
          <Badge tone={readiness.llm.ok ? 'success' : 'danger'}>
            {readiness.llm.ok ? t('agents.ok') : t('agents.notReady')}
          </Badge>
          <span>{t('agents.readinessLlm')}</span>
          {!readiness.llm.ok && <small>{readiness.llm.detail}</small>}
        </li>
        <li>
          <Badge tone={readiness.voice.ok ? 'success' : 'danger'}>
            {readiness.voice.ok ? t('agents.ok') : t('agents.notReady')}
          </Badge>
          <span>{t('agents.readinessVoice')}</span>
          {!readiness.voice.ok && <small>{readiness.voice.detail}</small>}
        </li>
        <li>
          <Badge tone={readiness.callable.ok ? 'success' : 'warn'}>
            {readiness.callable.ok ? t('agents.ok') : t('agents.consentNeeded')}
          </Badge>
          <span>{t('agents.readinessCallable')}</span>
          {!readiness.callable.ok && <small>{readiness.callable.detail}</small>}
        </li>
      </ul>
    </Panel>
  );
}

function TestConversation({ agent, t }) {
  const [transcript, setTranscript] = useState([]);
  const [input, setInput] = useState('');
  const [state, setState] = useState('idle');
  const [live, setLive] = useState(false);
  const socketRef = useRef(null);

  // Close the socket on unmount or when the selected agent changes. Without
  // this, switching agents mid-conversation leaves the old socket streaming
  // audio from an agent that is no longer on screen.
  useEffect(
    () => () => {
      socketRef.current?.close();
      socketRef.current = null;
    },
    [agent.id],
  );

  const handleEvent = useCallback(
    (event) => {
      if (event.type === 'state') {
        setState(event.value);
        return;
      }
      if (event.type === 'sentence') {
        setTranscript((prev) => [...prev, { role: 'assistant', text: event.text }]);
        return;
      }
      if (event.type === 'interrupted') {
        setTranscript((prev) => [...prev, { role: 'system', text: t('agents.interrupted') }]);
        return;
      }
      if (event.type === 'error') {
        setTranscript((prev) => [...prev, { role: 'error', text: event.detail }]);
        return;
      }
      if (event.type === 'closed') {
        setLive(false);
        setState('idle');
      }
    },
    [t],
  );

  const start = async () => {
    const socket = new ConversationSocket({ onEvent: handleEvent });
    socketRef.current = socket;
    try {
      await socket.connect(agent.id);
      setLive(true);
      setTranscript([]);
    } catch (err) {
      setTranscript([{ role: 'error', text: err?.message || t('agents.connectFailed') }]);
      socketRef.current = null;
    }
  };

  const stop = async () => {
    await socketRef.current?.close();
    socketRef.current = null;
    setLive(false);
    setState('idle');
  };

  const send = (e) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || !socketRef.current) return;
    setTranscript((prev) => [...prev, { role: 'user', text }]);
    socketRef.current.say(text);
    setInput('');
  };

  return (
    <Panel title={t('agents.testTitle')}>
      <p className="agents-hint">{t('agents.testHint')}</p>

      <div className="agents-transcript" aria-live="polite">
        {transcript.map((line, i) => (
          <p key={i} className={`agents-line is-${line.role}`}>
            {line.text}
          </p>
        ))}
      </div>

      <div className="agents-actions">
        {live ? (
          <>
            <Button variant="ghost" onClick={stop}>
              {t('agents.end')}
            </Button>
            {/* Barge-in is only meaningful while the agent is speaking, and it
                must stop the speaker locally before the server round trip. */}
            <Button onClick={() => socketRef.current?.bargeIn()} disabled={state !== 'speaking'}>
              {t('agents.interrupt')}
            </Button>
          </>
        ) : (
          <Button onClick={start}>{t('agents.start')}</Button>
        )}
        <span className="agents-state">{t(`agents.state.${state}`)}</span>
      </div>

      {live && (
        <form onSubmit={send} className="agents-compose">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t('agents.sayPlaceholder')}
            aria-label={t('agents.sayPlaceholder')}
          />
          <Button type="submit">{t('agents.send')}</Button>
        </form>
      )}
    </Panel>
  );
}

function CallPanel({ agent, t }) {
  const [destination, setDestination] = useState('');
  const [result, setResult] = useState(null);

  const check = async () => {
    try {
      setResult(await preflightCall(agent.id, destination));
    } catch (err) {
      setResult({ ok: false, detail: err?.message || t('agents.preflightFailed') });
    }
  };

  return (
    <Panel title={t('agents.callTitle')}>
      {/* Stated up front rather than discovered by a user who fills the form in
          and then hits a 501. */}
      <p className="agents-hint">
        <PhoneOff size={14} aria-hidden="true" /> {t('agents.callUnavailable')}
      </p>

      <Field label={t('agents.destination')} hint={t('agents.destinationHint')}>
        <Input
          type="tel"
          autoComplete="tel"
          value={destination}
          onChange={(e) => setDestination(e.target.value)}
          placeholder="+14155550123"
          inputMode="tel"
        />
      </Field>

      <div className="agents-actions">
        <Button variant="ghost" onClick={check} disabled={!destination.trim()}>
          <Phone size={14} /> {t('agents.preflight')}
        </Button>
      </div>

      {result && (
        <div className="agents-preflight" role="status">
          <p>{result.detail}</p>
          {result.disclosure && (
            <>
              <p className="agents-hint">{t('agents.disclosureLabel')}</p>
              <blockquote>{result.disclosure}</blockquote>
            </>
          )}
        </div>
      )}
    </Panel>
  );
}
