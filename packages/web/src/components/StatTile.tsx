export function StatTile({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string | number;
  note?: string;
  tone?: "warn" | "crit" | "ok";
}) {
  return (
    <div className="card tile">
      <div className="label">{label}</div>
      {/* The number wears text ink, never a series colour; the pill carries state. */}
      <div className="value">{value}</div>
      {note && (
        <div className="note">
          {tone ? <span className={`pill ${tone}`}>{note}</span> : note}
        </div>
      )}
    </div>
  );
}
