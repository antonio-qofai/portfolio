// Runs every sample group end to end and pins the outcome, so a scoring change
// that flips a result shows up as a failing test instead of going unnoticed.

import fs from "fs";
import path from "path";
import { describe, expect, test } from "vitest";
import { DESTINATIONS, parseSurveyCsv, planTrip } from "../planner";

const load = (file: string) =>
  planTrip(parseSurveyCsv(fs.readFileSync(path.join("data/samples", file), "utf8")), DESTINATIONS);

describe("sample groups", () => {
  test("big-budget group: home cities excluded, San Juan wins", () => {
    const plan = load("big-budget-group.csv");
    expect(plan.status).toBe("ok");
    expect(plan.checks).toEqual({ passed: true, failed: [] });
    expect(plan.winner?.destination.id).toBe("san_juan");
    for (const home of ["nashville", "charleston", "chicago", "savannah", "philadelphia"]) {
      expect(plan.winner?.destination.id).not.toBe(home);
      expect(plan.runner_up?.destination.id).not.toBe(home);
    }
  });

  test("Chicago beach group: warm beach destination inside the tightest budget", () => {
    const plan = load("chicago-beach-group.csv");
    expect(plan.status).toBe("ok");
    expect(plan.checks.passed).toBe(true);
    expect(plan.winner?.destination.id).toBe("miami_beach");
    expect(plan.winner!.destination.estimated_cost_per_person_usd).toBeLessThanOrEqual(plan.budget!.per_person_limit_usd);
  });

  test("mixed group: no passport plus a $900 budget leaves nothing, and the fix names Charleston", () => {
    const plan = load("mixed-group.csv");
    expect(plan.status).toBe("error");
    expect(plan.checks.passed).toBe(true);
    expect(plan.runner_up).toMatchObject({ feasible: false, destination: { id: "charleston" } });
    expect(plan.suggested_fixes[0]).toMatch(/Omar's budget to \$1,100/);
  });
});
