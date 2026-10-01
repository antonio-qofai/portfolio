// The organizer dashboard: share the link, watch answers come in, plan.

import { useCallback, useEffect, useState } from "react";

const PLAN_STAGES = ["Reading everyone's notes...", "Finding dates that work...", "Ranking destinations...", "Writing the itinerary...", "Checking the plan..."];

/** Cycles through stage labels while planning runs, so a 15-second wait doesn't look stuck. */
function usePlanStage(active: boolean) {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (!active) { setI(0); return; }
    const id = setInterval(() => setI(n => Math.min(n + 1, PLAN_STAGES.length - 1)), 3500);
    return () => clearInterval(id);
  }, [active]);
  return PLAN_STAGES[i];
}
import { Link } from "wouter";
import { Check, ChevronDown, Copy, Play, Trash2, UserPlus, Users } from "lucide-react";
import type { Traveler } from "@planner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { label } from "@/components/form";
import { Layout, Loading, PageError } from "@/components/layout";
import { PlanResultView } from "@/components/plan-result";
import { api, ApiError, type AdminTrip } from "@/lib/api";
import { adminKeyFromHash, saved, shareUrl } from "@/lib/saved";
import { toast } from "@/hooks/use-toast";
import { formatRange } from "./trip";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button variant="outline" onClick={async () => {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }}>
      {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />} {copied ? "Copied" : "Copy"}
    </Button>
  );
}

function AnswerSummary({ t }: { t: Traveler }) {
  const rows: [string, string][] = [
    ["From", `${t.departure_city}${t.home_airport ? ` (${t.home_airport})` : ""}`],
    ["Free", t.availability.map(r => formatRange(r.start, r.end)).join(", ")],
    ["Budget", `$${t.budget_usd.toLocaleString("en-US")}${t.budget_flex_pct ? `, can stretch ${Math.round(t.budget_flex_pct * 100)}%` : ", hard limit"}`],
    ["Wants", t.interests.map(label).join(", ")],
    ["Weather", t.climate_preference ? label(t.climate_preference) : "No preference"],
    ["Passport", t.passport_ready ? "Ready" : "Not ready"],
  ];
  if (t.max_travel_hours) rows.push(["Max flight", `${t.max_travel_hours} hours`]);
  if (t.avoid_crowds) rows.push(["Crowds", "Would rather avoid"]);
  if (t.notes) rows.push(["Notes", t.notes]);
  return (
    <dl className="grid grid-cols-[88px_1fr] gap-x-3 gap-y-1.5 text-sm">
      {rows.map(([k, v]) => [
        <dt key={`${k}-k`} className="text-muted-foreground">{k}</dt>,
        <dd key={`${k}-v`}>{v}</dd>,
      ])}
    </dl>
  );
}

export default function OrganizePage({ id }: { id: string }) {
  const key = adminKeyFromHash() ?? saved.organized()[id]?.admin_key ?? null;
  const [trip, setTrip] = useState<AdminTrip | null>(null);
  const [error, setError] = useState<string | null>(key ? null : "This page needs the organizer link.");
  const [busy, setBusy] = useState<string | null>(null);
  const stage = usePlanStage(busy === "plan");

  const load = useCallback(async () => {
    if (!key) return;
    try {
      const t = await api.getAdminTrip(id, key);
      setTrip(t);
      saved.rememberOrganized(id, t.name, key);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 404 ? "This trip doesn't exist or has expired."
        : e instanceof ApiError && e.status === 403 ? "That organizer link isn't valid." : "Could not load this trip.");
    }
  }, [id, key]);

  useEffect(() => { load(); }, [load]);

  async function run(action: string, fn: () => Promise<unknown>) {
    setBusy(action);
    try {
      await fn();
      await load();
    } catch (e) {
      toast({ variant: "destructive", description: e instanceof Error ? e.message : "Something went wrong." });
    } finally {
      setBusy(null);
    }
  }

  if (error) return <PageError message={error} />;
  if (!trip || !key) return <Loading />;

  const hasSample = trip.responses.some(r => r.is_sample);
  const plan = trip.plan;
  const stale = !!plan && (plan.group_size !== trip.responses.length || trip.responses.some(r => r.updated_at > plan.generated_at));

  return (
    <Layout wide>
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-widest text-primary">Organizer view</div>
          <h1 className="mt-1 text-3xl sm:text-4xl font-bold">{trip.name}</h1>
          <p className="mt-1 text-muted-foreground">{formatRange(trip.date_from, trip.date_to)} · {trip.responses.length} answered</p>
        </div>
        <Button size="lg" className="h-12 px-6 text-base" disabled={trip.responses.length === 0 || busy === "plan"}
          onClick={() => run("plan", () => api.plan(id, key))}>
          <Play className="h-4 w-4 fill-current" /> {busy === "plan" ? stage : trip.plan ? "Plan again" : "Plan the trip"}
        </Button>
      </div>

      {trip.is_sample && (
        <div className="mt-6 rounded-lg border border-accent-foreground/20 bg-accent px-4 py-3 text-sm text-accent-foreground">
          This is a sample trip. The six friends are made up, and each one tests something different: an expired passport,
          a wheelchair user, a hard budget limit, two people flying from the same city. Hit plan, then{" "}
          <Link href={`/t/${id}`} className="font-medium underline underline-offset-2">join as a seventh friend</Link> and plan again to see what changes.
        </div>
      )}

      {plan && (
        <section className="mt-8">
          {stale && <p className="mb-4 text-sm text-amber-700">Answers changed since this plan was made. Plan again to update it.</p>}
          <PlanResultView plan={plan} />
        </section>
      )}

      <div className="mt-10 grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2"><Users className="h-4 w-4" /> Answers</CardTitle>
          </CardHeader>
          <CardContent>
            {trip.responses.length === 0 && <p className="text-sm text-muted-foreground py-4">No answers yet. Send the link below.</p>}
            <ul className="divide-y">
              {trip.responses.map(r => (
                <li key={r.id}>
                  <details className="group py-3">
                    <summary className="flex cursor-pointer list-none items-center gap-3">
                      <ChevronDown className="h-4 w-4 text-muted-foreground transition-transform group-open:rotate-180" />
                      <span className="font-medium">{r.traveler.name}</span>
                      <span className="text-sm text-muted-foreground truncate">{r.traveler.departure_city}</span>
                      {r.is_sample && <Badge variant="secondary" className="font-normal">sample</Badge>}
                      <Button variant="ghost" size="icon" className="ml-auto text-muted-foreground" aria-label={`Remove ${r.traveler.name}`}
                        disabled={busy === r.id}
                        onClick={e => { e.preventDefault(); run(r.id, () => api.deleteResponse(id, r.id, key)); }}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </summary>
                    <div className="pl-7 pt-3"><AnswerSummary t={r.traveler} /></div>
                  </details>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-base">Share with your friends</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div className="flex gap-2">
                <input readOnly value={shareUrl(id)} className="min-w-0 flex-1 rounded-md border bg-muted px-3 text-sm font-mono" onFocus={e => e.target.select()} />
                <CopyButton text={shareUrl(id)} />
              </div>
              <Button variant="ghost" className="px-0 text-primary" asChild>
                <Link href={`/t/${id}`}>Add your own answers</Link>
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-base">Keep this page</CardTitle></CardHeader>
            <CardContent className="text-sm text-muted-foreground space-y-3">
              <p>This page's address is your organizer link. Bookmark it. Anyone with it can edit the trip.</p>
              <CopyButton text={location.href} />
            </CardContent>
          </Card>

          {!hasSample && (
            <Card>
              <CardContent className="pt-6 text-sm space-y-3">
                <p className="text-muted-foreground">Trying it out alone? Fill the trip with six made-up friends.</p>
                <Button variant="outline" disabled={busy === "sample"} onClick={() => run("sample", () => api.addSampleFriends(id, key))}>
                  <UserPlus className="h-4 w-4" /> Add sample friends
                </Button>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </Layout>
  );
}
