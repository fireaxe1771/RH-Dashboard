import { getAuthToken } from './api';

export interface AiAdoptionSummary {
  active_departments: number;
  departments_using_ai: number;
  departments_not_using_ai: number;
  departments_unknown: number;
  total_drafts: number;
  ai_department_drafts: number;
  non_ai_department_drafts: number;
  unknown_department_drafts: number;
  ai_coverage_percent: number;
  remaining_opportunity_percent: number;
}

export interface AiAdoptionDepartment {
  rank_overall: number;
  department_id: string;
  department_name: string | null;
  state: string | null;
  drafts: number;
  percent_of_total_volume: number;
  ai_status: string;
  ai_mode: string;
  qualifying_fee_count: number;
  has_auto: boolean;
  has_queued: boolean;
  has_limited_auto: boolean;
}

export interface AiAdoptionResponse {
  period: { start_date: string; end_date: string };
  ai_status_basis: string;
  summary: AiAdoptionSummary;
  departments: AiAdoptionDepartment[];
}

const BASE_URL = '/api/ai-adoption';

async function aiAdoptionFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const err = await response.json();
      detail = err.detail || detail;
    } catch {
      // keep default message
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const aiAdoptionApi = {
  getDepartments: (
    startDate: string,
    endDate: string,
    limit = 50,
    aiStatus = 'all'
  ): Promise<AiAdoptionResponse> => {
    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
      limit: String(limit),
      ai_status: aiStatus,
    });
    return aiAdoptionFetch<AiAdoptionResponse>(`/departments?${params.toString()}`);
  },
};
