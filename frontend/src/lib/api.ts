// Thin typed fetch client. Deliberately no heavy data-fetching library: the app's request surface is
// small and every call's honesty properties (does this cost money? does it execute?) need to be visible
// at the call site, not hidden behind a generic `useQuery(key)`.

import type {
  AnalyticsResponse,
  ApiErrorBody,
  ApiKeyCreatedResponse,
  ApiKeySummary,
  AuditLogEntry,
  CompareResponse,
  DataExportResponse,
  ExecutionResponse,
  ExecutionSummary,
  MemberResponse,
  ModelResponse,
  OptimizeResponse,
  OrgRole,
  OrgSettingsResponse,
  ProviderAccountResponse,
  PromptResponse,
  PromptVersionResponse,
  TokenResponse,
  UserResponse,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const ACCESS_TOKEN_KEY = "throughline.access_token";
const REFRESH_TOKEN_KEY = "throughline.refresh_token";

export class ApiError extends Error {
  code: string;
  detail?: string;
  status: number;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.code = body.code;
    this.detail = body.detail;
  }
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function setTokens(tokens: TokenResponse | null) {
  if (typeof window === "undefined") return;
  if (tokens === null) {
    window.localStorage.removeItem(ACCESS_TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
    return;
  }
  window.localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  opts: { auth?: boolean } = { auth: true },
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("content-type", "application/json");
  if (opts.auth !== false) {
    const token = getAccessToken();
    if (token) headers.set("authorization", `Bearer ${token}`);
  }

  const resp = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (!resp.ok) {
    let body: ApiErrorBody;
    try {
      body = await resp.json();
    } catch {
      body = { code: "unknown_error", message: `Request failed with status ${resp.status}` };
    }
    if (resp.status === 401 && opts.auth !== false) {
      setTokens(null);
    }
    throw new ApiError(resp.status, body);
  }

  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

// ---------------------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------------------
export async function signup(input: {
  email: string;
  password: string;
  display_name?: string;
  organization_name?: string;
}): Promise<TokenResponse> {
  const tokens = await request<TokenResponse>(
    "/auth/signup",
    { method: "POST", body: JSON.stringify(input) },
    { auth: false },
  );
  setTokens(tokens);
  return tokens;
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const tokens = await request<TokenResponse>(
    "/auth/login",
    { method: "POST", body: JSON.stringify({ email, password }) },
    { auth: false },
  );
  setTokens(tokens);
  return tokens;
}

export function logout() {
  setTokens(null);
}

export async function me(): Promise<UserResponse> {
  return request<UserResponse>("/auth/me");
}

// ---------------------------------------------------------------------------------------
// Engine
// ---------------------------------------------------------------------------------------
export interface EngineRequestInput {
  input: string;
  mode?: OrchestrationModeInput;
  project_id?: string | null;
  output_schema?: Record<string, unknown> | null;
}
export type OrchestrationModeInput = "auto" | "fast" | "balanced" | "deep" | "expert";

export async function optimize(body: EngineRequestInput): Promise<OptimizeResponse> {
  return request<OptimizeResponse>("/optimize", { method: "POST", body: JSON.stringify(body) });
}

export interface ExecuteInput extends EngineRequestInput {
  model_id?: string | null;
  candidate_count?: number | null;
  temperature?: number | null;
  max_output_tokens?: number | null;
  enable_judge?: boolean | null;
  max_cost_usd?: number | null;
  save_as_prompt?: string | null;
}

export async function execute(body: ExecuteInput): Promise<ExecutionResponse> {
  return request<ExecutionResponse>("/execute", { method: "POST", body: JSON.stringify(body) });
}

export async function compare(
  input: string,
  mode: "raw_vs_optimized" | "models",
  modelIds: string[] = [],
): Promise<CompareResponse> {
  return request<CompareResponse>("/compare", {
    method: "POST",
    body: JSON.stringify({ input, compare: mode, model_ids: modelIds }),
  });
}

// ---------------------------------------------------------------------------------------
// Catalog
// ---------------------------------------------------------------------------------------
export async function listModels(): Promise<ModelResponse[]> {
  return request<ModelResponse[]>("/models");
}

export async function listStaleModels(): Promise<ModelResponse[]> {
  return request<ModelResponse[]>("/models-status/stale");
}

// ---------------------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------------------
export async function listPrompts(): Promise<PromptResponse[]> {
  return request<PromptResponse[]>("/prompts");
}

export async function createPrompt(input: {
  name: string;
  description?: string;
  system_text?: string;
  user_text?: string;
  task_type?: string;
}): Promise<PromptResponse> {
  return request<PromptResponse>("/prompts", { method: "POST", body: JSON.stringify(input) });
}

export async function listPromptVersions(promptId: string): Promise<PromptVersionResponse[]> {
  return request<PromptVersionResponse[]>(`/prompts/${promptId}/versions`);
}

export async function createPromptVersion(
  promptId: string,
  input: { system_text?: string; user_text?: string; notes?: string },
): Promise<PromptVersionResponse> {
  return request<PromptVersionResponse>(`/prompts/${promptId}/versions`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function activatePromptVersion(
  promptId: string,
  versionId: string,
): Promise<PromptVersionResponse> {
  return request<PromptVersionResponse>(`/prompts/${promptId}/activate/${versionId}`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------------------
// History / feedback / analytics
// ---------------------------------------------------------------------------------------
export async function listExecutions(limit = 50): Promise<ExecutionSummary[]> {
  return request<ExecutionSummary[]>(`/executions?limit=${limit}`);
}

export async function getExecution(id: string): Promise<Record<string, unknown>> {
  return request(`/executions/${id}`);
}

export async function submitFeedback(input: {
  execution_id: string;
  rating: "up" | "down";
  reason?: string;
  comment?: string;
}): Promise<{ status: string; note: string }> {
  return request("/feedback", { method: "POST", body: JSON.stringify(input) });
}

export async function getAnalytics(days = 30): Promise<AnalyticsResponse> {
  return request<AnalyticsResponse>(`/analytics?days=${days}`);
}

// ---------------------------------------------------------------------------------------
// API keys
// ---------------------------------------------------------------------------------------
export async function createApiKey(name: string): Promise<ApiKeyCreatedResponse> {
  return request<ApiKeyCreatedResponse>("/api-keys", { method: "POST", body: JSON.stringify({ name }) });
}

export async function listApiKeys(): Promise<ApiKeySummary[]> {
  return request<ApiKeySummary[]>("/api-keys");
}

export async function revokeApiKey(keyId: string): Promise<void> {
  await request<void>(`/api-keys/${keyId}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------------------
// Admin: members
// ---------------------------------------------------------------------------------------
export async function listMembers(): Promise<MemberResponse[]> {
  return request<MemberResponse[]>("/admin/members");
}

export async function addMember(email: string, role: OrgRole = "member"): Promise<MemberResponse> {
  return request<MemberResponse>("/admin/members", {
    method: "POST",
    body: JSON.stringify({ email, role }),
  });
}

export async function updateMemberRole(userId: string, role: OrgRole): Promise<MemberResponse> {
  return request<MemberResponse>(`/admin/members/${userId}`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

export async function removeMember(userId: string): Promise<void> {
  await request<void>(`/admin/members/${userId}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------------------
// Admin: organization settings
// ---------------------------------------------------------------------------------------
export async function getOrganizationSettings(): Promise<OrgSettingsResponse> {
  return request<OrgSettingsResponse>("/admin/organization");
}

export async function updateOrganizationSettings(input: {
  name?: string;
  monthly_budget_usd?: number;
  retention_days?: number;
  store_raw_prompts?: boolean;
}): Promise<OrgSettingsResponse> {
  return request<OrgSettingsResponse>("/admin/organization", {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

// ---------------------------------------------------------------------------------------
// Admin: audit log
// ---------------------------------------------------------------------------------------
export async function listAuditLogs(opts: {
  limit?: number;
  offset?: number;
  action?: string;
} = {}): Promise<AuditLogEntry[]> {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  if (opts.action) params.set("action", opts.action);
  const qs = params.toString();
  return request<AuditLogEntry[]>(`/admin/audit-logs${qs ? `?${qs}` : ""}`);
}

// ---------------------------------------------------------------------------------------
// Admin: provider accounts (bring-your-own provider keys)
// ---------------------------------------------------------------------------------------
export async function listProviderAccounts(): Promise<ProviderAccountResponse[]> {
  return request<ProviderAccountResponse[]>("/admin/provider-accounts");
}

export async function createProviderAccount(input: {
  provider: string;
  label?: string;
  api_key: string;
}): Promise<ProviderAccountResponse> {
  return request<ProviderAccountResponse>("/admin/provider-accounts", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function deleteProviderAccount(accountId: string): Promise<void> {
  await request<void>(`/admin/provider-accounts/${accountId}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------------------
// Account self-service
// ---------------------------------------------------------------------------------------
export async function updateProfile(input: {
  display_name?: string;
  memory_enabled?: boolean;
}): Promise<UserResponse> {
  return request<UserResponse>("/account/profile", { method: "PATCH", body: JSON.stringify(input) });
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  await request<void>("/account/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

export async function exportAccountData(): Promise<DataExportResponse> {
  return request<DataExportResponse>("/account/export");
}

export async function deleteAccount(): Promise<void> {
  await request<void>("/account", { method: "DELETE" });
  setTokens(null);
}
