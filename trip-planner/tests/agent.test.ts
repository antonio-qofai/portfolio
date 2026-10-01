// The agent loop with a scripted stand-in for Claude: no network, no cost.

import { describe, expect, test } from "vitest";
import type { z } from "zod/v4";
import { DESTINATIONS, planTrip, type PlanResult, type Traveler, type TravelerConstraints } from "../planner";
import { runAgent } from "../server/agent";
import type { Store } from "../server/store";
import { emptyConstraints } from "../server/claude/constraints";
import { LlmError, type Llm, type StructuredRequest, type StructuredResponse } from "../server/claude/llm";
import { MAX_ATTEMPTS } from "../server/claude/itinerary";
import { MemoryStore } from "../server/store";
import { sampleTravelers, sampleTripStart } from "../server/sample-trip";

type Reply = unknown | Error;

/** Answers constraint and itinerary requests from separate scripts, and records what it was sent. */
class FakeLlm implements Llm {
  calls: { kind: "constraints" | "itinerary"; messages: StructuredRequest<z.ZodType>["messages"] }[] = [];
  constructor(private constraints: Reply[], private itineraries: (Reply | ((req: StructuredRequest<z.ZodType>) => Reply))[]) {}

  async structured<S extends z.ZodType>(req: StructuredRequest<S>): Promise<StructuredResponse<z.infer<S>>> {
    const kind = req.system.includes("day-by-day itinerary") ? "itinerary" : "constraints";
    this.calls.push({ kind, messages: [...req.messages] });
    const script = kind === "itinerary" ? this.itineraries : this.constraints;
    let reply = script.shift();
    if (typeof reply === "function") reply = reply(req);
    if (reply === undefined) throw new Error(`No scripted ${kind} reply left`);
    if (reply instanceof Error) throw reply;
    return {
      data: req.schema.parse(reply) as z.infer<S>,
      assistant: { role: "assistant", content: JSON.stringify(reply) },
      usage: { input_tokens: 100, output_tokens: 50, cost_usd: 0.001 },
    };
  }
}

const travelers = sampleTravelers(sampleTripStart("2026-10-01"));

function sampleConstraints(): { travelers: TravelerConstraints[] } {
  return {
    travelers: travelers.map(t => {
      const c = emptyConstraints(t.name);
      if (t.name === "Noah") return { ...c, mobility: "wheelchair" as const, avoid_tags: ["walking_tours"], summary: "Wheelchair user." };
      if (t.name === "Sofia") return { ...c, domestic_only: true, summary: "Expired passport." };
      if (t.name === "Maya") return { ...c, dietary: ["vegetarian"], avoid_tags: ["nightlife"], summary: "Vegetarian." };
      return c;
    }),
  };
}

/** A valid draft for whatever trip the planner chose, built from the request it was sent. */
function goodDraft(plan: PlanResult, leaveOut: string[] = []) {
  const names = plan.winner!.per_traveler.map(f => f.name).filter(n => !leaveOut.includes(n));
  return {
    summary: "It fits the group.",
    days: plan.itinerary.map((d, i) => ({ day: d.day, date: d.date, title: `Day ${i + 1}`, activity: "Something good.", for: i === 0 ? names : [names[0]] })),
  };
}

const deterministic = (t: Traveler[] = travelers) => planTrip(t, DESTINATIONS);

/** The agent with Claude only: no network calls for weather, travel time, or prices. */
const run = (t: Traveler[], llm: Llm | null, store: Store) => runAgent(t, { llm, store, publicData: false });

describe("agent", () => {
  test("without Claude it is the plain planner, with no agent trace", async () => {
    const plan = await run(travelers, null, new MemoryStore());
    expect(plan.agent).toBeUndefined();
    expect(plan.winner?.destination.id).toBe(deterministic().winner?.destination.id);
  });

  test("applies constraints, writes the itinerary, and records the trace", async () => {
    const base = deterministic();
    const llm = new FakeLlm([sampleConstraints()], [goodDraft(base)]);
    const plan = await run(travelers, llm, new MemoryStore());

    expect(plan.status).toBe("ok");
    expect(plan.constraints?.find(c => c.name === "Noah")?.mobility).toBe("wheelchair");
    expect(plan.agent).toMatchObject({ constraints: { source: "claude" }, itinerary: { source: "claude", attempts: [{ failed_checks: [] }] } });
    expect(plan.agent!.cost_usd).toBeCloseTo(0.002);
    expect(plan.itinerary[0].title).toBe("Day 1");
    expect(plan.summary).toBe("It fits the group.");
    expect(plan.checks.passed).toBe(true);
  });

  test("the itinerary brief carries each person's constraints", async () => {
    const llm = new FakeLlm([sampleConstraints()], [goodDraft(deterministic())]);
    await run(travelers, llm, new MemoryStore());
    const brief = JSON.parse(String(llm.calls[1].messages[0].content).split("<trip>")[1].split("</trip>")[0]);
    const noah = brief.travelers.find((t: { name: string }) => t.name === "Noah");
    expect(noah).toMatchObject({ mobility: "wheelchair", wants_to_avoid: ["walking_tours"] });
  });

  test("a failed draft goes back to Claude with the failures, and the retry is accepted", async () => {
    const base = deterministic();
    const llm = new FakeLlm([sampleConstraints()], [goodDraft(base, ["Dev"]), goodDraft(base)]);
    const plan = await run(travelers, llm, new MemoryStore());

    expect(plan.agent!.itinerary.attempts).toEqual([{ failed_checks: ["nobody_planned_for_Dev"] }, { failed_checks: [] }]);
    expect(plan.agent!.itinerary.source).toBe("claude");
    // The retry is the same conversation plus Claude's draft and the feedback.
    const retry = llm.calls[2].messages;
    expect(retry).toHaveLength(3);
    expect(retry[1].role).toBe("assistant");
    expect(String(retry[2].content)).toContain("Dev is not in any day's");
  });

  test(`after ${MAX_ATTEMPTS} failed drafts it falls back to the template itinerary`, async () => {
    const base = deterministic();
    const misdated = { ...goodDraft(base), days: goodDraft(base).days.slice(1) };
    const llm = new FakeLlm([sampleConstraints()], Array(MAX_ATTEMPTS).fill(misdated));
    const plan = await run(travelers, llm, new MemoryStore());

    expect(plan.agent!.itinerary.source).toBe("template");
    expect(plan.agent!.itinerary.attempts).toHaveLength(MAX_ATTEMPTS);
    expect(plan.agent!.itinerary.attempts[0].failed_checks).toContain("itinerary_wrong_length");
    expect(plan.itinerary).toHaveLength(plan.trip_window!.days);
    expect(plan.checks.passed).toBe(true);
  });

  test("if Claude errors, keyword rules and the template take over and the plan still comes back", async () => {
    const llm = new FakeLlm([new LlmError("Claude declined (cyber)")], [new LlmError("timeout")]);
    const plan = await run(travelers, llm, new MemoryStore());

    expect(plan.status).toBe("ok");
    expect(plan.agent!.constraints).toEqual({ source: "keywords", error: "Claude declined (cyber)" });
    expect(plan.agent!.itinerary).toEqual({ source: "template", attempts: [{ failed_checks: [], error: "timeout" }] });
    // Sofia's "has to be somewhere in the US" is still enforced by the passport flag.
    expect(plan.winner!.destination.country_or_region).toMatch(/USA/);
  });

  test("constraints are cached by the notes, so the same group is read once", async () => {
    const store = new MemoryStore();
    const base = deterministic();
    await run(travelers, new FakeLlm([sampleConstraints()], [goodDraft(base)]), store);

    const second = new FakeLlm([], [goodDraft(base)]);
    const plan = await run(travelers, second, store);
    expect(plan.agent!.constraints.source).toBe("cache");
    expect(second.calls.map(c => c.kind)).toEqual(["itinerary"]);

    // Changing anyone's notes means reading again.
    const edited = travelers.map(t => (t.name === "Dev" ? { ...t, notes: "Actually, I love nightlife." } : t));
    const third = new FakeLlm([sampleConstraints()], [goodDraft(deterministic(edited))]);
    await run(edited, third, store);
    expect(third.calls[0].kind).toBe("constraints");
  });

  test("a failed cache write does not count as a Claude failure", async () => {
    const store = new MemoryStore();
    store.setCached = async () => { throw new Error("invalid input syntax for type json"); };
    const plan = await run(travelers, new FakeLlm([sampleConstraints()], [goodDraft(deterministic())]), store);
    expect(plan.agent!.constraints).toEqual({ source: "claude" });
    expect(plan.constraints).toHaveLength(travelers.length);
  });

  test("a group with no notes skips the constraints call", async () => {
    const quiet = travelers.map(t => ({ ...t, notes: undefined }));
    const llm = new FakeLlm([], [goodDraft(deterministic(quiet))]);
    const plan = await run(quiet, llm, new MemoryStore());
    expect(llm.calls.map(c => c.kind)).toEqual(["itinerary"]);
    expect(plan.agent!.constraints.source).toBe("keywords");
  });
});

describe("constraints in the planner", () => {
  const two = travelers.slice(0, 2);

  test("a dealbreaker pushes a destination down the ranking", () => {
    const without = planTrip(two, DESTINATIONS);
    const winner = without.winner!.destination;
    const avoid = winner.activity_tags[0];
    const constraints = two.map(t => ({ ...emptyConstraints(t.name), avoid_tags: [avoid] }));
    const withIt = planTrip(two, DESTINATIONS, { constraints });
    expect(withIt.winner!.destination.id).not.toBe(winner.id);
    expect(withIt.winner!.score.components.dealbreakers).toBe(0);
  });

  test("domestic_only from notes restricts to the US even when everyone has a passport", () => {
    const constraints = two.map(t => ({ ...emptyConstraints(t.name), domestic_only: t.name === "Maya" }));
    const plan = planTrip(two, DESTINATIONS, { constraints });
    expect(plan.winner!.destination.country_or_region).toMatch(/USA/);
    expect(plan.excluded_counts.domestic_requested).toBeGreaterThan(0);
  });

  test("extra interests from notes count toward coverage", () => {
    const constraints = [{ ...emptyConstraints("Maya"), extra_interests: ["beach"] }, emptyConstraints("Dev")];
    const plan = planTrip(two, DESTINATIONS, { constraints });
    const maya = plan.winner!.per_traveler.find(f => f.name === "Maya")!;
    expect([...maya.matched_interests, ...maya.unmatched_interests]).toContain("beach");
  });
});
