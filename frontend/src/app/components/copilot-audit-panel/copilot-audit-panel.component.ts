import { Component, input, output } from '@angular/core';

import {
  ActionStatus,
  CitationRef,
  ComplianceAuditReport,
  OverallComplianceStatus,
} from '../../models/compliance.models';

@Component({
  selector: 'app-copilot-audit-panel',
  standalone: true,
  templateUrl: './copilot-audit-panel.component.html',
  styleUrl: './copilot-audit-panel.component.scss',
})
export class CopilotAuditPanelComponent {
  readonly report = input<ComplianceAuditReport | null>(null);
  readonly loading = input<boolean>(false);
  readonly errorMessage = input<string | null>(null);

  readonly selectCitation = output<CitationRef>();

  statusLabel(status: ActionStatus): string {
    switch (status) {
      case 'must_update':
        return '必須更新';
      case 'pending_moa_approval':
        return '待農業部核准';
      case 'verify_warning':
        return '待人工確認';
    }
  }

  overallLabel(status: OverallComplianceStatus): string {
    switch (status) {
      case 'compliant':
        return '完全合規 (Compliant)';
      case 'action_required':
        return '需更新標示 (Action Required)';
      case 'pending_moa_approval':
        return '待農業部變更核准 (Pending MOA Approval)';
      case 'needs_verification':
        return '需人工覆核 (Needs Verification)';
    }
  }
}
