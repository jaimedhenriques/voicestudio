import { apiJson } from './client';

/**
 * Voice agents and the telephony guardrail surface.
 * Contract: `backend/api/routers/agents.py`.
 */

export interface Agent {
  id: string;
  name: string;
  system_prompt: string;
  first_message: string;
  voice_profile: string | null;
  language: string;
  llm_model: string | null;
  temperature: number | null;
  enabled: boolean;
  created_at: number;
  updated_at: number;
}

export type AgentDraft = Partial<Omit<Agent, 'id' | 'created_at' | 'updated_at'>> & {
  name: string;
};

export interface AgentReadiness {
  /** Can hold a browser conversation right now. */
  ready: boolean;
  llm: { ok: boolean; detail: string; backend: string };
  voice: { ok: boolean; detail: string };
  /**
   * Separate from `ready` on purpose: a browser conversation does not need a
   * consent-locked voice, but placing a call does.
   */
  callable: { ok: boolean; detail: string };
}

export interface AllowlistEntry {
  e164: string;
  label: string;
  created_at: number;
}

export interface PreflightResult {
  ok: boolean;
  state: 'NOT_PROVISIONED' | 'REFUSED' | 'READY';
  reason: string | null;
  detail: string;
  /** Exactly what the callee will hear first. Shown before dialling. */
  disclosure: string;
  calls_today: number;
  daily_cap: number;
}

export interface CallLogEntry {
  id: string;
  agent_id: string | null;
  destination: string;
  status: string;
  refused_reason: string | null;
  disclosure_text: string;
  recorded: number;
  duration_s: number | null;
  created_at: number;
  ended_at: number | null;
}

export async function listAgents(): Promise<Agent[]> {
  const body = await apiJson<{ agents: Agent[] }>('/agents');
  return body.agents;
}

export async function createAgent(draft: AgentDraft): Promise<Agent> {
  return apiJson<Agent>('/agents', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(draft),
  });
}

export async function updateAgent(id: string, patch: Partial<AgentDraft>): Promise<Agent> {
  return apiJson<Agent>(`/agents/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
}

export async function deleteAgent(id: string): Promise<void> {
  await apiJson<void>(`/agents/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function getAgentReadiness(id: string): Promise<AgentReadiness> {
  return apiJson<AgentReadiness>(`/agents/${encodeURIComponent(id)}/readiness`);
}

export async function listAllowlist(): Promise<AllowlistEntry[]> {
  const body = await apiJson<{ destinations: AllowlistEntry[] }>('/telephony/allowlist');
  return body.destinations;
}

export async function addToAllowlist(destination: string, label = ''): Promise<void> {
  await apiJson<void>('/telephony/allowlist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ destination, label }),
  });
}

export async function removeFromAllowlist(e164: string): Promise<void> {
  await apiJson<void>(`/telephony/allowlist/${encodeURIComponent(e164)}`, {
    method: 'DELETE',
  });
}

/** Evaluate every guardrail without placing a call or writing a log row. */
export async function preflightCall(
  agentId: string,
  destination: string,
  recorded = false,
): Promise<PreflightResult> {
  return apiJson<PreflightResult>('/telephony/preflight', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_id: agentId, destination, recorded }),
  });
}

export async function getCallLog(): Promise<{
  calls: CallLogEntry[];
  calls_today: number;
  daily_cap: number;
  provisioned: boolean;
}> {
  return apiJson('/telephony/calls');
}
