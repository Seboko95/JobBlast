export type Job = {
  id: string;
  title?: string;
  company?: string;
  location?: string;
  source_name?: string;
  portal_type?: string;
  closing_date?: string;
  fit_score?: number;
  state?: string;
  is_new?: boolean;
  public_service?: boolean;
  apply_url?: string;
  listing_url?: string;
  application_method_label?: string;
  recommended_next_action?: string;
  why_match?: string[];
  required_docs?: string[];
  missing_docs?: string[];
  notes?: string[];
  cover_letter_ready?: boolean;
};

export type HomePayload = {
  user_id: number;
  profile: Record<string, unknown>;
  counts: Record<string, number>;
  source_stats: Record<string, number>;
  resume_status: string;
  document_summary: string;
};

export type Source = {
  id: string;
  name?: string;
  enabled?: boolean;
  scan_mode?: string;
  last_result?: string;
  scan_count?: number;
  success_count?: number;
  warning_count?: number;
  error_count?: number;
  total_new_jobs?: number;
  total_matched_jobs?: number;
};

export type DocumentItem = {
  id: string;
  original_name?: string;
  doc_type?: string;
  active?: boolean;
  tags?: string[];
};

export type ApiSettings = {
  baseUrl: string;
  token: string;
};

