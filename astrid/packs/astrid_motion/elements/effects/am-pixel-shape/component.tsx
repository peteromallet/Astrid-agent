import type {ReactElement} from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {COLOR, LOGICAL_PX, finiteNumber, integerIn, narrowParams, oneOf, type ElementComponentProps} from '../../_shared/am';
import {PIXEL_SHAPES, isBlinking, shapeCells, type PixelShape} from './shape-cells';

// am-pixel-shape: a procedural pixel mark on the sticker grid. No asset, no
// animation except blinkAt, which hides it for two frames as am-sprite does.
// Each cell is px_scale screen px; the shape's top-left sits at (x, y) logical
// px, the same placement as am-sprite.

type Params = {
  shape?: PixelShape;
  color?: string;
  px_scale?: number;
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  stroke?: number;
  blinkAt?: number | number[];
};

export default function AmPixelShape(props: ElementComponentProps): ReactElement | null {
  const frame = useCurrentFrame();
  const params = narrowParams<Params>(props.params);
  const shape = oneOf<PixelShape>(params.shape, PIXEL_SHAPES, 'ring');
  const px = integerIn(params.px_scale, 1, 24, LOGICAL_PX);
  const w = integerIn(params.w, 1, 200, 24);
  const h = integerIn(params.h, 1, 200, 24);
  const stroke = integerIn(params.stroke, 1, Math.min(w, h), 2);
  const color = typeof params.color === 'string' && params.color !== '' ? params.color : COLOR.charcoal;
  const x = finiteNumber(params.x, 0);
  const y = finiteNumber(params.y, 0);
  if (isBlinking(frame, params.blinkAt)) {
    return null;
  }

  const cells = shapeCells(shape, w, h, stroke);
  // One path, one unit square per cell: hard edges, no anti-aliasing seams.
  const d = cells.map(([col, row]) => `M${col} ${row}h1v1h-1z`).join('');

  return (
    <AbsoluteFill style={{overflow: 'hidden', pointerEvents: 'none'}}>
      <svg
        style={{position: 'absolute', left: x * LOGICAL_PX, top: y * LOGICAL_PX, overflow: 'visible'}}
        width={w * px}
        height={h * px}
        viewBox={`0 0 ${w} ${h}`}
        shapeRendering="crispEdges"
      >
        <path d={d} fill={color} />
      </svg>
    </AbsoluteFill>
  );
}
