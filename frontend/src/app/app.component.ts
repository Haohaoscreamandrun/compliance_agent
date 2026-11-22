import { Component, computed, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatChipsModule } from '@angular/material/chips';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSelectModule } from '@angular/material/select';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatToolbarModule } from '@angular/material/toolbar';
import { Subscription } from 'rxjs';

import { BboxSvgViewerComponent } from './components/bbox-svg-viewer/bbox-svg-viewer.component';
import { CopilotAuditPanelComponent } from './components/copilot-audit-panel/copilot-audit-panel.component';
import {
  UploadDialogComponent,
  UploadDialogData,
} from './components/upload-dialog/upload-dialog.component';
import {
  CitationRef,
  ComplianceAuditReport,
  DocType,
  DocumentSummary,
  DocumentUploadResponse,
  RegistryLookupResponse,
} from './models/compliance.models';
import { ComplianceApiService, extractApiErrorMessage } from './services/compliance-api.service';

@Component({
  selector: 'app-root',
  imports: [
    FormsModule,
    MatToolbarModule,
    MatButtonModule,
    MatCardModule,
    MatCheckboxModule,
    MatChipsModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressBarModule,
    MatSelectModule,
    MatSidenavModule,
    MatSnackBarModule,
    BboxSvgViewerComponent,
    CopilotAuditPanelComponent,
  ],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent implements OnInit {
  private readonly api = inject(ComplianceApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);

  private docsSub?: Subscription;
  private moaSub?: Subscription;
  private auditSub?: Subscription;

  // Audit & Registry state
  readonly regNo = signal('農藥進00569');
  readonly moaSnapshot = signal<RegistryLookupResponse | null>(null);
  readonly auditReport = signal<ComplianceAuditReport | null>(null);
  readonly auditLoading = signal(false);
  readonly auditError = signal<string | null>(null);

  // Document Library & Sidenav state (default closed)
  readonly sidenavOpen = signal(false);
  readonly documents = signal<DocumentSummary[]>([]);
  readonly searchDocType = signal<DocType | ''>('');
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
        doc_type: this.searchDocType(),
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
          this.docsError.set(extractApiErrorMessage(err, '載入文件清單失敗'));
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
        error: (err) => this.auditError.set(extractApiErrorMessage(err, 'MOA 查詢失敗')),
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
            extractApiErrorMessage(err, '合規審查失敗，請確認後端服務與 Gemini 金鑰。'),
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
        error: (err) => this.docsError.set(extractApiErrorMessage(err, '切換基準文件失敗')),
      });
  }

  openUploadModal(): void {
    const ref = this.dialog.open<UploadDialogComponent, UploadDialogData, DocumentUploadResponse | null>(
      UploadDialogComponent,
      {
        data: { defaultRegNo: this.regNo() },
        width: '540px',
        maxWidth: '94vw',
      },
    );

    ref
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((res) => {
        if (!res) return;
        this.loadDocuments();
        this.previewDocument(res);
        this.snackBar.open(
          `已上傳 ${res.doc_id}（模型：${res.model_used}，${res.page_count} 頁）`,
          '關閉',
          { duration: 6000 },
        );
      });
  }
}
