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
import { catchError, of, Subscription, switchMap } from 'rxjs';

import { BboxSvgViewerComponent } from './components/bbox-svg-viewer/bbox-svg-viewer.component';
import { CopilotAuditPanelComponent } from './components/copilot-audit-panel/copilot-audit-panel.component';
import {
  EntityAliasDialogComponent,
  EntityAliasDialogData,
} from './components/entity-alias-dialog/entity-alias-dialog.component';
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
  EntityAlias,
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
  private baselineSub?: Subscription;
  private moaSub?: Subscription;
  private auditSub?: Subscription;

  // Permit, MOA & Pulled Baselines state
  readonly regNo = signal('農藥進00569');
  readonly moaSnapshot = signal<RegistryLookupResponse | null>(null);
  readonly baselinesLoaded = signal(false);
  readonly baselineDocs = signal<DocumentSummary[]>([]);
  readonly baselinesLoading = signal(false);
  readonly baselinesError = signal<string | null>(null);

  // Audit Panel state (appears when audit runs)
  readonly auditReport = signal<ComplianceAuditReport | null>(null);
  readonly auditLoading = signal(false);
  readonly auditError = signal<string | null>(null);
  readonly entityAliases = signal<EntityAlias[]>([]);

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
    for (const d of this.baselineDocs()) {
      map.set(d.doc_id, d);
    }
    return map;
  });

  ngOnInit(): void {
    this.loadDocuments();
    this.loadEntityAliases();
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

  loadBaselines(): void {
    const target = this.regNo().trim();
    if (!target) return;

    this.baselinesLoading.set(true);
    this.baselinesError.set(null);
    this.baselinesLoaded.set(true);
    this.moaSnapshot.set(null);
    this.auditReport.set(null);
    this.auditError.set(null);

    this.moaSub?.unsubscribe();
    this.baselineSub?.unsubscribe();
    this.baselineSub = this.api
      .lookupRegistry(target)
      .pipe(
        catchError(() => of(null)),
        switchMap((snap) => {
          if (snap) {
            this.moaSnapshot.set(snap);
          }
          return this.api.listDocuments({
            reg_no: target,
            is_active_baseline: true,
            include_shared_certs: true,
            limit: 50,
          });
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (docs) => {
          this.baselineDocs.set(docs);
          this.baselinesLoading.set(false);
          this.api
            .getCachedAuditReport(target)
            .pipe(
              catchError(() => of(null)),
              takeUntilDestroyed(this.destroyRef),
            )
            .subscribe((cached) => {
              if (cached) {
                this.auditReport.set(cached);
              }
            });
        },
        error: (err) => {
          this.baselinesError.set(extractApiErrorMessage(err, '載入基準文件失敗'));
          this.baselinesLoading.set(false);
        },
      });
  }

  runAudit(forceRefresh = false): void {
    const target = this.regNo().trim();
    if (!target) return;
    this.auditSub?.unsubscribe();
    this.auditReport.set(null);
    this.auditLoading.set(true);
    this.auditError.set(null);
    this.auditSub = this.api
      .runComplianceAudit(target, forceRefresh)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (report) => {
          this.auditReport.set(report);
          this.auditLoading.set(false);
          this.loadDocuments();
          this.promptUnconfirmedBilingualEntity(report);
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
        next: () => {
          this.loadDocuments();
          if (this.baselinesLoaded()) {
            this.loadBaselines();
          }
        },
        error: (err) => this.docsError.set(extractApiErrorMessage(err, '切換基準文件失敗')),
      });
  }

  openUploadModal(): void {
    const ref = this.dialog.open<
      UploadDialogComponent,
      UploadDialogData,
      DocumentUploadResponse[] | null
    >(UploadDialogComponent, {
      data: { defaultRegNo: this.regNo() },
      width: 'auto',
      maxWidth: '94vw',
      panelClass: 'wide-upload-dialog',
    });

    ref
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((results) => {
        if (!results || results.length === 0) return;
        this.loadDocuments();
        if (this.baselinesLoaded()) {
          this.loadBaselines();
        }
        const latest = results[results.length - 1];
        this.previewDocument(latest);
        const msg =
          results.length === 1
            ? `已上傳 ${latest.doc_id}（模型：${latest.model_used}，${latest.page_count} 頁）`
            : `已批次上傳 ${results.length} 份文件`;
        this.snackBar.open(msg, '關閉', { duration: 6000 });
      });
  }

  private loadEntityAliases(): void {
    this.api
      .listEntityAliases()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (aliases) => this.entityAliases.set(aliases),
        error: () => {
          // Non-fatal on initial load
        },
      });
  }

  private promptUnconfirmedBilingualEntity(
    report: ComplianceAuditReport,
    startIndex = 0,
  ): void {
    const cjkRe = /[\u4e00-\u9fff]/;
    const latinRe = /[A-Za-z]/;
    const existing = this.entityAliases();

    for (let idx = startIndex; idx < report.checklist_items.length; idx++) {
      const item = report.checklist_items[idx];
      if (item.category !== 'entity_contact_update') continue;
      const a = item.current_label_value.trim();
      const b = item.recommended_new_value.trim();
      if (!a || !b) continue;

      let zhName = '';
      let enName = '';
      if (cjkRe.test(a) && latinRe.test(b) && !cjkRe.test(b)) {
        zhName = a;
        enName = b;
      } else if (cjkRe.test(b) && latinRe.test(a) && !cjkRe.test(a)) {
        zhName = b;
        enName = a;
      } else {
        continue;
      }

      const alreadyRecorded = existing.some(
        (al) =>
          al.zh_name.toLowerCase() === zhName.toLowerCase() &&
          al.en_name.toLowerCase() === enName.toLowerCase(),
      );
      if (alreadyRecorded) continue;

      const ref = this.dialog.open<EntityAliasDialogComponent, EntityAliasDialogData, boolean>(
        EntityAliasDialogComponent,
        {
          data: { zhName, enName },
          width: '480px',
          maxWidth: '94vw',
        },
      );
      ref
        .afterClosed()
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe((confirmed) => {
          if (confirmed === undefined) return;
          this.api
            .upsertEntityAlias(zhName, enName, confirmed)
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
              next: (saved) => {
                this.entityAliases.update((list) => [saved, ...list]);
                if (confirmed) {
                  this.runAudit();
                } else {
                  this.promptUnconfirmedBilingualEntity(report, idx + 1);
                }
              },
            });
        });
      break;
    }
  }
}
