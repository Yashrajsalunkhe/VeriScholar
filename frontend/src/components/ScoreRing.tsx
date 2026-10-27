interface ScoreRingProps {
  value: number;
  maxValue?: number;
  size?: number;
  strokeWidth?: number;
  label?: string;
}

export default function ScoreRing({
  value,
  maxValue = 100,
  size = 140,
  strokeWidth = 8,
  label = 'similarity',
}: ScoreRingProps) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = Math.min(value / maxValue, 1);
  const offset = circumference * (1 - pct);

  // Color thresholds
  let color = '#22c55e'; // green < 15%
  if (value >= 50) color = '#ef4444'; // red
  else if (value >= 25) color = '#f59e0b'; // amber
  else if (value >= 15) color = '#3b82f6'; // blue

  return (
    <div className="score-ring" style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`}>
        <circle
          className="bg-circle"
          cx={size / 2}
          cy={size / 2}
          r={radius}
        />
        <circle
          className="fg-circle"
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke={color}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      <div className="score-text">
        <span className="score-number" style={{ color }}>
          {value.toFixed(1)}%
        </span>
        <span className="score-label">{label}</span>
      </div>
    </div>
  );
}
