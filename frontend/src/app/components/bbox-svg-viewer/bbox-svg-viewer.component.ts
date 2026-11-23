import { Component, computed, effect, ElementRef, inject, input, output, signal, viewChild } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';

import { Box2D, CitationRef } from '../../models/compliance.models';
import { ComplianceApiService } from '../../services/compliance-api.service';

@Component({
  selector: 'app-bbox-svg-viewer',
  imports: [MatCardModule, MatButtonModule],
  templateUrl: './bbox-svg-viewer.component.html',
  styleUrl: './bbox-svg-viewer.component.scss',
})
export class BboxSvgViewerComponent {
  private readonly api = inject(ComplianceApiService);

  readonly docId = input<string | null>(null);
  readonly pageNumber = input<number>(1);
  readonly totalPages = input<number>(1);
  readonly citation = input<CitationRef | null>(null);
  readonly docLabel = input<string>('');
  readonly compareCitations = input<CitationRef[]>([]);
  readonly docLabelsById = input<Record<string, string>>({});

  readonly scrollContainer = viewChild<ElementRef<HTMLElement>>('scrollContainer');
  readonly pageChange = output<number>();
  readonly openFullCitation = output<CitationRef>();

  readonly naturalWidth = signal(1132);
  readonly naturalHeight = signal(1600);
  readonly imageError = signal(false);
  readonly dimsByUrl = signal<Record<string, { w: number; h: number }>>({});

  // Zoom (0.5x to 4.0x) and hover Magnifier Loupe state
  readonly zoom = signal(1);
  readonly magnifierActive = signal(false);
  readonly loupeVisible = signal(false);
  readonly loupeX = signal(0);
  readonly loupeY = signal(0);
  readonly stageWidthPx = signal(600);
  readonly stageHeightPx = signal(800);

  readonly zoomPercent = computed(() => Math.round(this.zoom() * 100));

  readonly imageUrl = computed(() => {
    const id = this.docId();
    return id ? this.api.getPageImageUrl(id, this.pageNumber()) : null;
  });

  constructor() {
    effect(() => {
      this.imageUrl();
      this.imageError.set(false);
      this.loupeVisible.set(false);
    });
    effect(() => {
      for (const cite of this.compareCitations()) {
        const url = this.api.getPageImageUrl(cite.doc_id, cite.page);
        if (this.dimsByUrl()[url] || typeof Image === 'undefined') continue;
        const probe = new Image();
        probe.onload = () => {
          if (probe.naturalWidth > 0 && probe.naturalHeight > 0) {
            this.dimsByUrl.update((prev) => ({
              ...prev,
              [url]: { w: probe.naturalWidth, h: probe.naturalHeight },
            }));
          }
        };
        probe.src = url;
      }
    });
  }

  getCitationImageUrl(cite: CitationRef): string {
    return this.api.getPageImageUrl(cite.doc_id, cite.page);
  }

  getCitationDocLabel(cite: CitationRef): string {
    return this.docLabelsById()[cite.doc_id] || cite.doc_id;
  }

  getCitationDims(cite: CitationRef): { w: number; h: number } {
    const url = this.getCitationImageUrl(cite);
    return this.dimsByUrl()[url] ?? { w: 1132, h: 1600 };
  }

  getCitationBoxes(cite: CitationRef): Box2D[] {
    return [cite.box_2d, ...(cite.extra_boxes_2d ?? [])];
  }

  getCropViewBox(cite: CitationRef): string {
    const { w, h } = this.getCitationDims(cite);
    const boxes = this.getCitationBoxes(cite);
    const ymin = Math.min(...boxes.map((b) => b[0]));
    const xmin = Math.min(...boxes.map((b) => b[1]));
    const ymax = Math.max(...boxes.map((b) => b[2]));
    const xmax = Math.max(...boxes.map((b) => b[3]));

    const bx = (xmin / 1000) * w;
    const by = (ymin / 1000) * h;
    const bw = Math.max(1, ((xmax - xmin) / 1000) * w);
    const bh = Math.max(1, ((ymax - ymin) / 1000) * h);

    const padX = Math.max(bw * 0.35, w * 0.08);
    const padY = Math.max(bh * 0.75, h * 0.07);
    const vx = Math.max(0, bx - padX);
    const vy = Math.max(0, by - padY);
    const vx2 = Math.min(w, bx + bw + padX);
    const vy2 = Math.min(h, by + bh + padY);
    return `${vx} ${vy} ${Math.max(1, vx2 - vx)} ${Math.max(1, vy2 - vy)}`;
  }

  scaleBoxRect(box: Box2D, cite: CitationRef): { x: number; y: number; width: number; height: number } {
    const { w, h } = this.getCitationDims(cite);
    return {
      x: (box[1] / 1000) * w,
      y: (box[0] / 1000) * h,
      width: box[3] > box[1] ? ((box[3] - box[1]) / 1000) * w : 0,
      height: box[2] > box[0] ? ((box[2] - box[0]) / 1000) * h : 0,
    };
  }

  readonly activeBoxes = computed<Box2D[]>(() => {
    const c = this.citation();
    if (!c || c.doc_id !== this.docId() || c.page !== this.pageNumber()) {
      return [];
    }
    return [c.box_2d, ...(c.extra_boxes_2d ?? [])];
  });

  readonly svgAriaLabel = computed(() => {
    const count = this.activeBoxes().length;
    return count > 0
      ? `文件頁面標註檢視 (${count} 個高亮區域)`
      : '文件頁面檢視';
  });

  onImageLoad(event: Event): void {
    const img = event.target as HTMLImageElement;
    this.imageError.set(false);
    if (img.naturalWidth > 0 && img.naturalHeight > 0) {
      this.naturalWidth.set(img.naturalWidth);
      this.naturalHeight.set(img.naturalHeight);
    }
    this.scrollToFocusBox();
  }

  private scrollToFocusBox(): void {
    const boxes = this.activeBoxes();
    const container = this.scrollContainer()?.nativeElement;
    if (!container || boxes.length === 0 || container.scrollHeight <= container.clientHeight) {
      return;
    }
    const [ymin, , ymax] = boxes[0];
    const centerFraction = (ymin + ymax) / 2000;
    container.scrollTop = Math.max(0, container.scrollHeight * centerFraction - container.clientHeight / 2);
  }

  onImageError(): void {
    this.imageError.set(true);
  }

  zoomIn(): void {
    this.zoom.update((z) => Math.min(4, +(z + 0.25).toFixed(2)));
  }

  zoomOut(): void {
    this.zoom.update((z) => Math.max(0.5, +(z - 0.25).toFixed(2)));
  }

  resetZoom(): void {
    this.zoom.set(1);
  }

  toggleMagnifier(): void {
    this.magnifierActive.update((v) => !v);
    if (!this.magnifierActive()) {
      this.loupeVisible.set(false);
    }
  }

  onStageMouseMove(event: MouseEvent): void {
    if (!this.magnifierActive()) return;
    const stage = event.currentTarget as HTMLElement;
    const rect = stage.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const relX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
    const relY = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
    this.stageWidthPx.set(rect.width);
    this.stageHeightPx.set(rect.height);
    this.loupeX.set(relX);
    this.loupeY.set(relY);
    this.loupeVisible.set(true);
  }

  onStageMouseLeave(): void {
    this.loupeVisible.set(false);
  }

  prevPage(): void {
    if (this.pageNumber() > 1) {
      this.pageChange.emit(this.pageNumber() - 1);
    }
  }

  nextPage(): void {
    if (this.pageNumber() < this.totalPages()) {
      this.pageChange.emit(this.pageNumber() + 1);
    }
  }
}
