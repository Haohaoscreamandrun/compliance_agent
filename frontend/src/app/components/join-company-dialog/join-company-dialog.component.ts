import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { AccessRequestOut } from '../../models/compliance.models';
import { ComplianceApiService, extractApiErrorMessage } from '../../services/compliance-api.service';

export interface JoinCompanyDialogData {
  email: string;
}

@Component({
  selector: 'app-join-company-dialog',
  imports: [
    FormsModule,
    MatButtonModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
  ],
  template: `
    <h2 mat-dialog-title>申請加入公司 <small lang="en">Join Company</small></h2>
    <mat-dialog-content class="join-content">
      <p class="hint">
        登入帳號：<strong class="mono">{{ data.email }}</strong>
      </p>
      <p class="hint">提交申請後，管理員可將您的帳號指派至對應公司工作區。</p>
      <mat-form-field appearance="outline" subscriptSizing="dynamic" class="full-width">
        <mat-label>公司名稱 (Company Name)</mat-label>
        <input
          matInput
          type="text"
          [ngModel]="companyName()"
          (ngModelChange)="companyName.set($event)"
          placeholder="e.g. 台灣示範農化股份有限公司"
          maxlength="128"
        />
      </mat-form-field>
      <mat-form-field appearance="outline" subscriptSizing="dynamic" class="full-width">
        <mat-label>備註 (選填 / Note)</mat-label>
        <textarea
          matInput
          rows="2"
          [ngModel]="note()"
          (ngModelChange)="note.set($event)"
          placeholder="部門、聯絡資訊或測試用途"
          maxlength="1000"
        ></textarea>
      </mat-form-field>
      @if (errorMsg(); as err) {
        <p class="error-text" role="alert">{{ err }}</p>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" [disabled]="submitting()" (click)="dialogRef.close()">
        取消
      </button>
      <button
        mat-flat-button
        type="button"
        [disabled]="submitting() || !companyName().trim()"
        (click)="submit()"
      >
        {{ submitting() ? '送出中…' : '送出申請' }}
      </button>
    </mat-dialog-actions>
  `,
  styles: `
    .join-content {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      padding-top: 0.25rem;
    }
    .hint {
      margin: 0;
      font-size: 0.875rem;
      color: var(--text-muted);
    }
    .mono {
      font-family: var(--font-mono);
      color: var(--text-primary);
    }
    .full-width {
      inline-size: 100%;
    }
    .error-text {
      margin: 0;
      font-size: 0.85rem;
      color: var(--status-danger-fg);
    }
  `,
})
export class JoinCompanyDialogComponent {
  readonly data = inject<JoinCompanyDialogData>(MAT_DIALOG_DATA);
  readonly dialogRef = inject(MatDialogRef<JoinCompanyDialogComponent, AccessRequestOut | undefined>);
  private readonly api = inject(ComplianceApiService);

  readonly companyName = signal('');
  readonly note = signal('');
  readonly submitting = signal(false);
  readonly errorMsg = signal<string | null>(null);

  submit(): void {
    const name = this.companyName().trim();
    if (!name || this.submitting()) return;
    this.submitting.set(true);
    this.errorMsg.set(null);
    this.api.submitAccessRequest(name, this.note()).subscribe({
      next: (saved) => {
        this.submitting.set(false);
        this.dialogRef.close(saved);
      },
      error: (err) => {
        this.submitting.set(false);
        this.errorMsg.set(extractApiErrorMessage(err, '送出加入公司申請失敗'));
      },
    });
  }
}
