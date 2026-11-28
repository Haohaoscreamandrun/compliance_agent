import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatProgressBarModule } from '@angular/material/progress-bar';

import { BillingSummaryResponse } from '../../models/compliance.models';
import { ComplianceApiService, extractApiErrorMessage } from '../../services/compliance-api.service';

export interface BillingDialogData {
  summary: BillingSummaryResponse | null;
}

@Component({
  selector: 'app-billing-dialog',
  imports: [
    CommonModule,
    MatButtonModule,
    MatCardModule,
    MatChipsModule,
    MatDialogModule,
    MatProgressBarModule,
  ],
  templateUrl: './billing-dialog.component.html',
  styleUrl: './billing-dialog.component.scss',
})
export class BillingDialogComponent {
  private readonly api = inject(ComplianceApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly dialogRef = inject(MatDialogRef<BillingDialogComponent>);
  private readonly data = inject<BillingDialogData>(MAT_DIALOG_DATA, { optional: true });

  readonly summary = signal<BillingSummaryResponse | null>(this.data?.summary ?? null);
  readonly checkoutBusy = signal<string | null>(null);
  readonly checkoutError = signal<string | null>(null);

  constructor() {
    this.api
      .getBillingSummary()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (fresh) => this.summary.set(fresh),
        error: () => {
          // Keep initial summary snapshot if refresh fails
        },
      });
  }

  close(): void {
    this.dialogRef.close();
  }

  startTopup(packId: string): void {
    this.checkoutBusy.set(packId);
    this.checkoutError.set(null);
    this.api
      .createBillingCheckout(packId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          this.checkoutBusy.set(null);
          if (typeof document === 'undefined') return;
          const form = document.createElement('form');
          form.method = 'POST';
          form.action = res.mpg_url;
          for (const [k, v] of Object.entries(res.form_fields)) {
            const inputEl = document.createElement('input');
            inputEl.type = 'hidden';
            inputEl.name = k;
            inputEl.value = v;
            form.appendChild(inputEl);
          }
          document.body.appendChild(form);
          form.submit();
        },
        error: (err) => {
          this.checkoutBusy.set(null);
          this.checkoutError.set(extractApiErrorMessage(err, '建立藍新金流結帳失敗，請稍後再試。'));
        },
      });
  }
}
