import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import {
  ComplianceAuditReport,
  DocType,
  DocumentSummary,
  DocumentUploadResponse,
  RegistryLookupResponse,
} from '../models/compliance.models';

export interface DocumentFilterParams {
  reg_no?: string;
  doc_type?: DocType | '';
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

  listDocuments(filters: DocumentFilterParams = {}): Observable<DocumentSummary[]> {
    let params = new HttpParams();
    if (filters.reg_no?.trim()) {
      params = params.set('reg_no', filters.reg_no.trim());
    }
    if (filters.doc_type) {
      params = params.set('doc_type', filters.doc_type);
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

  uploadDocument(
    file: File,
    docType: DocType,
    options: { reg_no?: string; manual_notes?: string } = {},
  ): Observable<DocumentUploadResponse> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('doc_type', docType);
    if (options.reg_no?.trim()) {
      formData.append('reg_no', options.reg_no.trim());
    }
    if (options.manual_notes?.trim()) {
      formData.append('manual_notes', options.manual_notes.trim());
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

  runComplianceAudit(regNo: string): Observable<ComplianceAuditReport> {
    return this.http.post<ComplianceAuditReport>(
      `${this.baseUrl}/copilot/audit/${encodeURIComponent(regNo.trim())}`,
      {},
    );
  }
}
