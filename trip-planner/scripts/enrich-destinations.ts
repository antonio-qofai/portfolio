// One-time data prep: adds coordinates (for weather and travel time) and the
// main airport (for flight searches) to every destination. Coordinates come
// from Open-Meteo geocoding, filtered by country so "Valencia" is Spain, not
// Venezuela. Airports are picked by hand. Re-run after adding destinations:
//   node scripts/enrich-destinations.ts

import { readFileSync, writeFileSync } from "node:fs";

const FILE = new URL("../data/destinations.json", import.meta.url);

// id -> [geocoding query, ISO country code, airport IATA code]
const PLACES: Record<string, [string, string, string]> = {
  miami_beach: ["Miami Beach", "US", "MIA"],
  vancouver: ["Vancouver", "CA", "YVR"],
  cancun: ["Cancún", "MX", "CUN"],
  denver: ["Denver", "US", "DEN"],
  lisbon: ["Lisbon", "PT", "LIS"],
  chicago: ["Chicago", "US", "ORD"],
  tokyo: ["Tokyo", "JP", "HND"],
  hong_kong: ["Hong Kong", "HK", "HKG"],
  bangkok: ["Bangkok", "TH", "BKK"],
  shkoder_alps: ["Shkodër", "AL", "TIA"],
  new_york_city: ["New York", "US", "JFK"],
  las_vegas: ["Las Vegas", "US", "LAS"],
  budapest: ["Budapest", "HU", "BUD"],
  new_orleans: ["New Orleans", "US", "MSY"],
  rome: ["Rome", "IT", "FCO"],
  petra: ["Wadi Musa", "JO", "AMM"],
  reykjavik: ["Reykjavík", "IS", "KEF"],
  cape_town: ["Cape Town", "ZA", "CPT"],
  banff: ["Banff", "CA", "YYC"],
  marrakech: ["Marrakesh", "MA", "RAK"],
  santorini: ["Fira", "GR", "JTR"],
  barcelona: ["Barcelona", "ES", "BCN"],
  dubai: ["Dubai", "AE", "DXB"],
  bali: ["Ubud", "ID", "DPS"],
  amsterdam: ["Amsterdam", "NL", "AMS"],
  seoul: ["Seoul", "KR", "ICN"],
  phuket: ["Phuket", "TH", "HKT"],
  prague: ["Prague", "CZ", "PRG"],
  queenstown: ["Queenstown", "NZ", "ZQN"],
  istanbul: ["Istanbul", "TR", "IST"],
  honolulu: ["Honolulu", "US", "HNL"],
  mexico_city: ["Mexico City", "MX", "MEX"],
  medellin: ["Medellín", "CO", "MDE"],
  porto: ["Porto", "PT", "OPO"],
  krakow: ["Kraków", "PL", "KRK"],
  valencia: ["Valencia", "ES", "VLC"],
  athens: ["Athens", "GR", "ATH"],
  hanoi: ["Hanoi", "VN", "HAN"],
  ho_chi_minh_city: ["Ho Chi Minh City", "VN", "SGN"],
  kuala_lumpur: ["Kuala Lumpur", "MY", "KUL"],
  sarajevo: ["Sarajevo", "BA", "SJJ"],
  belgrade: ["Belgrade", "RS", "BEG"],
  bucharest: ["Bucharest", "RO", "OTP"],
  tbilisi: ["Tbilisi", "GE", "TBS"],
  san_juan: ["San Juan", "PR", "SJU"],
  charleston: ["Charleston", "US", "CHS"],
  savannah: ["Savannah", "US", "SAV"],
  nashville: ["Nashville", "US", "BNA"],
  philadelphia: ["Philadelphia", "US", "PHL"],
  montreal: ["Montreal", "CA", "YUL"],
  quebec_city: ["Québec", "CA", "YQB"],
  accra: ["Accra", "GH", "ACC"],
  zanzibar: ["Zanzibar", "TZ", "ZNZ"],
  addis_ababa: ["Addis Ababa", "ET", "ADD"],
  bratislava: ["Bratislava", "SK", "BTS"],
  ljubljana: ["Ljubljana", "SI", "LJU"],
  skopje: ["Skopje", "MK", "SKP"],
  almaty: ["Almaty", "KZ", "ALA"],
  doha: ["Doha", "QA", "DOH"],
  la_paz: ["La Paz", "BO", "LPB"],
  guatemala_city: ["Guatemala City", "GT", "GUA"],
};

type Result = { name: string; latitude: number; longitude: number; population?: number; country_code: string };

async function geocode(query: string, country: string): Promise<Result> {
  const url = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query)}&count=10&language=en&format=json&countryCode=${country}`;
  const results: Result[] = (await (await fetch(url)).json()).results ?? [];
  const plain = (s: string) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  // Exact name matches first (so "Vancouver" beats "Vancouver Island"), then the most populous.
  const best = results
    .filter(r => r.country_code === country)
    .sort((a, b) => Number(plain(b.name) === plain(query)) - Number(plain(a.name) === plain(query)) || (b.population ?? 0) - (a.population ?? 0))[0];
  if (!best) throw new Error(`No geocoding result for ${query}, ${country}`);
  return best;
}

const destinations = JSON.parse(readFileSync(FILE, "utf8"));
for (const d of destinations) {
  const place = PLACES[d.id];
  if (!place) throw new Error(`Add ${d.id} to PLACES`);
  const [query, country, airport] = place;
  const r = await geocode(query, country);
  d.latitude = Math.round(r.latitude * 1e4) / 1e4;
  d.longitude = Math.round(r.longitude * 1e4) / 1e4;
  d.airport = airport;
  console.log(`${d.id.padEnd(18)} ${r.name.padEnd(20)} ${d.latitude}, ${d.longitude}  ${airport}`);
}
writeFileSync(FILE, JSON.stringify(destinations, null, 2) + "\n");
