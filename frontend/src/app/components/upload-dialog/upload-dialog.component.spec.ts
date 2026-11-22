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

  it('auto-fills regNo only for label docType and validates missing file', () => {
    expect(component.regNo()).toBe('農藥進00569');
    component.onDocTypeChange('change_cert');
    expect(component.regNo()).toBe('');
    component.onDocTypeChange('label');
    expect(component.regNo()).toBe('農藥進00569');

    component.submit(new Event('submit'));
    expect(component.errorMessage()).toContain('請先選擇');
  });

  it('uploads file and closes dialog with response', () => {
    const file = new File(['abc'], 'label.webp', { type: 'image/webp' });
    component.selectedFile.set(file);
    component.submit(new Event('submit'));

    const req = httpMock.expectOne('/api/documents/upload');
    expect(req.request.method).toBe('POST');
    const mockRes = {
      doc_id: 'label_1',
      filename: 'label.webp',
      doc_type: 'label' as const,
      reg_no: '農藥進00569',
      common_name: 'Sulfur',
      is_active_baseline: true,
      page_count: 1,
      uploaded_at: '2026-11-22T00:00:00Z',
      model_used: 'gemini-3.8-flash',
      used_fallback: false,
      extracted_data: {},
    };
    req.flush(mockRes);
    expect(dialogRefSpy.close).toHaveBeenCalledWith(mockRes);
  });
});
