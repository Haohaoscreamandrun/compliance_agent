export type DocType = 'label' | 'msds' | 'change_cert' | 'official_letter';

export type DiffCategory =
  | 'factory_source_change'
  | 'entity_contact_update'
  | 'ghs_hazard_drift'
  | 'moa_registry_sync'
  | 'aphia_label_instruction'
  | 'official_letter_mandate'
  | 'common_name_mismatch'
  | 'crop_usage_sync';

export type ActionStatus =
  | 'must_update'
  | 'pending_moa_approval'
  | 'verify_warning';

export type OverallComplianceStatus =
  | 'compliant'
  | 'action_required'
  | 'pending_moa_approval'
  | 'needs_verification';

export type Box2D = [number, number, number, number];

export interface CitationRef {
  doc_id: string;
  page: number;
  box_2d: Box2D;
  verbatim_quote: string;
  extra_boxes_2d: Box2D[];
}

export interface ComplianceChecklistItem {
  field_name: string;
  category: DiffCategory;
  action_status: ActionStatus;
  current_label_value: string;
  recommended_new_value: string;
  rationale: string;
  current_label_citation: CitationRef | null;
  supporting_citations: CitationRef[];
}

export interface ComplianceAuditReport {
  reg_no: string;
  product_name_zh: string | null;
  product_name_en: string | null;
  overall_status: OverallComplianceStatus;
  executive_summary_zh: string;
  executive_summary_en: string;
  checklist_items: ComplianceChecklistItem[];
  moa_snapshot_stale: boolean;
  msds_doc_id_used: string | null;
  audit_warnings: string[];
  from_cache?: boolean;
}

export interface RegistryLookupResponse {
  reg_no: string;
  permit_type: string;
  permit_number: string;
  chinese_name: string | null;
  english_name: string | null;
  pesticide_code: string | null;
  brand_name: string | null;
  company_name: string | null;
  foreign_manufacturer: string | null;
  formulation: string | null;
  content_pct: string | null;
  valid_until_roc: string | null;
  revocation_status: string | null;
  stale: boolean;
  fetched_at: string;
}

export interface DocumentSummary {
  doc_id: string;
  filename: string;
  doc_type: DocType;
  reg_no: string | null;
  volume_tag?: string;
  common_name: string | null;
  is_active_baseline: boolean;
  page_count: number;
  uploaded_at: string;
}

export interface EntityAlias {
  id: number;
  zh_name: string;
  en_name: string;
  confirmed: boolean;
}

export interface DocumentUploadResponse extends DocumentSummary {
  model_used: string;
  used_fallback: boolean;
  extracted_data: Record<string, unknown>;
}

export interface AphiaRecordItem {
  source_kind: string;
  source_key: string;
  title: string;
  common_name: string | null;
  published_date: string | null;
  detail_url: string | null;
}

export interface AphiaLookupResponse {
  reg_no: string;
  chinese_name: string | null;
  keywords: string[];
  carcinogen_matches: AphiaRecordItem[];
  assessment_matches: AphiaRecordItem[];
  latest_pre_notice: AphiaRecordItem | null;
  evaluation_submitted_date: string | null;
  stale: boolean;
  fetched_at: string;
}

export interface EvaluationSubmissionResponse {
  reg_no: string;
  common_name: string;
  submitted_date: string;
  notes: string | null;
  updated_at: string;
}

export interface PendingAccessRequest {
  id: number;
  requested_company_name: string;
  status: string;
  created_at: string;
}

export interface MeResponse {
  authenticated: boolean;
  email: string | null;
  company_id: string | null;
  company_name: string | null;
  role: string | null;
  pending_request: PendingAccessRequest | null;
}

export interface AccessRequestOut {
  id: number;
  email: string;
  requested_company_name: string;
  note: string | null;
  status: string;
  assigned_company_id: string | null;
  created_at: string;
}
