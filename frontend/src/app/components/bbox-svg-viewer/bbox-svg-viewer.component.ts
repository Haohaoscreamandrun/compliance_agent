import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
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

  readonly pageChange = output<number>();

  readonly naturalWidth = signal(1132);
  readonly naturalHeight = signal(1600);
  readonly imageError = signal(false);

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
