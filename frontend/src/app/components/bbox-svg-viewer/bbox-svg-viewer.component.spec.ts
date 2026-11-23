import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { BboxSvgViewerComponent } from './bbox-svg-viewer.component';

describe('BboxSvgViewerComponent', () => {
  let component: BboxSvgViewerComponent;
  let fixture: ComponentFixture<BboxSvgViewerComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BboxSvgViewerComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();

    fixture = TestBed.createComponent(BboxSvgViewerComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('renders box_2d and extra_boxes_2d only when docId and pageNumber match citation', () => {
    fixture.componentRef.setInput('docId', 'doc_a');
    fixture.componentRef.setInput('pageNumber', 2);
    fixture.componentRef.setInput('totalPages', 3);
    fixture.componentRef.setInput('citation', {
      doc_id: 'doc_a',
      page: 2,
      box_2d: [100, 200, 300, 450],
      verbatim_quote: 'BASF Taiwan Ltd.',
      extra_boxes_2d: [[320, 200, 360, 500]],
    });
    fixture.detectChanges();

    const rects = fixture.nativeElement.querySelectorAll('svg.bbox-overlay rect.bbox-rect');
    expect(rects.length).toBe(2);
    expect(rects[0].getAttribute('x')).toBe('200');
    expect(rects[0].getAttribute('y')).toBe('100');
    expect(rects[0].getAttribute('width')).toBe('250');
    expect(rects[0].getAttribute('height')).toBe('200');
    expect(rects[1].getAttribute('y')).toBe('320');

    // Switching page hides SVG boxes for page 2
    fixture.componentRef.setInput('pageNumber', 1);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('rect.bbox-rect').length).toBe(0);
  });

  it('emits pageChange on nextPage/prevPage and shows error fallback on image error', () => {
    const emitted: number[] = [];
    component.pageChange.subscribe((p) => emitted.push(p));

    fixture.componentRef.setInput('docId', 'doc_a');
    fixture.componentRef.setInput('pageNumber', 1);
    fixture.componentRef.setInput('totalPages', 3);
    fixture.detectChanges();

    component.nextPage();
    expect(emitted).toEqual([2]);

    component.onImageError();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[role="alert"]')?.textContent).toContain('無法載入頁面影像');
  });

  it('supports zoom controls and magnifier loupe toggle', () => {
    expect(component.zoomPercent()).toBe(100);
    component.zoomIn();
    expect(component.zoomPercent()).toBe(125);
    component.zoomOut();
    expect(component.zoomPercent()).toBe(100);
    component.zoomIn();
    component.resetZoom();
    expect(component.zoomPercent()).toBe(100);

    expect(component.magnifierActive()).toBeFalse();
    component.toggleMagnifier();
    expect(component.magnifierActive()).toBeTrue();
    component.toggleMagnifier();
    expect(component.magnifierActive()).toBeFalse();
  });

  it('renders cropped focus zones side-by-side when compareCitations is set', () => {
    fixture.componentRef.setInput('docLabelsById', {
      doc_label: 'label.pdf (label)',
      doc_loa: 'loa.pdf (change_cert)',
    });
    fixture.componentRef.setInput('compareCitations', [
      {
        doc_id: 'doc_label',
        page: 1,
        box_2d: [200, 100, 300, 500],
        verbatim_quote: 'Old Factory',
        extra_boxes_2d: [],
      },
      {
        doc_id: 'doc_loa',
        page: 2,
        box_2d: [400, 200, 500, 700],
        verbatim_quote: 'New Factory',
        extra_boxes_2d: [],
      },
    ]);
    fixture.detectChanges();

    const cards = fixture.nativeElement.querySelectorAll('.focus-zone-card');
    expect(cards.length).toBe(2);
    expect(cards[0].textContent).toContain('label.pdf (label) · P1');
    expect(cards[1].textContent).toContain('loa.pdf (change_cert) · P2');
    const svgs = fixture.nativeElement.querySelectorAll('svg.focus-crop-svg');
    expect(svgs.length).toBe(2);
    expect(svgs[0].getAttribute('viewBox')).not.toBe('0 0 1132 1600');
  });
});
