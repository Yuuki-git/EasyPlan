export function formatPreviewEffort(minutes: number | null | undefined): string {
  if (minutes == null) return '投入未知';
  if (minutes <= 15) return '低投入';
  if (minutes <= 30) return '中投入';
  return '较重投入';
}

export function formatBoardMinutes(minutes: number | null | undefined): string | null {
  if (minutes == null) return null;
  const rounded = minutes <= 30 ? Math.round(minutes / 5) * 5 : Math.round(minutes / 10) * 10;
  return `${Math.max(5, rounded)} 分钟`;
}
