import {
  afterNextRender,
  Component,
  computed,
  DestroyRef,
  ElementRef,
  inject,
  OnInit,
  signal,
  viewChild,
} from '@angular/core';
import { background, BackgroundHandle, contour, fluid } from 'asciify-engine/kit';
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
import { catchError, forkJoin, of, Subscription, switchMap } from 'rxjs';

import { BboxSvgViewerComponent } from './components/bbox-svg-viewer/bbox-svg-viewer.component';
import { CopilotAuditPanelComponent } from './components/copilot-audit-panel/copilot-audit-panel.component';
import {
  EntityAliasDialogComponent,
  EntityAliasDialogData,
} from './components/entity-alias-dialog/entity-alias-dialog.component';
import {
  JoinCompanyDialogComponent,
  JoinCompanyDialogData,
} from './components/join-company-dialog/join-company-dialog.component';
import {
  UploadDialogComponent,
  UploadDialogData,
} from './components/upload-dialog/upload-dialog.component';
import {
  AccessRequestOut,
  AphiaLookupResponse,
  CitationRef,
  ComplianceAuditReport,
  DocType,
  DocumentSummary,
  DocumentUploadResponse,
  EntityAlias,
  MeResponse,
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
  private contourBg?: BackgroundHandle;

  readonly contourHost = viewChild<ElementRef<HTMLElement>>('contourHost');

  constructor() {
    afterNextRender(() => {
      this.mountBackdrop(this.theme());
    });

    this.destroyRef.onDestroy(() => {
      this.contourBg?.destroy();
      this.contourBg = undefined;
    });
  }

  // Theme & Identity state
  readonly theme = signal<'light' | 'dark'>(this.readInitialTheme());
  readonly me = signal<MeResponse | null>(null);
  readonly activeWorkspace = computed(() => this.api.workspaceCompanyId());
  readonly isPublicDemoMode = computed(() => this.api.workspaceCompanyId() === 'public_demo');
  readonly canManagePublicDemo = computed(() => !!this.me()?.can_manage_public_demo);

  // Permit, MOA & Pulled Baselines state
  readonly regNo = signal('');
  readonly moaSnapshot = signal<RegistryLookupResponse | null>(null);
  readonly aphiaSnapshot = signal<AphiaLookupResponse | null>(null);
  readonly aphiaRefreshing = signal(false);
  readonly evalDateInput = signal('');
  readonly evalSaving = signal(false);
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
  readonly compareCitations = signal<CitationRef[]>([]);

  readonly docLabelsById = computed<Record<string, string>>(() => {
    const labels: Record<string, string> = {};
    for (const [id, d] of this.docsById()) {
      labels[id] = `${d.filename} (${d.doc_type})`;
    }
    return labels;
  });

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
    this.loadMe();
  }

  loadMe(): void {
    this.api
      .getMe()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (info) => {
          this.me.set(info);
          if (info.company_id) {
            this.loadDocuments();
            this.loadEntityAliases();
          }
        },
        error: () => {
          this.me.set(null);
        },
      });
  }

  togglePublicDemoWorkspace(): void {
    if (!this.canManagePublicDemo()) return;
    const next = this.isPublicDemoMode() ? 'default' : 'public_demo';
    this.api.setWorkspaceCompanyId(next);
    this.baselinesLoaded.set(false);
    this.baselineDocs.set([]);
    this.moaSnapshot.set(null);
    this.aphiaSnapshot.set(null);
    this.auditReport.set(null);
    this.auditError.set(null);
    this.viewerDocId.set(null);
    this.activeCitation.set(null);
    this.compareCitations.set([]);
    this.regNo.set(next === 'public_demo' ? '農藥進99001' : '');
    this.loadMe();
  }

  tryDemoAudit(): void {
    const demoReg = '農藥進99001';
    this.regNo.set(demoReg);
    this.auditLoading.set(true);
    this.auditError.set(null);
    this.baselinesLoaded.set(false);
    this.api
      .lookupRegistry(demoReg)
      .pipe(
        catchError(() => of(null)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((moa) => {
        if (moa) this.moaSnapshot.set(moa);
      });
    this.api
      .getCachedAuditReport(demoReg)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (report) => {
          this.auditReport.set(report);
          this.auditLoading.set(false);
        },
        error: (err) => {
          this.auditError.set(extractApiErrorMessage(err, '載入公開示範審查失敗'));
          this.auditLoading.set(false);
        },
      });
  }

  openJoinCompanyDialog(): void {
    const email = this.me()?.email;
    if (!email) return;
    const ref = this.dialog.open<JoinCompanyDialogComponent, JoinCompanyDialogData, AccessRequestOut | undefined>(
      JoinCompanyDialogComponent,
      {
        data: { email },
        width: '460px',
        maxWidth: '94vw',
      },
    );
    ref
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((created) => {
        if (!created) return;
        this.snackBar.open(`已送出加入「${created.requested_company_name}」申請`, '關閉', {
          duration: 4500,
        });
        this.loadMe();
      });
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
    this.aphiaSnapshot.set(null);
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
          return forkJoin({
            docs: this.api.listDocuments({
              reg_no: target,
              is_active_baseline: true,
              include_shared_certs: true,
              limit: 50,
            }),
            aphia: snap
              ? this.api.lookupAphia(target).pipe(catchError(() => of(null)))
              : of(null),
          });
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: ({ docs, aphia }) => {
          this.baselineDocs.set(docs);
          this.aphiaSnapshot.set(aphia);
          this.evalDateInput.set(aphia?.evaluation_submitted_date ?? '');
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

  onSelectCitations(citations: CitationRef[]): void {
    const byKey = new Map<string, CitationRef>();
    for (const c of citations) {
      const key = `${c.doc_id}:${c.page}`;
      const existing = byKey.get(key);
      if (existing) {
        byKey.set(key, {
          ...existing,
          extra_boxes_2d: [
            ...(existing.extra_boxes_2d ?? []),
            c.box_2d,
            ...(c.extra_boxes_2d ?? []),
          ],
          verbatim_quote: existing.verbatim_quote.includes(c.verbatim_quote)
            ? existing.verbatim_quote
            : `${existing.verbatim_quote} / ${c.verbatim_quote}`,
        });
      } else {
        byKey.set(key, c);
      }
    }
    this.compareCitations.set(Array.from(byKey.values()));
  }

  onOpenFullCitation(citation: CitationRef): void {
    const doc = this.docsById().get(citation.doc_id);
    this.compareCitations.set([]);
    this.viewerDocId.set(citation.doc_id);
    this.viewerPage.set(citation.page);
    this.viewerTotalPages.set(doc?.page_count ?? Math.max(citation.page, 1));
    this.viewerDocLabel.set(doc ? `${doc.filename} (${doc.doc_type})` : citation.doc_id);
    this.activeCitation.set(citation);
  }

  previewDocument(doc: DocumentSummary): void {
    this.compareCitations.set([]);
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
      width: 'min(1160px, 94vw)',
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

  refreshAphia(): void {
    const target = this.regNo().trim();
    if (!target || this.aphiaRefreshing()) return;
    this.aphiaRefreshing.set(true);
    this.api
      .lookupAphia(target, true)
      .pipe(
        catchError(() => of(null)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((aphia) => {
        if (aphia) {
          this.aphiaSnapshot.set(aphia);
          this.evalDateInput.set(aphia.evaluation_submitted_date ?? '');
        }
        this.aphiaRefreshing.set(false);
      });
  }

  saveEvaluationDate(): void {
    const target = this.regNo().trim();
    const dateVal = this.evalDateInput().trim();
    if (!target || !dateVal || this.evalSaving()) return;
    this.evalSaving.set(true);
    this.api
      .saveAphiaEvaluation(target, dateVal)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (saved) => {
          this.aphiaSnapshot.update((cur) =>
            cur ? { ...cur, evaluation_submitted_date: saved.submitted_date } : cur,
          );
          this.evalSaving.set(false);
          this.snackBar.open(`已儲存 ${saved.common_name} 評估送件日：${saved.submitted_date}`, '關閉', {
            duration: 4000,
          });
        },
        error: (err) => {
          this.evalSaving.set(false);
          this.snackBar.open(extractApiErrorMessage(err, '儲存評估送件日失敗'), '關閉', {
            duration: 5000,
          });
        },
      });
  }

  toggleTheme(): void {
    const next = this.theme() === 'light' ? 'dark' : 'light';
    this.theme.set(next);
    this.applyTheme(next);
    this.mountBackdrop(next);
  }

  private mountBackdrop(mode: 'light' | 'dark'): void {
    this.contourBg?.destroy();
    this.contourBg = undefined;
    const host = this.contourHost()?.nativeElement;
    if (!host) return;
    if (mode === 'light') {
      this.contourBg = background(host, {
        preset: fluid,
        palette: '#ff7a59',
        speed: 1,
        cellSize: 7,
        opacity: 0.7,
        lazy: false,
      });
      return;
    }
    this.contourBg = background(host, {
      preset: contour,
      palette: '#5ec2d8',
      accent: '#1b6c80',
      cellSize: 15,
      charset: ' ⠁⠃⠇⡇⣇⣧⣷⣿',
      cursor: 'fluid',
      opacity: 0.6,
      lazy: false,
    });
  }

  private readInitialTheme(): 'light' | 'dark' {
    try {
      const saved = globalThis.localStorage?.getItem('compliance_theme');
      const initial = saved === 'dark' ? 'dark' : 'light';
      this.applyTheme(initial);
      return initial;
    } catch {
      return 'light';
    }
  }

  private applyTheme(mode: 'light' | 'dark'): void {
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', mode);
    }
    try {
      globalThis.localStorage?.setItem('compliance_theme', mode);
    } catch {
      // Ignore storage errors in restricted contexts.
    }
  }
}
