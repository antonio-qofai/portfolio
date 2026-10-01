import destinationsJson from "../data/destinations.json";
import type { Destination } from "./types";

export const DESTINATIONS: Destination[] = destinationsJson;

export { planTrip, TARGET_DAYS, MIN_DAYS, SHORTLIST_SIZE } from "./plan";
export { parseSurveyCsv, parseTravelers, parseTraveler } from "./survey";
export { checkPlan, checkItinerary } from "./checks";
export type * from "./types";
export { travelerSchema, tripSchema, INTEREST_OPTIONS, CLIMATE_OPTIONS } from "./schema";
export type { TravelerInput, TripInput } from "./schema";
export { applyConstraints } from "./constraints";
export type { TravelerConstraints } from "./constraints";
