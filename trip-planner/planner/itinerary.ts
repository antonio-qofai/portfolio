// Template itinerary. This is the original version's day planner, made
// deterministic (same input, same plan) and driven by what the group actually
// asked for instead of cycling through the destination's tags. It is also the
// fallback whenever a generated itinerary is unavailable.

import { addDays } from "./dates";
import { tagsForInterest } from "./scoring";
import type { Destination, ItineraryDay, Traveler } from "./types";

const ACTIVITIES: Record<string, string[]> = {
  nightlife: [
    "Evening bar crawl and a look at the local music scene.",
    "Late dinner, then out to the neighborhood the locals go to.",
    "Sunset drinks at a rooftop bar.",
  ],
  relaxation: [
    "Slow morning, then an afternoon at a spa or thermal bath.",
    "Unstructured afternoon for a park, a cafe, or a nap.",
    "Sunset walk and an early, unhurried dinner.",
  ],
  food: [
    "Guided street food tour through the local specialties.",
    "Dinner at a well-reviewed local restaurant, booked ahead for the group.",
    "Morning at the main food market, then a cooking class.",
  ],
  water_sports: [
    "Morning paddleboarding or snorkeling session.",
    "Afternoon boat rental or kayak tour along the coast.",
  ],
  snorkeling: ["Half-day snorkeling trip to the nearest reef."],
  hiking: [
    "Early hike to the best viewpoint within an hour of town.",
    "Full-day trail with a packed lunch at the top.",
  ],
  museums: [
    "The city's main art or history museum, with lunch nearby.",
    "A smaller specialist museum or gallery the guidebooks underrate.",
  ],
  scenic_views: [
    "Cable car or short climb to a panoramic lookout.",
    "Sunset from the best-known viewpoint, then dinner close by.",
  ],
  shopping: [
    "Afternoon on the main shopping street and a local design district.",
    "Artisan market for crafts and souvenirs.",
  ],
  culture: [
    "Traditional performance or show in the evening.",
    "Historic temple, cathedral, or palace, with a guide.",
    "Walk through the old town's heritage architecture.",
  ],
  adventure: [
    "Zip-lining, rafting, or canyoning with a local outfitter.",
    "Guided climbing or cave trip for the adventurous half of the group.",
  ],
  beach: [
    "Full beach day with lunch by the water.",
    "Beach morning, then a seaside promenade walk in the afternoon.",
  ],
  history: [
    "Walking tour of the historic sites and ruins.",
    "A UNESCO World Heritage site within a short trip of town.",
  ],
  nature: [
    "Botanical garden or nature reserve in the morning.",
    "Wildlife or eco tour with a local guide.",
  ],
  markets: [
    "Morning at the central market for spices, textiles, and snacks.",
    "Night market for shopping and street food.",
  ],
  walking_tours: [
    "Guided walking tour of the historic center.",
    "Self-guided route through the side streets and squares.",
  ],
  architecture: ["Architecture walk past the city's landmark buildings."],
  music: ["Live music venue for the evening."],
  wine: ["Afternoon wine tasting at a nearby vineyard or wine bar."],
};

/** Stable small hash, so a destination always gets the same variations. */
function hash(s: string): number {
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h;
}

/**
 * The destination's tags ordered by how many travelers they serve, so the
 * most widely shared interests get days first.
 */
export function rankedThemes(d: Destination, travelers: Traveler[]): string[] {
  const tags = [...new Set([...d.activity_tags, ...d.climate_tags])].filter(t => ACTIVITIES[t]);
  const served = (tag: string) => travelers.filter(t => t.interests.some(i => tagsForInterest(i).includes(tag))).length;
  return tags
    .map(tag => ({ tag, n: served(tag) }))
    .sort((a, b) => b.n - a.n || a.tag.localeCompare(b.tag))
    .map(x => x.tag);
}

function label(tag: string): string {
  return tag.replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase());
}

export function templateItinerary(d: Destination, travelers: Traveler[], start: string, days: number): ItineraryDay[] {
  const themes = rankedThemes(d, travelers);
  const seed = hash(d.id);
  const pick = (tag: string, i: number) => {
    const options = ACTIVITIES[tag];
    return options[(seed + i) % options.length];
  };

  return Array.from({ length: days }, (_, i) => {
    const date = addDays(start, i);
    if (i === 0) {
      const theme = themes[0];
      return {
        day: 1,
        date,
        title: "Arrival",
        activity: theme
          ? `Arrive and check in. ${pick(theme, i)}`
          : "Arrive, check in, and have a welcome dinner together.",
      };
    }
    if (i === days - 1) {
      return { day: days, date, title: "Departure", activity: "Easy last morning, then head to the airport." };
    }
    const theme = themes[i % Math.max(1, themes.length)];
    return theme
      ? { day: i + 1, date, title: label(theme), activity: pick(theme, i) }
      : { day: i + 1, date, title: "Explore", activity: "Free day to explore the neighborhood." };
  });
}
