import { ComponentFixture, TestBed } from '@angular/core/testing';

import { CitationRef, ComplianceAuditReport } from '../../models/compliance.models';
import { CopilotAuditPanelComponent } from './copilot-audit-panel.component';

describe('CopilotAuditPanelComponent', () => {
  let component: CopilotAuditPanelComponent;
  let fixture: ComponentFixture<CopilotAuditPanelComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CopilotAuditPanelComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(CopilotAuditPanelComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('renders progress when loading and bilingual summaries + citation buttons for report', () => {
    fixture.componentRef.setInput('loading', true);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('mat-progress-bar')).toBeTruthy();

    const citation: CitationRef = {
      doc_id: 'loa_1',
      page: 2,
      box_2d: [10, 20, 30, 40],
      verbatim_quote: 'BASF',
      extra_boxes_2d: [],
    };
    const report: ComplianceAuditReport = {
      reg_no: '農藥進00569',
      product_name_zh: '可濕性硫黃',
      product_name_en: 'SULFUR',
      overall_status: 'pending_moa_approval',
      executive_summary_zh: '中文摘要',
      executive_summary_en: 'English summary',
      checklist_items: [
        {
          field_name: '製造廠名稱',
          category: 'factory_source_change',
          action_status: 'pending_moa_approval',
          current_label_value: 'Old Plant',
          recommended_new_value: 'New Plant',
          rationale: 'Factory changed.',
          current_label_citation: null,
          supporting_citations: [citation],
        },
      ],
      moa_snapshot_stale: true,
      msds_doc_id_used: null,
      audit_warnings: ['No MSDS uploaded'],
    };

    fixture.componentRef.setInput('loading', false);
    fixture.componentRef.setInput('report', report);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[lang="zh-Hant"]')?.textContent).toContain('中文摘要');
    expect(fixture.nativeElement.querySelector('p.summary-en[lang="en"]')?.textContent).toContain(
      'English summary',
    );

    const emitted: CitationRef[] = [];
    component.selectCitation.subscribe((c) => emitted.push(c));
    const btn: HTMLButtonElement | null = fixture.nativeElement.querySelector('button.cite-support');
    expect(btn?.getAttribute('aria-label')).toContain('製造廠名稱');
    btn?.click();
    expect(emitted).toEqual([citation]);
  });
});
