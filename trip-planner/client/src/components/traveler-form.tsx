import { useState } from "react";
import { Plus, X } from "lucide-react";
import { CLIMATE_OPTIONS, INTEREST_OPTIONS, travelerSchema, type Traveler, type TravelerInput } from "@planner";
import { Button } from "@/components/ui/button";
import { Chip, Field, Select, TextArea, TextInput, Toggle, label } from "@/components/form";
import { ApiError } from "@/lib/api";

type Errors = Record<string, string | undefined>;

interface FormState {
  name: string;
  departure_city: string;
  home_airport: string;
  availability: { start: string; end: string }[];
  budget: string;
  flex: string;
  interests: string[];
  climate: string;
  passport_ready: boolean;
  avoid_crowds: boolean;
  max_hours: string;
  notes: string;
}

function fromTraveler(t: Traveler | undefined, window: { from: string; to: string }): FormState {
  return {
    name: t?.name ?? "",
    departure_city: t?.departure_city ?? "",
    home_airport: t?.home_airport ?? "",
    availability: t?.availability ?? [{ start: window.from, end: window.to }],
    budget: t ? String(t.budget_usd) : "",
    flex: t ? String(t.budget_flex_pct) : "0.1",
    interests: t?.interests ?? [],
    climate: t?.climate_preference ?? "",
    passport_ready: t?.passport_ready ?? true,
    avoid_crowds: t?.avoid_crowds ?? false,
    max_hours: t?.max_travel_hours ? String(t.max_travel_hours) : "",
    notes: t?.notes ?? "",
  };
}

function toInput(f: FormState): TravelerInput {
  return {
    name: f.name,
    departure_city: f.departure_city,
    home_airport: f.home_airport,
    availability: f.availability,
    budget_usd: Number(f.budget.replace(/[$,]/g, "")) || 0,
    budget_flex_pct: Number(f.flex),
    interests: f.interests,
    climate_preference: (f.climate || undefined) as TravelerInput["climate_preference"],
    passport_ready: f.passport_ready,
    avoid_crowds: f.avoid_crowds,
    max_travel_hours: f.max_hours ? Number(f.max_hours) : undefined,
    notes: f.notes,
  };
}

export function TravelerForm({ initial, window, submitLabel, onSubmit }: {
  initial?: Traveler;
  window: { from: string; to: string };
  submitLabel: string;
  onSubmit: (answers: TravelerInput) => Promise<void>;
}) {
  const [f, setF] = useState<FormState>(() => fromTraveler(initial, window));
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF(prev => ({ ...prev, [k]: v }));

  const setRange = (i: number, key: "start" | "end", value: string) =>
    set("availability", f.availability.map((r, j) => (j === i ? { ...r, [key]: value } : r)));

  const toggleInterest = (tag: string) =>
    set("interests", f.interests.includes(tag) ? f.interests.filter(t => t !== tag) : [...f.interests, tag]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    const input = toInput(f);
    const parsed = travelerSchema.safeParse(input);
    if (!parsed.success) {
      const fe = parsed.error.flatten().fieldErrors as Record<string, string[]>;
      // Date-range errors are nested; surface them on the availability field.
      const rangeIssue = parsed.error.issues.find(i => i.path[0] === "availability");
      setErrors({ ...Object.fromEntries(Object.entries(fe).map(([k, v]) => [k, v[0]])), availability: rangeIssue?.message });
      return;
    }
    setErrors({});
    setBusy(true);
    try {
      await onSubmit(input);
    } catch (err) {
      if (err instanceof ApiError) {
        setFormError(err.message);
        setErrors(Object.fromEntries(Object.entries(err.fieldErrors).map(([k, v]) => [k, v[0]])));
      } else setFormError("Could not save. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-6" noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Your name" error={errors.name}>
          <TextInput value={f.name} onChange={e => set("name", e.target.value)} autoComplete="given-name" invalid={!!errors.name} />
        </Field>
        <div className="grid grid-cols-[1fr_96px] gap-3">
          <Field label="Flying from" error={errors.departure_city}>
            <TextInput value={f.departure_city} onChange={e => set("departure_city", e.target.value)} placeholder="City" invalid={!!errors.departure_city} />
          </Field>
          <Field label="Airport" error={errors.home_airport}>
            <TextInput value={f.home_airport} onChange={e => set("home_airport", e.target.value.toUpperCase())} placeholder="ORD" maxLength={3} invalid={!!errors.home_airport} />
          </Field>
        </div>
      </div>

      <Field label="When are you free?" hint="Add more than one range if you have gaps." error={errors.availability}>
        <div className="space-y-2">
          {f.availability.map((r, i) => (
            <div key={i} className="flex items-center gap-2">
              <TextInput type="date" value={r.start} min={window.from} max={window.to} onChange={e => setRange(i, "start", e.target.value)} />
              <span className="text-muted-foreground text-sm">to</span>
              <TextInput type="date" value={r.end} min={r.start || window.from} max={window.to} onChange={e => setRange(i, "end", e.target.value)} />
              {f.availability.length > 1 && (
                <Button type="button" variant="ghost" size="icon" aria-label="Remove range"
                  onClick={() => set("availability", f.availability.filter((_, j) => j !== i))}>
                  <X className="h-4 w-4" />
                </Button>
              )}
            </div>
          ))}
          {f.availability.length < 5 && (
            <Button type="button" variant="ghost" size="sm" className="px-0 text-primary"
              onClick={() => set("availability", [...f.availability, { start: window.from, end: window.to }])}>
              <Plus className="h-4 w-4" /> Add another range
            </Button>
          )}
        </div>
      </Field>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Total budget for the trip (USD)" hint="Flights, lodging, and spending." error={errors.budget_usd}>
          <TextInput inputMode="numeric" value={f.budget} onChange={e => set("budget", e.target.value)} placeholder="1500" invalid={!!errors.budget_usd} />
        </Field>
        <Field label="How firm is that?">
          <Select value={f.flex} onChange={e => set("flex", e.target.value)}>
            <option value="0">Hard limit</option>
            <option value="0.1">Could stretch 10%</option>
            <option value="0.2">Could stretch 20%</option>
            <option value="0.3">Flexible (30%)</option>
          </Select>
        </Field>
      </div>

      <Field label="What do you want to do?" hint="Pick up to 8." error={errors.interests}>
        <div className="flex flex-wrap gap-2 pt-1">
          {INTEREST_OPTIONS.map(tag => (
            <Chip key={tag} selected={f.interests.includes(tag)} onClick={() => toggleInterest(tag)}>{label(tag)}</Chip>
          ))}
        </div>
      </Field>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Weather you'd like">
          <Select value={f.climate} onChange={e => set("climate", e.target.value)}>
            <option value="">No preference</option>
            {CLIMATE_OPTIONS.map(c => <option key={c} value={c}>{label(c)}</option>)}
          </Select>
        </Field>
        <Field label="Longest flight you'd take">
          <Select value={f.max_hours} onChange={e => set("max_hours", e.target.value)}>
            <option value="">Any length</option>
            {[3, 5, 8, 12].map(h => <option key={h} value={h}>{h} hours</option>)}
          </Select>
        </Field>
      </div>

      <div className="divide-y rounded-lg border bg-card px-4">
        <div className="py-2"><Toggle checked={f.passport_ready} onChange={v => set("passport_ready", v)} label="My passport is valid" hint="If anyone says no, the trip stays in the US." /></div>
        <div className="py-2"><Toggle checked={f.avoid_crowds} onChange={v => set("avoid_crowds", v)} label="I'd rather avoid crowded tourist spots" /></div>
      </div>

      <Field label="Anything else we should know?" hint="Diet, accessibility, dealbreakers, must-dos. The planner reads this." error={errors.notes}>
        <TextArea value={f.notes} onChange={e => set("notes", e.target.value)} maxLength={500}
          placeholder="e.g. Vegetarian. I use a wheelchair. No party towns." />
      </Field>

      {formError && <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{formError}</p>}
      <Button type="submit" size="lg" className="w-full h-12 text-base" disabled={busy}>
        {busy ? "Saving..." : submitLabel}
      </Button>
    </form>
  );
}
