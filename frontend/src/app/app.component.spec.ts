import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { AppComponent } from './app.component';

describe('AppComponent', () => {
  let fixture: ComponentFixture<AppComponent>;
  let component: AppComponent;
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();

    fixture = TestBed.createComponent(AppComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  function flushInitMe(): void {
    httpMock.expectOne('/api/me').flush({
      authenticated: true,
      email: 'local@localhost',
      company_id: 'default',
      company_name: 'Default Workspace',
      role: 'admin',
      pending_request: null,
    });
  }

  it('loads all documents and aliases on init with sidenav closed and empty regNo', () => {
    fixture.detectChanges();
    flushInitMe();
    expect(component.sidenavOpen()).toBeFalse();
    expect(component.regNo()).toBe('');

    const docReq = httpMock.expectOne('/api/documents?limit=50');
    expect(docReq.request.method).toBe('GET');
    docReq.flush([
      {
        doc_id: 'loa_multi',
        filename: 'loa.pdf',
        doc_type: 'change_cert',
        reg_no: null,
        volume_tag: '',
        common_name: 'Sulfur',
        is_active_baseline: false,
        page_count: 4,
        uploaded_at: '2026-11-21T00:00:00Z',
      },
    ]);

    const aliasReq = httpMock.expectOne('/api/entity-aliases');
    aliasReq.flush([]);

    component.onSelectCitations([
      {
        doc_id: 'loa_multi',
        page: 2,
        box_2d: [10, 10, 50, 50],
        verbatim_quote: 'Sulfur',
        extra_boxes_2d: [],
      },
      {
        doc_id: 'label_1',
        page: 1,
        box_2d: [20, 20, 60, 60],
        verbatim_quote: 'Label Sulfur',
        extra_boxes_2d: [],
      },
    ]);
    expect(component.compareCitations().length).toBe(2);
    expect(component.docLabelsById()['loa_multi']).toBe('loa.pdf (change_cert)');

    component.onOpenFullCitation({
      doc_id: 'loa_multi',
      page: 2,
      box_2d: [10, 10, 50, 50],
      verbatim_quote: 'Sulfur',
      extra_boxes_2d: [],
    });
    expect(component.compareCitations().length).toBe(0);
    expect(component.viewerTotalPages()).toBe(4);
    expect(component.viewerPage()).toBe(2);
  });

  it('toggles theme cleanly and cleans up backdrop on destroy', () => {
    fixture.detectChanges();
    flushInitMe();
    httpMock.expectOne('/api/documents?limit=50').flush([]);
    httpMock.expectOne('/api/entity-aliases').flush([]);

    const initial = component.theme();
    component.toggleTheme();
    const toggled = component.theme();
    expect(toggled).not.toBe(initial);
    expect(document.documentElement.getAttribute('data-theme')).toBe(toggled);

    expect(() => fixture.destroy()).not.toThrow();
  });

  it('loads APHIA details after MOA lookup without blocking on APHIA error', () => {
    fixture.detectChanges();
    flushInitMe();
    httpMock.expectOne('/api/documents?limit=50').flush([]);
    httpMock.expectOne('/api/entity-aliases').flush([]);

    component.regNo.set('農藥進00569');
    component.loadBaselines();
    httpMock.expectOne('/api/registry/%E8%BE%B2%E8%97%A5%E9%80%B200569').flush({
      reg_no: '農藥進00569',
      permit_type: '農藥進',
      permit_number: '00569',
      chinese_name: '嘉磷塞',
      english_name: 'Glyphosate',
      stale: false,
      fetched_at: '2026-11-23T00:00:00Z',
    });
    httpMock
      .expectOne(
        '/api/documents?reg_no=%E8%BE%B2%E8%97%A5%E9%80%B200569&is_active_baseline=true&include_shared_certs=true&limit=50',
      )
      .flush([]);
    httpMock
      .expectOne('/api/registry/%E8%BE%B2%E8%97%A5%E9%80%B200569/aphia')
      .flush('upstream error', { status: 503, statusText: 'Service Unavailable' });
    httpMock
      .expectOne('/api/copilot/audit/%E8%BE%B2%E8%97%A5%E9%80%B200569')
      .flush(null, { status: 404, statusText: 'Not Found' });

    expect(component.moaSnapshot()?.chinese_name).toBe('嘉磷塞');
    expect(component.aphiaSnapshot()).toBeNull();
    expect(component.baselinesLoading()).toBeFalse();
  });

  it('loadBaselines fetches MOA and active baseline docs and reveals audit trigger', () => {
    fixture.detectChanges();
    flushInitMe();
    httpMock.expectOne('/api/documents?limit=50').flush([]);
    httpMock.expectOne('/api/entity-aliases').flush([]);

    component.regNo.set('農藥進00569');
    component.loadBaselines();
    expect(component.baselinesLoaded()).toBeTrue();

    httpMock.expectOne('/api/registry/%E8%BE%B2%E8%97%A5%E9%80%B200569').flush({
      reg_no: '農藥進00569',
      permit_type: '農藥進',
      permit_number: '00569',
      chinese_name: '可濕性硫黃',
      english_name: 'SULFUR',
      pesticide_code: 'F001',
      brand_name: '克魔力',
      company_name: '台灣巴斯夫股份有限公司',
      foreign_manufacturer: 'BASF',
      formulation: '水分散性粒劑',
      content_pct: '80%',
      valid_until_roc: '120/01/01',
      revocation_status: null,
      stale: false,
      fetched_at: '2026-11-22T00:00:00Z',
    });

    const baseReq = httpMock.expectOne(
      '/api/documents?reg_no=%E8%BE%B2%E8%97%A5%E9%80%B200569&is_active_baseline=true&include_shared_certs=true&limit=50',
    );
    baseReq.flush([
      {
        doc_id: 'lbl_1kg',
        filename: 'label_1kg.jpg',
        doc_type: 'label',
        reg_no: '農藥進00569',
        volume_tag: '1kg',
        common_name: '可濕性硫黃',
        is_active_baseline: true,
        page_count: 1,
        uploaded_at: '2026-11-22T00:00:00Z',
      },
    ]);
    httpMock
      .expectOne('/api/registry/%E8%BE%B2%E8%97%A5%E9%80%B200569/aphia')
      .flush({
        reg_no: '農藥進00569',
        chinese_name: '可濕性硫黃',
        keywords: ['可濕性硫黃', 'SULFUR'],
        carcinogen_matches: [],
        assessment_matches: [],
        latest_pre_notice: null,
        evaluation_submitted_date: null,
        stale: false,
        fetched_at: '2026-11-23T00:00:00Z',
      });
    expect(component.baselineDocs().length).toBe(1);

    httpMock
      .expectOne('/api/copilot/audit/%E8%BE%B2%E8%97%A5%E9%80%B200569')
      .flush(null, { status: 404, statusText: 'Not Found' });
  });

  it('formats 422 array errors safely in runAudit', () => {
    fixture.detectChanges();
    flushInitMe();
    httpMock.expectOne('/api/documents?limit=50').flush([]);
    httpMock.expectOne('/api/entity-aliases').flush([]);

    component.regNo.set('農藥進00569');
    component.runAudit();
    const auditReq = httpMock.expectOne('/api/copilot/audit/%E8%BE%B2%E8%97%A5%E9%80%B200569');
    auditReq.flush({ detail: [{ msg: 'Invalid reg_no' }] }, { status: 422, statusText: 'Unprocessable' });
    expect(component.auditError()).toBe('Invalid reg_no');
  });

  it('skips private loads for un-allowlisted users and supports tryDemoAudit', () => {
    fixture.detectChanges();
    httpMock.expectOne('/api/me').flush({
      authenticated: true,
      email: 'new@gmail.com',
      company_id: null,
      company_name: null,
      role: null,
      pending_request: null,
    });
    httpMock.expectOne('/api/documents?limit=50').flush([]);
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('申請加入公司');

    component.tryDemoAudit();
    fixture.detectChanges();
    expect(component.regNo()).toBe('農藥進99001');
    const demoBtn = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('button'),
    ).find((b) => b.textContent?.includes('試用範例')) as HTMLButtonElement | undefined;
    expect(demoBtn?.disabled).toBeTrue();

    component.loadBaselines();
    httpMock.expectOne('/api/registry/%E8%BE%B2%E8%97%A5%E9%80%B299001').flush({
      reg_no: '農藥進99001',
      permit_type: '農藥進',
      permit_number: '99001',
      chinese_name: '示範殺菌劑',
      english_name: 'DEMOFUNGIN',
      company_name: '示範農化股份有限公司',
      stale: false,
      fetched_at: '2026-11-26T00:00:00Z',
    });
    httpMock
      .expectOne(
        '/api/documents?reg_no=%E8%BE%B2%E8%97%A5%E9%80%B299001&is_active_baseline=true&include_shared_certs=true&limit=50',
      )
      .flush([]);
    httpMock.expectOne('/api/registry/%E8%BE%B2%E8%97%A5%E9%80%B299001/aphia').flush({
      reg_no: '農藥進99001',
      chinese_name: '示範殺菌劑',
      keywords: ['示範殺菌劑', 'DEMOFUNGIN'],
      carcinogen_matches: [],
      assessment_matches: [],
      latest_pre_notice: null,
      evaluation_submitted_date: null,
      stale: false,
      fetched_at: '2026-11-26T00:00:00Z',
    });
    expect(component.moaSnapshot()?.reg_no).toBe('農藥進99001');
    expect(component.auditReport()).toBeNull();

    component.runAudit();
    const demoAuditReq = httpMock.expectOne('/api/copilot/audit/%E8%BE%B2%E8%97%A5%E9%80%B299001');
    expect(demoAuditReq.request.method).toBe('GET');
    demoAuditReq.flush({
      reg_no: '農藥進99001',
      product_name_zh: '示範殺菌劑',
      product_name_en: 'DEMOFUNGIN',
      overall_status: 'compliant',
      executive_summary_zh: 'demo',
      executive_summary_en: 'demo',
      checklist_items: [],
    });
    expect(component.auditReport()?.reg_no).toBe('農藥進99001');
  });

  it('allows default admin to switch into public_demo workspace with header and warning banner', () => {
    fixture.detectChanges();
    httpMock.expectOne('/api/me').flush({
      authenticated: true,
      email: 'owner@example.com',
      company_id: 'default',
      company_name: 'Default Workspace',
      role: 'admin',
      can_manage_public_demo: true,
      pending_request: null,
    });
    httpMock.expectOne('/api/documents?limit=50').flush([]);
    httpMock.expectOne('/api/entity-aliases').flush([]);
    fixture.detectChanges();

    expect(component.canManagePublicDemo()).toBeTrue();
    expect(component.isPublicDemoMode()).toBeFalse();

    component.togglePublicDemoWorkspace();
    expect(component.isPublicDemoMode()).toBeTrue();
    expect(component.regNo()).toBe('農藥進99001');

    const meReq = httpMock.expectOne('/api/me');
    expect(meReq.request.headers.get('X-Workspace-Company-Id')).toBe('public_demo');
    meReq.flush({
      authenticated: true,
      email: 'owner@example.com',
      company_id: 'public_demo',
      company_name: 'Public Demo',
      role: 'admin',
      can_manage_public_demo: true,
      pending_request: null,
    });

    const docsReq = httpMock.expectOne('/api/documents?limit=50');
    expect(docsReq.request.headers.get('X-Workspace-Company-Id')).toBe('public_demo');
    docsReq.flush([]);

    const aliasReq = httpMock.expectOne('/api/entity-aliases');
    expect(aliasReq.request.headers.get('X-Workspace-Company-Id')).toBe('public_demo');
    aliasReq.flush([]);

    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('公開示範編輯模式');

    component.togglePublicDemoWorkspace();
    expect(component.isPublicDemoMode()).toBeFalse();
    expect(component.regNo()).toBe('');
    const backMe = httpMock.expectOne('/api/me');
    expect(backMe.request.headers.has('X-Workspace-Company-Id')).toBeFalse();
    backMe.flush({
      authenticated: true,
      email: 'owner@example.com',
      company_id: 'default',
      company_name: 'Default Workspace',
      role: 'admin',
      can_manage_public_demo: true,
      pending_request: null,
    });
    httpMock.expectOne('/api/documents?limit=50').flush([]);
    httpMock.expectOne('/api/entity-aliases').flush([]);
  });
});
