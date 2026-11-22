import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';

import { UploadDialogComponent } from './upload-dialog.component';

describe('UploadDialogComponent', () => {
  let fixture: ComponentFixture<UploadDialogComponent>;
  let component: UploadDialogComponent;
  let httpMock: HttpTestingController;
  let dialogRefSpy: jasmine.SpyObj<MatDialogRef<UploadDialogComponent>>;

  beforeEach(async () => {
    dialogRefSpy = jasmine.createSpyObj('MatDialogRef', ['close']);

    await TestBed.configureTestingModule({
      imports: [UploadDialogComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: MatDialogRef, useValue: dialogRefSpy },
        { provide: MAT_DIALOG_DATA, useValue: { defaultRegNo: '農藥進00569' } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(UploadDialogComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('supports adding/removing batch entries, label-only regNo auto-fill, and missing file validation', async () => {
    const firstId = component.entries()[0].id;
    expect(component.entries()[0].regNo).toBe('農藥進00569');
    component.onDocTypeChange(firstId, 'change_cert');
    expect(component.entries()[0].regNo).toBe('');
    component.onDocTypeChange(firstId, 'label');
    expect(component.entries()[0].regNo).toBe('農藥進00569');

    component.addEntry();
    expect(component.entries().length).toBe(2);
    component.removeEntry(component.entries()[1].id);
    expect(component.entries().length).toBe(1);

    await component.submit(new Event('submit'));
    expect(component.errorMessage()).toContain('請為每一筆上傳項目選擇');
  });

  it('batch-uploads entries and closes dialog with all responses', async () => {
    component.addEntry();
    const [e1, e2] = component.entries();
    expect(component.selectedFileCount()).toBe(0);
    component.onFieldChange(e1.id, 'selectedFile', new File(['a'], 'label1.webp', { type: 'image/webp' }));
    component.onFieldChange(e2.id, 'selectedFile', new File(['b'], 'label2.webp', { type: 'image/webp' }));
    expect(component.selectedFileCount()).toBe(2);

    const submitPromise = component.submit(new Event('submit'));
    await Promise.resolve();

    httpMock
      .expectOne('/api/documents/exists?filename=label1.webp&doc_type=label&reg_no=%E8%BE%B2%E8%97%A5%E9%80%B200569')
      .flush({ exists: false, doc_id: null });
    await Promise.resolve();

    const r1 = {
      doc_id: 'label_1',
      filename: 'label1.webp',
      doc_type: 'label' as const,
      reg_no: '農藥進00569',
      common_name: 'Sulfur',
      is_active_baseline: false,
      page_count: 1,
      uploaded_at: '2026-11-22T00:00:00Z',
      model_used: 'gemini-3.8-flash',
      used_fallback: false,
      extracted_data: {},
    };
    httpMock.expectOne('/api/documents/upload').flush(r1);
    await Promise.resolve();

    httpMock
      .expectOne('/api/documents/exists?filename=label2.webp&doc_type=label&reg_no=%E8%BE%B2%E8%97%A5%E9%80%B200569')
      .flush({ exists: false, doc_id: null });
    await Promise.resolve();

    const r2 = { ...r1, doc_id: 'label_2', filename: 'label2.webp' };
    httpMock.expectOne('/api/documents/upload').flush(r2);
    await submitPromise;

    expect(dialogRefSpy.close).toHaveBeenCalledWith([r1, r2]);
  });
});
