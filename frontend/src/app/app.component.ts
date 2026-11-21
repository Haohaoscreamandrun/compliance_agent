import {
  Component,
  computed,
  DestroyRef,
  ElementRef,
  inject,
  OnInit,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatToolbarModule } from '@angular/material/toolbar';
import { Subscription } from 'rxjs';

import { BboxSvgViewerComponent } from './components/bbox-svg-viewer/bbox-svg-viewer.component';
import { CopilotAuditPanelComponent } from './components/copilot-audit-panel/copilot-audit-panel.component';
import {
  CitationRef,
  ComplianceAuditReport,
  DocType,
  DocumentSummary,
  RegistryLookupResponse,
} from './models/compliance.models';
import { ComplianceApiService } from './services/compliance-api.service';

@Component({
  selector: 'app-root',
  imports: [
    FormsModule,
    MatToolbarModule,
    MatButtonModule,
    BboxSvgViewerComponent,
    CopilotAuditPanelComponent,
  ],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent implements OnInit {
  private readonly api = inject(ComplianceApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly uploadDialogRef = viewChild<ElementRef<HTMLDialogElement>>('uploadDialog');
  private readonly uploadFileInputRef = viewChild<ElementRef<HTMLInputElement>>('uploadFileInput');

  private docsSub?: Subscription;
  private moaSub?: Subscription;
  private auditSub?: Subscription;
  private uploadSub?: Subscription;

  // Audit & Registry state
  readonly regNo = signal('農藥進00569');
  readonly moaSnapshot = signal<RegistryLookupResponse | null>(null);
  readonly auditReport = signal<ComplianceAuditReport | null>(null);
  readonly auditLoading = signal(false);
  readonly auditError = signal<string | null>(null);

  // Document Library & Search state
  readonly documents = signal<DocumentSummary[]>([]);
  readonly searchCommonName = signal('');
  readonly searchProductCode = signal('');
  readonly filterByRegNo = signal(false);
  readonly docsLoading = signal(false);
  readonly docsError = signal<string | null>(null);

  // Active BBox Viewer state
  readonly viewerDocId = signal<string | null>(null);
  readonly viewerPage = signal(1);
  readonly viewerTotalPages = signal(1);
  readonly viewerDocLabel = signal('');
  readonly activeCitation = signal<CitationRef | null>(null);

  // Native <dialog> Upload Modal state
  readonly uploadDocType = signal<DocType>('label');
  readonly uploadRegNo = signal('');
  readonly uploadNotes = signal('');
  readonly uploadFile = signal<File | null>(null);
  readonly uploading = signal(false);
  readonly uploadError = signal<string | null>(null);
  readonly uploadSuccess = signal<string | null>(null);

  readonly docsById = computed(() => {
    const map = new Map<string, DocumentSummary>();
    for (const d of this.documents()) {
      map.set(d.doc_id, d);
    }
    return map;
  });

  ngOnInit(): void {
    this.loadDocuments();
  }

  loadDocuments(): void {
    this.docsSub?.unsubscribe();
    this.docsLoading.set(true);
    this.docsError.set(null);
    this.docsSub = this.api
      .listDocuments({
        reg_no: this.filterByRegNo() ? this.regNo() || undefined : undefined,
        common_name: this.searchCommonName() || undefined,
        product_internal_code: this.searchProductCode() || undefined,
        limit: 50,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (docs) => {
          this.documents.set(docs);
          this.docsLoading.set(false);
        },
        error: (err) => {
          this.docsError.set(this.extractErrorMessage(err, '載入文件清單失敗'));
          this.docsLoading.set(false);
        },
      });
  }

  lookupMoa(): void {
    const target = this.regNo().trim();
    if (!target) return;
    this.moaSub?.unsubscribe();
    this.moaSnapshot.set(null);
    this.auditError.set(null);
    this.moaSub = this.api
      .lookupRegistry(target)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (snap) => this.moaSnapshot.set(snap),
        error: (err) => this.auditError.set(this.extractErrorMessage(err, 'MOA 查詢失敗')),
      });
  }

  runAudit(): void {
    const target = this.regNo().trim();
    if (!target) return;
    this.auditSub?.unsubscribe();
    this.auditReport.set(null);
    this.auditLoading.set(true);
    this.auditError.set(null);
    this.auditSub = this.api
      .runComplianceAudit(target)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (report) => {
          this.auditReport.set(report);
          this.auditLoading.set(false);
          this.loadDocuments();
        },
        error: (err) => {
          this.auditError.set(
            this.extractErrorMessage(err, '合規審查失敗，請確認後端服務與 Gemini 金鑰。'),
          );
          this.auditLoading.set(false);
        },
      });
  }

  onSelectCitation(citation: CitationRef): void {
    const doc = this.docsById().get(citation.doc_id);
    this.viewerDocId.set(citation.doc_id);
    this.viewerPage.set(citation.page);
    this.viewerTotalPages.set(doc?.page_count ?? Math.max(citation.page, 1));
    this.viewerDocLabel.set(doc ? `${doc.filename} (${doc.doc_type})` : citation.doc_id);
    this.activeCitation.set(citation);
  }

  previewDocument(doc: DocumentSummary): void {
    this.viewerDocId.set(doc.doc_id);
    this.viewerPage.set(1);
    this.viewerTotalPages.set(doc.page_count);
    this.viewerDocLabel.set(`${doc.filename} (${doc.doc_type})`);
    this.activeCitation.set(null);
  }

  toggleBaseline(doc: DocumentSummary): void {
    if (!doc.reg_no) return;
    this.docsError.set(null);
    this.api
      .toggleBaseline(doc.doc_id, !doc.is_active_baseline)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => this.loadDocuments(),
        error: (err) => this.docsError.set(this.extractErrorMessage(err, '切換基準文件失敗')),
      });
  }

  openUploadModal(): void {
    this.resetUploadForm();
    this.uploadSuccess.set(null);
    this.uploadRegNo.set(this.uploadDocType() === 'label' ? this.regNo() : '');
    this.uploadDialogRef()?.nativeElement.showModal();
  }

  closeUploadModal(): void {
    this.uploadSub?.unsubscribe();
    this.uploading.set(false);
    this.resetUploadForm();
    const dlg = this.uploadDialogRef()?.nativeElement;
    if (dlg) {
      if (dlg.open) {
        dlg.close();
      }
      dlg.returnValue = '';
    }
  }

  onDialogClose(): void {
    this.uploadSub?.unsubscribe();
    this.uploading.set(false);
    this.resetUploadForm();
    const dlg = this.uploadDialogRef()?.nativeElement;
    if (dlg) {
      dlg.returnValue = '';
    }
  }

  onFileSelected(event: Event): void {
    const inputEl = event.target as HTMLInputElement;
    this.uploadFile.set(inputEl.files?.[0] ?? null);
  }

  submitUpload(event: Event): void {
    event.preventDefault();
    if (this.uploading()) return;
    const file = this.uploadFile();
    if (!file) {
      this.uploadError.set('請先選擇 PDF 或標籤圖檔。');
      return;
    }
    this.uploading.set(true);
    this.uploadError.set(null);
    this.uploadSuccess.set(null);

    this.uploadSub?.unsubscribe();
    this.uploadSub = this.api
      .uploadDocument(file, this.uploadDocType(), {
        reg_no: this.uploadRegNo(),
        manual_notes: this.uploadNotes(),
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          this.uploading.set(false);
          this.uploadSuccess.set(`已上傳 ${res.doc_id}（模型：${res.model_used}，${res.page_count} 頁）`);
          this.loadDocuments();
          this.previewDocument(res);
          this.closeUploadModal();
        },
        error: (err) => {
          this.uploading.set(false);
          this.uploadError.set(this.extractErrorMessage(err, '文件上傳解析失敗'));
        },
      });
  }

  private resetUploadForm(): void {
    this.uploadFile.set(null);
    this.uploadNotes.set('');
    this.uploadError.set(null);
    const fileInput = this.uploadFileInputRef()?.nativeElement;
    if (fileInput) {
      fileInput.value = '';
    }
  }

  private extractErrorMessage(err: unknown, fallback: string): string {
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
}
