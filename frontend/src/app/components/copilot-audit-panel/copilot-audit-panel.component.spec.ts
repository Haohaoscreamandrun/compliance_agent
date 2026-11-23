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
        {
          field_name: '使用範圍：茶 / 炭疽病',
          category: 'crop_usage_sync',
          action_status: 'verify_warning',
          current_label_value: '未登載',
          recommended_new_value: '稀釋 1000 倍',
          rationale: 'Allowed by MOA but omitted on label.',
          current_label_citation: null,
          supporting_citations: [],
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
    expect(component.basicChecklistItems().length).toBe(1);
    expect(component.cropUsageItems().length).toBe(1);
    expect(fixture.nativeElement.textContent).toContain('基本資料比對 (1)');
    expect(fixture.nativeElement.textContent).toContain('未登載使用範圍 (1)');

    const emitted: CitationRef[][] = [];
    component.selectCitations.subscribe((c) => emitted.push(c));
    const btn: HTMLButtonElement | null = fixture.nativeElement.querySelector('button.cite-diff');
    expect(btn?.textContent?.trim()).toBe('Diff');
    expect(btn?.getAttribute('aria-label')).toContain('製造廠名稱');
    btn?.click();
    expect(emitted).toEqual([[citation]]);
  });
});
