export function rm(value: number | null | undefined): string {
  const amount = Number(value ?? 0);
  const sign = amount < 0 ? "-" : "";
  return `${sign}RM${Math.abs(amount).toLocaleString("en-MY", { maximumFractionDigits: 0 })}`;
}

export function m3(value: number | null | undefined): string {
  const amount = Number(value ?? 0);
  return `${amount.toLocaleString("en-MY", { maximumFractionDigits: 1 })} m³`;
}

export function num(value: number | null | undefined, digits = 1): string {
  return Number(value ?? 0).toLocaleString("en-MY", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export function longDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
