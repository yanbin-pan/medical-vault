import { useEffect, useState } from "react";

export interface TooltipState {
  x: number;
  y: number;
  title: string;
  rows: string[];
}

/** A single floating tooltip, positioned to stay on screen. */
export function Tooltip({ state }: { state: TooltipState | null }) {
  const [flip, setFlip] = useState(false);

  useEffect(() => {
    if (state) setFlip(state.x > window.innerWidth - 280);
  }, [state]);

  if (!state) return null;
  return (
    <div
      className="tooltip"
      role="status"
      style={{
        left: flip ? undefined : state.x + 14,
        right: flip ? window.innerWidth - state.x + 14 : undefined,
        top: Math.min(state.y + 14, window.innerHeight - 120),
      }}
    >
      <div className="t-title">{state.title}</div>
      {state.rows.map((row) => (
        <div className="t-row" key={row}>
          {row}
        </div>
      ))}
    </div>
  );
}
