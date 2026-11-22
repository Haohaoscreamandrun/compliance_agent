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

  it('loads all documents on init with sidenav closed by default', () => {
    fixture.detectChanges();
    expect(component.sidenavOpen()).toBeFalse();

    const req = httpMock.expectOne('/api/documents?limit=50');
    expect(req.request.method).toBe('GET');
    req.flush([
      {
        doc_id: 'loa_multi',
        filename: 'loa.pdf',
        doc_type: 'change_cert',
        reg_no: null,
        common_name: 'Sulfur',
        is_active_baseline: false,
        page_count: 4,
        uploaded_at: '2026-11-21T00:00:00Z',
      },
    ]);

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

  it('formats 422 array errors safely in runAudit', () => {
    fixture.detectChanges();
    httpMock.expectOne('/api/documents?limit=50').flush([]);

    component.runAudit();
    const auditReq = httpMock.expectOne('/api/copilot/audit/%E8%BE%B2%E8%97%A5%E9%80%B200569');
    auditReq.flush({ detail: [{ msg: 'Invalid reg_no' }] }, { status: 422, statusText: 'Unprocessable' });
    expect(component.auditError()).toBe('Invalid reg_no');
  });
});
