import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { ComplianceApiService } from './compliance-api.service';

describe('ComplianceApiService', () => {
  let service: ComplianceApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(ComplianceApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('encodes regNo in lookupRegistry and runComplianceAudit', () => {
    service.lookupRegistry(' 農藥進00569 ').subscribe();
    const regReq = httpMock.expectOne('/api/registry/%E8%BE%B2%E8%97%A5%E9%80%B200569');
    expect(regReq.request.method).toBe('GET');
    regReq.flush({});

    service.runComplianceAudit('農藥進00569').subscribe();
    const auditReq = httpMock.expectOne('/api/copilot/audit/%E8%BE%B2%E8%97%A5%E9%80%B200569');
    expect(auditReq.request.method).toBe('POST');
    auditReq.flush({});
  });

  it('passes non-empty filters to listDocuments and builds page image URL', () => {
    service.listDocuments({ common_name: 'Sulfur', product_internal_code: 'BAS 175', limit: 25 }).subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url === '/api/documents' &&
        r.params.get('common_name') === 'Sulfur' &&
        r.params.get('product_internal_code') === 'BAS 175' &&
        r.params.get('limit') === '25',
    );
    expect(req.request.method).toBe('GET');
    req.flush([]);

    expect(service.getPageImageUrl('doc_1/2', 3)).toBe('/api/documents/doc_1%2F2/pages/3/image');
  });

  it('uploads FormData and toggles baseline via PATCH', () => {
    const file = new File(['hello'], 'label.webp', { type: 'image/webp' });
    service.uploadDocument(file, 'label', { reg_no: '農藥進00569', manual_notes: 'test' }).subscribe();
    const uploadReq = httpMock.expectOne('/api/documents/upload');
    expect(uploadReq.request.method).toBe('POST');
    expect((uploadReq.request.body as FormData).get('doc_type')).toBe('label');
    expect((uploadReq.request.body as FormData).get('reg_no')).toBe('農藥進00569');
    uploadReq.flush({});

    service.toggleBaseline('doc_1', true).subscribe();
    const patchReq = httpMock.expectOne('/api/documents/doc_1/baseline');
    expect(patchReq.request.method).toBe('PATCH');
    expect(patchReq.request.body).toEqual({ is_active_baseline: true });
    patchReq.flush({});
  });
});
