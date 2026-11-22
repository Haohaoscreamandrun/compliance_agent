import { Component, DestroyRef, ElementRef, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSelectModule } from '@angular/material/select';
import { Subscription } from 'rxjs';

import { DocType, DocumentUploadResponse } from '../../models/compliance.models';
import { ComplianceApiService, extractApiErrorMessage } from '../../services/compliance-api.service';

export interface UploadDialogData {
  defaultRegNo: string;
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
  private readonly dialogRef = inject(MatDialogRef<UploadDialogComponent, DocumentUploadResponse | null>);
  readonly data = inject<UploadDialogData>(MAT_DIALOG_DATA);

  private readonly fileInputRef = viewChild<ElementRef<HTMLInputElement>>('fileInput');
  private uploadSub?: Subscription;

  readonly docType = signal<DocType>('label');
  readonly regNo = signal(this.data.defaultRegNo);
  readonly manualNotes = signal('');
  readonly selectedFile = signal<File | null>(null);
  readonly uploading = signal(false);
  readonly errorMessage = signal<string | null>(null);

  onDocTypeChange(nextType: DocType): void {
    this.docType.set(nextType);
    // Auto-fill reg_no only for 'label'; leave optional/blank for multi-product LoA/MoC/MSDS
    this.regNo.set(nextType === 'label' ? this.data.defaultRegNo : '');
  }

  onFileSelected(event: Event): void {
    const inputEl = event.target as HTMLInputElement;
    this.selectedFile.set(inputEl.files?.[0] ?? null);
    this.errorMessage.set(null);
  }

  clearFile(): void {
    this.selectedFile.set(null);
    const inputEl = this.fileInputRef()?.nativeElement;
    if (inputEl) {
      inputEl.value = '';
    }
  }

  cancel(): void {
    this.uploadSub?.unsubscribe();
    this.uploading.set(false);
    this.dialogRef.disableClose = false;
    this.dialogRef.close(null);
  }

  submit(event: Event): void {
    event.preventDefault();
    if (this.uploading()) return;

    const file = this.selectedFile();
    if (!file) {
      this.errorMessage.set('請先選擇 PDF 或標籤圖檔。');
      return;
    }

    this.uploading.set(true);
    this.dialogRef.disableClose = true;
    this.errorMessage.set(null);

    this.uploadSub?.unsubscribe();
    this.uploadSub = this.api
      .uploadDocument(file, this.docType(), {
        reg_no: this.regNo(),
        manual_notes: this.manualNotes(),
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          this.uploading.set(false);
          this.dialogRef.disableClose = false;
          this.dialogRef.close(res);
        },
        error: (err) => {
          this.uploading.set(false);
          this.dialogRef.disableClose = false;
          this.errorMessage.set(extractApiErrorMessage(err, '文件上傳解析失敗'));
        },
      });
  }
}
