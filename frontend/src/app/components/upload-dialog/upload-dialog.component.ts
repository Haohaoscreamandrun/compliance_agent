import { Component, computed, DestroyRef, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import {
  MAT_DIALOG_DATA,
  MatDialog,
  MatDialogModule,
  MatDialogRef,
} from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSelectModule } from '@angular/material/select';
import { firstValueFrom } from 'rxjs';

import { DocType, DocumentUploadResponse } from '../../models/compliance.models';
import { ComplianceApiService, extractApiErrorMessage } from '../../services/compliance-api.service';

export interface UploadDialogData {
  defaultRegNo: string;
}

export interface UploadFormEntry {
  id: number;
  docType: DocType;
  regNo: string;
  volumeTag: string;
  manualNotes: string;
  selectedFile: File | null;
  status: 'idle' | 'uploading' | 'done' | 'skipped' | 'error';
  statusMessage?: string;
}

@Component({
  selector: 'app-reparse-confirm-dialog',
  imports: [MatDialogModule, MatButtonModule],
  template: `
    <h2 mat-dialog-title>檔案已存在資料庫中</h2>
    <mat-dialog-content>
      <p>檔案「{{ data.filename }}」已存在於資料庫中。是否要重新以 Gemini 解析並設為基準文件？</p>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button type="button" mat-button (click)="dialogRef.close(false)">否，取消此筆上傳</button>
      <button type="button" mat-flat-button (click)="dialogRef.close(true)">是，重新解析並設為基準</button>
    </mat-dialog-actions>
  `,
})
export class ReparseConfirmDialogComponent {
  readonly dialogRef = inject(MatDialogRef<ReparseConfirmDialogComponent, boolean>);
  readonly data = inject<{ filename: string }>(MAT_DIALOG_DATA);
}

@Component({
  selector: 'app-upload-dialog',
  imports: [
    FormsModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatButtonModule,
    MatProgressBarModule,
  ],
  templateUrl: './upload-dialog.component.html',
  styleUrl: './upload-dialog.component.scss',
})
export class UploadDialogComponent {
  private readonly api = inject(ComplianceApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly dialog = inject(MatDialog);
  private readonly dialogRef = inject(
    MatDialogRef<UploadDialogComponent, DocumentUploadResponse[] | null>,
  );
  readonly data = inject<UploadDialogData>(MAT_DIALOG_DATA);

  private nextId = 1;
  private cancelled = false;

  readonly entries = signal<UploadFormEntry[]>([this.createEntry()]);
  readonly selectedFileCount = computed(
    () => this.entries().filter((e) => e.selectedFile !== null).length,
  );
  readonly uploading = signal(false);
  readonly errorMessage = signal<string | null>(null);

  constructor() {
    this.destroyRef.onDestroy(() => {
      this.cancelled = true;
    });
  }

  private createEntry(): UploadFormEntry {
    return {
      id: this.nextId++,
      docType: 'label',
      regNo: this.data.defaultRegNo,
      volumeTag: '',
      manualNotes: '',
      selectedFile: null,
      status: 'idle',
    };
  }

  addEntry(): void {
    this.entries.update((list) => [...list, this.createEntry()]);
  }

  removeEntry(id: number): void {
    this.entries.update((list) => (list.length > 1 ? list.filter((e) => e.id !== id) : list));
  }

  onDocTypeChange(id: number, nextType: DocType): void {
    this.entries.update((list) =>
      list.map((e) =>
        e.id === id
          ? {
              ...e,
              docType: nextType,
              regNo: nextType === 'label' ? this.data.defaultRegNo : '',
            }
          : e,
      ),
    );
  }

  onFieldChange<K extends keyof UploadFormEntry>(id: number, key: K, value: UploadFormEntry[K]): void {
    this.entries.update((list) => list.map((e) => (e.id === id ? { ...e, [key]: value } : e)));
  }

  onFileSelected(id: number, event: Event): void {
    const inputEl = event.target as HTMLInputElement;
    const file = inputEl.files?.[0] ?? null;
    this.entries.update((list) =>
      list.map((e) => (e.id === id ? { ...e, selectedFile: file, status: 'idle', statusMessage: undefined } : e)),
    );
    this.errorMessage.set(null);
  }

  clearFile(id: number, inputEl: HTMLInputElement): void {
    inputEl.value = '';
    this.onFieldChange(id, 'selectedFile', null);
  }

  cancel(): void {
    this.cancelled = true;
    this.uploading.set(false);
    this.dialogRef.disableClose = false;
    this.dialogRef.close(null);
  }

  async submit(event: Event): Promise<void> {
    event.preventDefault();
    if (this.uploading()) return;

    const current = this.entries();
    const missing = current.find((e) => e.status !== 'done' && !e.selectedFile);
    if (missing) {
      this.errorMessage.set('請為每一筆上傳項目選擇 PDF 或標籤圖檔。');
      return;
    }

    this.uploading.set(true);
    this.dialogRef.disableClose = true;
    this.errorMessage.set(null);

    const results: DocumentUploadResponse[] = [];
    let hasError = false;

    for (const entry of current) {
      if (this.cancelled) return;
      if (entry.status === 'done' || !entry.selectedFile) continue;

      this.patchEntry(entry.id, { status: 'uploading', statusMessage: '檢查與解析中…' });

      let forceReparse = false;
      try {
        const existsRes = await firstValueFrom(
          this.api.checkDocumentExists(entry.selectedFile.name, entry.docType, entry.regNo),
        );
        if (this.cancelled) return;
        if (existsRes.exists) {
          const confirmRef = this.dialog.open<
            ReparseConfirmDialogComponent,
            { filename: string },
            boolean
          >(ReparseConfirmDialogComponent, {
            data: { filename: entry.selectedFile.name },
            width: '440px',
            maxWidth: '92vw',
          });
          const shouldReparse = await firstValueFrom(confirmRef.afterClosed());
          if (this.cancelled) return;
          if (!shouldReparse) {
            this.patchEntry(entry.id, { status: 'skipped', statusMessage: '已略過（取消覆蓋）' });
            continue;
          }
          forceReparse = true;
        }
      } catch {
        // Proceed with direct upload if pre-check fails
      }

      try {
        const res = await firstValueFrom(
          this.api.uploadDocument(entry.selectedFile, entry.docType, {
            reg_no: entry.regNo,
            volume_tag: entry.docType === 'label' ? entry.volumeTag : '',
            manual_notes: entry.manualNotes,
            force_reparse: forceReparse,
          }),
        );
        if (this.cancelled) return;
        results.push(res);
        this.patchEntry(entry.id, {
          status: 'done',
          statusMessage: `完成 (${res.doc_id}, ${res.model_used})`,
        });
      } catch (err) {
        hasError = true;
        this.patchEntry(entry.id, {
          status: 'error',
          statusMessage: extractApiErrorMessage(err, '文件上傳解析失敗'),
        });
      }
    }

    this.uploading.set(false);
    this.dialogRef.disableClose = false;
    if (!hasError) {
      this.dialogRef.close(results.length > 0 ? results : null);
    } else {
      this.errorMessage.set('部分文件上傳失敗，請檢查上方錯誤訊息後重試。');
    }
  }

  private patchEntry(id: number, patch: Partial<UploadFormEntry>): void {
    this.entries.update((list) => list.map((e) => (e.id === id ? { ...e, ...patch } : e)));
  }
}
