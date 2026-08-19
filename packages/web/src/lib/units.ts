/**
 * Render a UCUM unit the way a clinician would write it.
 *
 * Values are stored in UCUM because it is unambiguous and machine-comparable,
 * but `10*9/L` and `m[IU]/L` are notation for computers. Showing them raw makes
 * a report look like a database dump.
 */
const DISPLAY: Record<string, string> = {
  "10*9/L": "×10⁹/L",
  "10*12/L": "×10¹²/L",
  "10*3/uL": "×10³/µL",
  "10*6/uL": "×10⁶/µL",
  "/uL": "/µL",
  "mm[Hg]": "mmHg",
  "m[IU]/L": "mIU/L",
  "[IU]/L": "IU/L",
  "ug/L": "µg/L",
  "umol/L": "µmol/L",
  "kg/m2": "kg/m²",
  "mL/min/{1.73_m2}": "mL/min/1.73m²",
  // UCUM's dimensionless unit. A resistive index is a ratio; printing "· 1"
  // beside it reads as a stray number rather than as "no unit".
  "1": "",
};

export function displayUnit(unit: string | null | undefined): string {
  if (!unit) return "";
  return DISPLAY[unit] ?? unit;
}

/** True when the unit is worth showing at all. */
export function hasUnit(unit: string | null | undefined): boolean {
  return displayUnit(unit) !== "";
}
