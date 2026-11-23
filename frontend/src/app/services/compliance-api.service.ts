import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import {
  AphiaLookupResponse,
  ComplianceAuditReport,
  DocType,
  DocumentSummary,
  DocumentUploadResponse,
  EntityAlias,
  EvaluationSubmissionResponse,
  RegistryLookupResponse,
} from '../models/compliance.models';

export interface DocumentFilterParams {
  reg_no?: string;
  doc_type?: DocType | '';
  is_active_baseline?: boolean;
  include_shared_certs?: boolean;
  common_name?: string;
  product_internal_code?: string;
  limit?: number;
  offset?: number;
}

@Injectable({ providedIn: 'root' })
export class ComplianceApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = '/api';

  lookupRegistry(regNo: string): Observable<RegistryLookupResponse> {
    return this.http.get<RegistryLookupResponse>(
      `${this.baseUrl}/registry/${encodeURIComponent(regNo.trim())}`,
    );
  }

  lookupAphia(regNo: string, refresh = false): Observable<AphiaLookupResponse> {
    const params = refresh ? new HttpParams().set('refresh', 'true') : undefined;
    return this.http.get<AphiaLookupResponse>(
      `${this.baseUrl}/registry/${encodeURIComponent(regNo.trim())}/aphia`,
      { params },
    );
  }

  saveAphiaEvaluation(
    regNo: string,
    submittedDate: string,
    notes?: string,
  ): Observable<EvaluationSubmissionResponse> {
    return this.http.post<EvaluationSubmissionResponse>(
      `${this.baseUrl}/registry/${encodeURIComponent(regNo.trim())}/aphia/evaluation`,
      { submitted_date: submittedDate, notes: notes ?? null },
    );
  }

  listDocuments(filters: DocumentFilterParams = {}): Observable<DocumentSummary[]> {
    let params = new HttpParams();
    if (filters.reg_no?.trim()) {
      params = params.set('reg_no', filters.reg_no.trim());
    }
    if (filters.doc_type) {
      params = params.set('doc_type', filters.doc_type);
    }
    if (filters.is_active_baseline !== undefined) {
      params = params.set('is_active_baseline', String(filters.is_active_baseline));
    }
    if (filters.include_shared_certs !== undefined) {
      params = params.set('include_shared_certs', String(filters.include_shared_certs));
    }
    if (filters.common_name?.trim()) {
      params = params.set('common_name', filters.common_name.trim());
    }
    if (filters.product_internal_code?.trim()) {
      params = params.set('product_internal_code', filters.product_internal_code.trim());
    }
    if (filters.limit !== undefined) {
      params = params.set('limit', filters.limit);
    }
    if (filters.offset !== undefined) {
      params = params.set('offset', filters.offset);
    }
    return this.http.get<DocumentSummary[]>(`${this.baseUrl}/documents`, { params });
  }

  checkDocumentExists(
    filename: string,
    docType: DocType,
    regNo?: string,
  ): Observable<{ exists: boolean; doc_id: string | null }> {
    let params = new HttpParams().set('filename', filename).set('doc_type', docType);
    if (regNo?.trim()) {
      params = params.set('reg_no', regNo.trim());
    }
    return this.http.get<{ exists: boolean; doc_id: string | null }>(
      `${this.baseUrl}/documents/exists`,
      { params },
    );
  }

  uploadDocument(
    file: File,
    docType: DocType,
    options: {
      reg_no?: string;
      volume_tag?: string;
      manual_notes?: string;
      force_reparse?: boolean;
    } = {},
  ): Observable<DocumentUploadResponse> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('doc_type', docType);
    if (options.reg_no?.trim()) {
      formData.append('reg_no', options.reg_no.trim());
    }
    if (options.volume_tag?.trim()) {
      formData.append('volume_tag', options.volume_tag.trim());
    }
    if (options.manual_notes?.trim()) {
      formData.append('manual_notes', options.manual_notes.trim());
    }
    if (options.force_reparse) {
      formData.append('force_reparse', 'true');
    }
    return this.http.post<DocumentUploadResponse>(`${this.baseUrl}/documents/upload`, formData);
  }

  toggleBaseline(docId: string, isActiveBaseline: boolean): Observable<DocumentSummary> {
    return this.http.patch<DocumentSummary>(
      `${this.baseUrl}/documents/${encodeURIComponent(docId)}/baseline`,
      { is_active_baseline: isActiveBaseline },
    );
  }

  getPageImageUrl(docId: string, pageNumber: number): string {
    return `${this.baseUrl}/documents/${encodeURIComponent(docId)}/pages/${pageNumber}/image`;
  }

  getCachedAuditReport(regNo: string): Observable<ComplianceAuditReport> {
    return this.http.get<ComplianceAuditReport>(
      `${this.baseUrl}/copilot/audit/${encodeURIComponent(regNo.trim())}`,
    );
  }

  runComplianceAudit(regNo: string, forceRefresh = false): Observable<ComplianceAuditReport> {
    const params = forceRefresh ? new HttpParams().set('force_refresh', 'true') : undefined;
    return this.http.post<ComplianceAuditReport>(
      `${this.baseUrl}/copilot/audit/${encodeURIComponent(regNo.trim())}`,
      {},
      { params },
    );
  }

  listEntityAliases(): Observable<EntityAlias[]> {
    return this.http.get<EntityAlias[]>(`${this.baseUrl}/entity-aliases`);
  }

  upsertEntityAlias(zhName: string, enName: string, confirmed: boolean): Observable<EntityAlias> {
    return this.http.post<EntityAlias>(`${this.baseUrl}/entity-aliases`, {
      zh_name: zhName,
      en_name: enName,
      confirmed,
    });
  }
}

export function extractApiErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { error?: { detail?: unknown } } | null)?.error?.detail;
  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const msgs = detail
      .map((item) =>
        typeof item === 'object' && item !== null && 'msg' in item
          ? String((item as { msg: unknown }).msg)
          : String(item),
      )
      .filter(Boolean);
    if (msgs.length > 0) {
      return msgs.join('; ');
    }
  }
  return fallback;
}
