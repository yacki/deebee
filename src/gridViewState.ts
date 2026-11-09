type Cell = { row: number; column: string };
type GridViewState = {
  cell?: Cell;
  selected: Set<number>;
  anchor: number;
  top: number;
  left: number;
  rowHeight: number;
  frozenThrough: number;
};

// Result arrays belong to their tabs. Weak keys retain a view while that result
// exists, without keeping replaced results or closed tabs alive.
export const gridViewStates = new WeakMap<Record<string, unknown>[], GridViewState>();
