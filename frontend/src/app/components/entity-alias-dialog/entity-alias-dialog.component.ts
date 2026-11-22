import { Component, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';

export interface EntityAliasDialogData {
  zhName: string;
  enName: string;
}

@Component({
  selector: 'app-entity-alias-dialog',
  imports: [MatDialogModule, MatButtonModule],
  template: `
    <h2 mat-dialog-title>確認中英文持有人名稱是否相同</h2>
    <mat-dialog-content class="alias-body">
      <p>系統偵測到以下兩組名稱語言不同，請問是否代表同一家公司？</p>
      <div class="alias-pair">
        <div><strong>中文名稱：</strong>{{ data.zhName }}</div>
        <div><strong>英文名稱：</strong><span lang="en">{{ data.enName }}</span></div>
      </div>
      <p class="alias-hint">確認後將儲存至資料庫，後續審查將自動視為一致並不再提示。</p>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button type="button" mat-button (click)="dialogRef.close(false)">否，不同公司</button>
      <button type="button" mat-flat-button (click)="dialogRef.close(true)">是，同一家公司</button>
    </mat-dialog-actions>
  `,
  styles: `
    .alias-body {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .alias-pair {
      padding: 0.75rem;
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }
    .alias-hint {
      margin: 0;
      font-size: 0.85rem;
      color: var(--text-muted);
    }
  `,
})
export class EntityAliasDialogComponent {
  readonly dialogRef = inject(MatDialogRef<EntityAliasDialogComponent, boolean>);
  readonly data = inject<EntityAliasDialogData>(MAT_DIALOG_DATA);
}
