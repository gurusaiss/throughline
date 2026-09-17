// Mirrors backend/app/api/v1/schemas.py. Kept hand-in-sync deliberately rather than codegen'd, because
// the response shapes double as documentation of the honesty contract (every field here is something the
// backend promises never to fabricate — see WhyResponse, EvaluationResponse, CostResponse).

export type OrchestrationMode = "auto" | "fast" | "balanced" | "deep" | "expert";

export interface AppliedTechnique {
  technique: string;
  reason: string;
  trigger: string;
}

export interface WhyResponse {
  mode: string;
  mode_reason: string;
  task_type: string;
  complexity: string;
  ambiguity: string;
  risk_level: string;
  applied: AppliedTechnique[];
  skipped: string[];
  rationale: string;
  signal_confidence: number;
  inferred_fields: Record<string, string>;
}

export interface CompiledPromptResponse {
  system: string;
  user: string;
  output_schema: Record<string, unknown> | null;
}

export interface ModelChoiceResponse {
  model_id: string;
  provider: string;
  role: string;
  reason: string;
}

export interface RoutingResponse {
  strategy: string;
  selections: ModelChoiceResponse[];
  reason: string;
  estimated_cost_usd: number;
  estimated_latency_ms: number;
  cost_is_estimate: boolean;
}

export interface CheckResponse {
  name: string;
  status: "pass" | "fail" | "skipped" | "error";
  detail: string;
  criterion: string;
}

export interface ScoreResponse {
  criterion: string;
  score: number;
  source: "deterministic" | "judge" | "human";
  reasoning: string;
}

export interface CandidateResponse {
  id: string;
  model_id: string;
  provider: string;
  text: string;
  cost_usd: number;
  latency_ms: number;
  from_cache: boolean;
  error: string | null;
  checks: CheckResponse[];
  scores: ScoreResponse[];
  overall: number | null;
}

export interface MitigationsResponse {
  order_randomized: boolean;
  model_identity_hidden: boolean;
  judge_family: string;
  self_preference_risk: boolean;
  length_delta_chars: number;
}

export interface EvaluationResponse {
  status: string;
  selected_candidate_id: string | null;
  selection_reason: string;
  model_agreement: number | null;
  judge_agreement: number | null;
  mitigations: MitigationsResponse | null;
  cost_usd: number;
  note: string;
}

export interface CostResponse {
  total_usd: number;
  generation_usd: number;
  orchestration_usd: number;
  orchestration_ratio: number;
  input_tokens: number;
  output_tokens: number;
  llm_calls: number;
  is_estimate: boolean;
  includes_unverified_pricing: boolean;
}

export interface ExecutionResponse {
  execution_id: string;
  status: string;
  output: string;
  generated_by: "ai";
  confidence: "low" | "moderate" | "high";
  confidence_note: string;
  why: WhyResponse;
  prompt: CompiledPromptResponse;
  routing: RoutingResponse | null;
  candidates: CandidateResponse[];
  evaluation: EvaluationResponse | null;
  cost: CostResponse;
  latency_ms: number;
  warnings: string[];
  error: string | null;
}

export interface OptimizeResponse {
  status: string;
  prompt: CompiledPromptResponse;
  why: WhyResponse;
  routing: RoutingResponse | null;
  estimated_cost_usd: number;
}

export interface ComparisonArm {
  label: string;
  prompt: CompiledPromptResponse;
  output: string;
  model_id: string;
  cost_usd: number;
  latency_ms: number;
  checks_passed: number;
  checks_failed: number;
  overall: number | null;
}

export interface CompareResponse {
  comparison_id: string;
  arms: ComparisonArm[];
  verdict: string;
  caveat: string;
}

export interface PricingResponse {
  input_per_mtok: number;
  output_per_mtok: number;
  cache_read_per_mtok: number | null;
  verified: boolean;
  verified_at: string | null;
  source: string;
  staleness_warning: string;
}

export interface ModelResponse {
  id: string;
  provider: string;
  display_name: string;
  family: string;
  tier: string;
  context_window: number;
  max_output_tokens: number;
  modalities: string[];
  capabilities: string[];
  quality_index: number;
  speed_index: number;
  pricing: PricingResponse;
  available: boolean;
  configured: boolean;
}

export interface PromptVersionResponse {
  id: string;
  version: number;
  system_text: string;
  user_text: string;
  output_schema: Record<string, unknown> | null;
  notes: string;
  created_at: string;
  is_active: boolean;
}

export interface PromptResponse {
  id: string;
  name: string;
  description: string;
  task_type: string;
  version_count: number;
  active_version: PromptVersionResponse | null;
  created_at: string;
  updated_at: string;
}

export interface ExecutionSummary {
  id: string;
  created_at: string;
  status: string;
  mode: string;
  task_type: string;
  selected_model_id: string;
  cost_usd: number;
  latency_ms: number;
  llm_calls: number;
  confidence: string;
  preview: string;
}

export interface AnalyticsResponse {
  period_days: number;
  executions: number;
  total_cost_usd: number;
  generation_cost_usd: number;
  orchestration_cost_usd: number;
  orchestration_ratio: number;
  avg_latency_ms: number;
  avg_llm_calls: number;
  mode_distribution: Record<string, number>;
  task_distribution: Record<string, number>;
  model_distribution: Record<string, number>;
  feedback_positive: number;
  feedback_negative: number;
  budget_limit_usd: number;
  budget_spent_usd: number;
  note: string;
}

export interface UserResponse {
  id: string;
  email: string;
  display_name: string;
  org_id: string;
  org_name: string;
  role: string;
  memory_enabled: boolean;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  detail?: string;
}
