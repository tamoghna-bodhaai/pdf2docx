export function Progress({ value, label }: { value: number; label: string }) {
  const bounded = Math.max(0, Math.min(100, value));
  return (
    <div className="upload-progress" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={bounded}>
      <div className="progress-track"><i style={{ width: `${bounded}%` }} /></div>
      <p aria-live="polite">{label}</p>
    </div>
  );
}
