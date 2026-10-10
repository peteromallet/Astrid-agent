// am-pixel-shape geometry: which cells of a w x h grid are inked for a shape.
// Pure (no imports), so tests can run it directly under node. Cells are [col, row].

export type PixelShape = 'ring' | 'dot' | 'underline' | 'arrow';
export const PIXEL_SHAPES: readonly PixelShape[] = ['ring', 'dot', 'underline', 'arrow'];

export const shapeCells = (shape: PixelShape, w: number, h: number, stroke: number): [number, number][] => {
  const cells: [number, number][] = [];
  const cx = w / 2;
  const cy = h / 2;
  if (shape === 'dot' || shape === 'ring') {
    const rx = w / 2;
    const ry = h / 2;
    const irx = rx - stroke;
    const iry = ry - stroke;
    for (let row = 0; row < h; row += 1) {
      for (let col = 0; col < w; col += 1) {
        const dx = (col + 0.5 - cx) / rx;
        const dy = (row + 0.5 - cy) / ry;
        if (dx * dx + dy * dy > 1) continue;
        if (shape === 'ring' && irx > 0 && iry > 0) {
          const ix = (col + 0.5 - cx) / irx;
          const iy = (row + 0.5 - cy) / iry;
          if (ix * ix + iy * iy <= 1) continue;
        }
        cells.push([col, row]);
      }
    }
  } else if (shape === 'underline') {
    for (let row = Math.max(0, h - stroke); row < h; row += 1) {
      for (let col = 0; col < w; col += 1) cells.push([col, row]);
    }
  } else {
    // arrow: a shaft of `stroke` rows, then a head whose base is the full height
    // and whose tip is at the right edge.
    const head = Math.min(w - 1, Math.max(2, Math.round(h / 2)));
    for (let row = 0; row < h; row += 1) {
      const off = Math.abs(row + 0.5 - cy);
      if (off <= stroke / 2) {
        for (let col = 0; col < w - head; col += 1) cells.push([col, row]);
      }
    }
    for (let k = 0; k < head; k += 1) {
      const half = (h / 2) * (1 - k / head);
      const col = w - head + k;
      for (let row = 0; row < h; row += 1) {
        if (Math.abs(row + 0.5 - cy) <= half) cells.push([col, row]);
      }
    }
  }
  return cells;
};

// Blink rule, the same as am-sprite's blinkAt: the shape hides for two frames
// starting at each listed clip frame. A number or an array of numbers.
export const isBlinking = (frame: number, blinkAt: number | number[] | null | undefined): boolean => {
  const blinks: number[] = Array.isArray(blinkAt) ? blinkAt : typeof blinkAt === 'number' ? [blinkAt] : [];
  return blinks.some((at) => frame >= at && frame < at + 2);
};
