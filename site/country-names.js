// Country names in place of the bare ISO 3166-1 alpha-2 codes that program-written text carries in parentheses
// ("Elpro GmbH (DE)", "US embassy (MX)", "USA 123 (US)"), so a reader new to the codes can read every card.
// Only a code the item itself lists in its `countries` is named, so an acronym that looks like a code
// ("antidumping duty (AD)", "general license (GL)") stays as written, and a code already beside its name
// ("Iran (IR)") is left alone. The names are the browser's own (Intl.DisplayNames); where it has none, the text
// stays as the program wrote it. Only shown text changes: ids, links and the data files are never touched.
let regions = null;
try {
  regions = new Intl.DisplayNames(["en"], { type: "region", fallback: "none" });
} catch {
  regions = null;
}

const CODE = /^[A-Z]{2}$/;

/** The English name of an ISO alpha-2 code, or null when the browser has none. */
export function countryName(cc) {
  if (!regions || typeof cc !== "string" || !CODE.test(cc)) return null;
  try {
    // The short form: "Myanmar (Burma)" reads "Myanmar", so a name never carries brackets of its own.
    const name = String(regions.of(cc) || "").replace(/\s*\([^)]*\)$/, "");
    return name && name !== cc ? name : null;
  } catch {
    return null;
  }
}

/** `text` with each "(XX)" whose code is in `countries` read as "(Name)". */
export function withCountryNames(text, countries) {
  if (typeof text !== "string" || !Array.isArray(countries) || !countries.length) return text;
  const listed = new Set(countries.filter((c) => typeof c === "string" && CODE.test(c)));
  if (!listed.size) return text;
  return text.replace(/\(([A-Z]{2})\)/g, (whole, cc, at) => {
    if (!listed.has(cc)) return whole;
    const name = countryName(cc);
    if (!name || text.slice(0, at).trimEnd().endsWith(name)) return whole;
    return `(${name})`;
  });
}
