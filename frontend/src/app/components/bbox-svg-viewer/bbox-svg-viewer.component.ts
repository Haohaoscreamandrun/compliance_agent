import { Component, computed, effect, inject, input, output, signal } from '@angular/core';

import { Box2D, CitationRef } from '../../models/compliance.models';
import { ComplianceApiService } from '../../services/compliance-api.service';

@Component({
  selector: 'app-bbox-svg-viewer',
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

  readonly imageUrl = computed(() => {
    const id = this.docId();
    return id ? this.api.getPageImageUrl(id, this.pageNumber()) : null;
  });

  constructor() {
    effect(() => {
      this.imageUrl();
      this.imageError.set(false);
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
