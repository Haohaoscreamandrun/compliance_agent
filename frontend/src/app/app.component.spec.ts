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

  it('loads all documents and aliases on init with sidenav closed', () => {
    fixture.detectChanges();
    expect(component.sidenavOpen()).toBeFalse();

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

    component.onSelectCitation({
      doc_id: 'loa_multi',
      page: 2,
      box_2d: [10, 10, 50, 50],
      verbatim_quote: 'Sulfur',
      extra_boxes_2d: [],
    });
    expect(component.viewerTotalPages()).toBe(4);
    expect(component.viewerPage()).toBe(2);
  });

  it('toggles theme cleanly and cleans up backdrop on destroy', () => {
    fixture.detectChanges();
    httpMock.expectOne('/api/documents?limit=50').flush([]);
    httpMock.expectOne('/api/entity-aliases').flush([]);

    const initial = component.theme();
    component.toggleTheme();
    const toggled = component.theme();
    expect(toggled).not.toBe(initial);
    expect(document.documentElement.getAttribute('data-theme')).toBe(toggled);

    expect(() => fixture.destroy()).not.toThrow();
  });

  it('loadBaselines fetches MOA and active baseline docs and reveals audit trigger', () => {
    fixture.detectChanges();
    httpMock.expectOne('/api/documents?limit=50').flush([]);
    httpMock.expectOne('/api/entity-aliases').flush([]);

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
    expect(component.baselineDocs().length).toBe(1);

    httpMock
      .expectOne('/api/copilot/audit/%E8%BE%B2%E8%97%A5%E9%80%B200569')
      .flush(null, { status: 404, statusText: 'Not Found' });
  });

  it('formats 422 array errors safely in runAudit', () => {
    fixture.detectChanges();
    httpMock.expectOne('/api/documents?limit=50').flush([]);
    httpMock.expectOne('/api/entity-aliases').flush([]);

    component.runAudit();
    const auditReq = httpMock.expectOne('/api/copilot/audit/%E8%BE%B2%E8%97%A5%E9%80%B200569');
    auditReq.flush({ detail: [{ msg: 'Invalid reg_no' }] }, { status: 422, statusText: 'Unprocessable' });
    expect(component.auditError()).toBe('Invalid reg_no');
  });
});
