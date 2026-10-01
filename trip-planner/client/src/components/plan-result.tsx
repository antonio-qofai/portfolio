import { AlertCircle, AlertTriangle, Bot, CalendarDays, Check, CheckCircle2, ChevronDown, CloudSun, ExternalLink, Lightbulb, ListOrdered, MapPin, Minus, Plane, Users } from "lucide-react";
import type { AgentTrace, PlanResult, ScoreBreakdown } from "@planner";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { JsonViewer } from "@/components/json-viewer";
import { label } from "@/components/form";

const usd = (n: number) => `$${Math.round(n).toLocaleString("en-US")}`;

function formatDate(iso: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric" }) {
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-US", { ...opts, timeZone: "UTC" });
}

const COMPONENT_LABELS: Record<keyof ScoreBreakdown["components"], string> = {
  interests: "Interests covered",
  fairness: "Least-served person",
  climate: "Climate match",
  budget: "Budget headroom",
  crowds: "Crowd penalty",
  dealbreakers: "Dealbreaker penalty",
};

const PENALTIES = new Set(["crowds", "dealbreakers"]);

function ScoreBars({ score }: { score: ScoreBreakdown }) {
  return (
    <div className="space-y-2">
      {(Object.keys(COMPONENT_LABELS) as (keyof ScoreBreakdown["components"])[]).map(k => {
        const v = score.components[k];
        const penalty = PENALTIES.has(k);
        if (penalty && v === 0) return null;
        return (
          <div key={k} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 text-sm">
            <span className="text-muted-foreground">{COMPONENT_LABELS[k]}</span>
            <span className="font-mono text-xs">{penalty ? `-${Math.round(Math.abs(v) * 100)}%` : `${Math.round(v * 100)}%`}</span>
            <div className="col-span-2 h-1.5 rounded-full bg-muted overflow-hidden">
              <div className={penalty ? "h-full bg-destructive" : "h-full bg-primary"} style={{ width: `${Math.abs(v) * 100}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function NoteList({ icon: Icon, title, items, tone }: { icon: typeof AlertCircle; title: string; items: string[]; tone: string }) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className={`flex items-center gap-2 text-xs font-semibold uppercase tracking-wider ${tone}`}>
        <Icon className="h-4 w-4" /> {title}
      </div>
      <ul className="mt-2 space-y-1.5 text-sm text-foreground/80">
        {items.map((t, i) => <li key={i} className="pl-6">{t}</li>)}
      </ul>
    </div>
  );
}

export function PlanResultView({ plan }: { plan: PlanResult }) {
  const { winner, trip_window, budget } = plan;

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="lg:col-span-2 space-y-6">
        {winner && trip_window ? (
          <Card className="overflow-hidden border-primary/20 shadow-elevated">
            <div className="h-1.5 bg-primary" />
            <CardHeader className="pb-4">
              <div className="text-xs font-semibold uppercase tracking-widest text-primary">The group is going to</div>
              <CardTitle className="text-3xl sm:text-4xl font-display">{winner.destination.name}</CardTitle>
              <div className="flex flex-wrap items-center gap-2 pt-1 text-sm text-muted-foreground">
                <Badge variant="secondary" className="font-normal">{winner.destination.country_or_region}</Badge>
                <span className="inline-flex items-center gap-1"><CalendarDays className="h-4 w-4" />
                  {formatDate(trip_window.start)} to {formatDate(trip_window.end)} · {trip_window.days} days</span>
                <span>· {costRange(plan)}</span>
              </div>
              {winner.weather && (
                <div className="flex items-center gap-1.5 pt-1 text-sm text-muted-foreground"><CloudSun className="h-4 w-4" /> {winner.weather.summary}</div>
              )}
            </CardHeader>
            <CardContent className="space-y-6">
              <p className="text-foreground/85 leading-relaxed">{plan.summary ?? winner.explanation}</p>
              <ol className="relative border-l-2 border-primary/20 ml-2 space-y-6">
                {plan.itinerary.map(day => (
                  <li key={day.day} className="pl-6 relative">
                    <span className="absolute -left-[9px] top-1 h-4 w-4 rounded-full border-2 border-primary bg-background" />
                    <div className="text-xs font-semibold uppercase tracking-wider text-primary">
                      Day {day.day} · {formatDate(day.date)} · {day.title}
                    </div>
                    <p className="mt-1">{day.activity}</p>
                    {day.for && day.for.length > 0 && (
                      <p className="mt-1.5 text-xs text-muted-foreground">For {day.for.join(", ")}</p>
                    )}
                  </li>
                ))}
              </ol>
              {plan.agent?.itinerary.source === "claude" && (
                <p className="text-xs text-muted-foreground">Itinerary written by Claude. Check details and opening hours before booking.</p>
              )}
            </CardContent>
          </Card>
        ) : (
          <Card className="border-destructive/40 bg-destructive/5">
            <CardContent className="py-10 text-center">
              <AlertCircle className="h-10 w-10 text-destructive mx-auto mb-3" />
              <h3 className="text-xl font-semibold">
                {plan.status === "needs_info" ? "Some answers are missing" : "No destination works for everyone yet"}
              </h3>
              <p className="text-muted-foreground max-w-md mx-auto mt-2 text-sm">
                {plan.status === "needs_info" ? "Fill in the gaps below and plan again." : "Here is what got in the way, and what would fix it."}
              </p>
            </CardContent>
          </Card>
        )}

        {winner && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center gap-2"><Users className="h-4 w-4" /> How it works for each person</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="divide-y">
                {winner.per_traveler.map(f => {
                  const c = plan.constraints?.find(x => x.name === f.name);
                  return (
                  <li key={f.name} className="py-3 flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-4">
                    <span className="font-medium w-24 shrink-0">{f.name}</span>
                    <div className="flex-1 space-y-1.5">
                    <div className="flex flex-wrap gap-1.5">
                      {f.matched_interests.map(i => (
                        <Badge key={i} className="font-normal gap-1"><Check className="h-3 w-3" />{label(i)}</Badge>
                      ))}
                      {f.unmatched_interests.map(i => (
                        <Badge key={i} variant="outline" className="font-normal gap-1 text-muted-foreground"><Minus className="h-3 w-3" />{label(i)}</Badge>
                      ))}
                    </div>
                    {c && c.summary !== "No extra notes." && (
                      <p className="text-xs text-muted-foreground">From their notes: {c.summary}</p>
                    )}
                    </div>
                    <span className={`text-xs sm:pt-1 whitespace-nowrap ${f.within_budget ? "text-muted-foreground" : "text-destructive"}`}>
                      {usd(f.cost_usd)} of {usd(f.budget_limit_usd)}
                    </span>
                  </li>
                  );
                })}
              </ul>
            </CardContent>
          </Card>
        )}

        {winner?.costs && trip_window && <CostCard costs={winner.costs} plan={plan} />}

        {plan.runner_up && (
          <Card className="border-dashed bg-muted/40">
            <CardContent className="py-5 flex gap-4">
              <MapPin className="h-5 w-5 text-muted-foreground shrink-0 mt-0.5" />
              <div>
                <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {plan.runner_up.feasible ? "Runner-up" : "Closest alternative"}
                </div>
                <div className="font-display text-lg font-semibold">{plan.runner_up.destination.name}</div>
                <p className="text-sm text-muted-foreground mt-1">{plan.runner_up.reason}</p>
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      <div className="space-y-6">
        {winner && (
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-baseline justify-between">
                <CardTitle className="text-base">Score</CardTitle>
                <span className="font-display text-2xl font-bold">{winner.score.total}<span className="text-sm text-muted-foreground font-normal"> / 100</span></span>
              </div>
            </CardHeader>
            <CardContent><ScoreBars score={winner.score} /></CardContent>
          </Card>
        )}

        <Card>
          <CardContent className="pt-6 space-y-5">
            {budget && (
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">Budget</span>
                <Badge variant={budget.state === "healthy" ? "default" : budget.state === "tight" ? "secondary" : "destructive"}>{budget.state}</Badge>
              </div>
            )}
            {plan.missing_fields && (
              <NoteList icon={AlertCircle} title="Missing answers" tone="text-destructive"
                items={Object.entries(plan.missing_fields).map(([n, fields]) => `${n}: ${fields.map(label).join(", ")}`)} />
            )}
            <NoteList icon={AlertTriangle} title="Heads up" items={plan.warnings} tone="text-amber-600" />
            <NoteList icon={AlertCircle} title="What limited the options" items={plan.limiting_factors} tone="text-muted-foreground" />
            <NoteList icon={Lightbulb} title="How to fix it" items={plan.suggested_fixes} tone="text-primary" />
            <div className="flex items-center gap-2 text-xs text-muted-foreground pt-1">
              <CheckCircle2 className={`h-4 w-4 ${plan.checks.passed ? "text-emerald-600" : "text-destructive"}`} />
              {plan.checks.passed ? "All plan checks passed" : `Failed checks: ${plan.checks.failed.join(", ")}`}
            </div>
          </CardContent>
        </Card>

        {plan.shortlist.length > 1 && <ShortlistCard plan={plan} />}

        {plan.agent && <AgentCard trace={plan.agent} />}

        <details className="group">
          <summary className="flex cursor-pointer list-none items-center gap-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" /> Raw planner output
          </summary>
          <JsonViewer data={plan} className="mt-3" />
        </details>
      </div>
    </div>
  );
}

const PRICE_TEXT: Record<AgentTrace["data"]["prices"], string> = {
  serpapi: "live flight and hotel prices",
  cache: "flight and hotel prices from the last few days",
  quota_reached: "estimated prices (this month's live-price quota is used up)",
  unavailable: "estimated prices (live prices failed)",
  skipped: "estimated prices",
};

const SOURCE_TEXT = {
  claude: "Claude",
  cache: "Claude (cached)",
  keywords: "keyword rules",
  template: "template",
} as const;

function AgentCard({ trace }: { trace: AgentTrace }) {
  const attempts = trace.itinerary.attempts;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2"><Bot className="h-4 w-4" /> How the agent got here</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <ol className="space-y-2">
          <li><span className="text-muted-foreground">1. Notes read by</span> {SOURCE_TEXT[trace.constraints.source]}
            {trace.constraints.error && <span className="block text-xs text-muted-foreground">Claude failed ({trace.constraints.error}), so keyword rules were used.</span>}
          </li>
          <li><span className="text-muted-foreground">2. Destination and dates by</span> the deterministic planner, using
            {" "}{trace.data.travel_time === "estimated" ? "estimated flight times" : "no flight times"},
            {" "}{trace.data.weather === "open-meteo" ? "real weather for the dates" : "static climate tags"}, and
            {" "}{PRICE_TEXT[trace.data.prices]}
            {trace.data.destinations_price_checked.length > 1 && (
              <span className="block text-xs text-muted-foreground">
                Prices checked for {trace.data.destinations_price_checked.length} destinations: the first no longer fit someone's budget.
              </span>
            )}
            {trace.data.errors.length > 0 && <span className="block text-xs text-muted-foreground">Problems: {trace.data.errors.join("; ")}</span>}
          </li>
          <li><span className="text-muted-foreground">3. Itinerary by</span> {SOURCE_TEXT[trace.itinerary.source]}
            {attempts.length > 0 && (
              <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                {attempts.map((a, i) => (
                  <li key={i}>
                    Draft {i + 1}: {a.error ? `error (${a.error})` : a.failed_checks.length ? `failed ${a.failed_checks.map(f => f.replace(/_/g, " ")).join(", ")}` : "passed every check"}
                  </li>
                ))}
              </ul>
            )}
          </li>
        </ol>
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t pt-3 text-xs text-muted-foreground font-mono">
          <span>{trace.model}</span>
          <span>{(trace.input_tokens + trace.output_tokens).toLocaleString("en-US")} tokens</span>
          {trace.data.price_searches > 0 && <span>{trace.data.price_searches} price searches</span>}
          <span>${trace.cost_usd.toFixed(3)}</span>
          <span>{(trace.duration_ms / 1000).toFixed(1)}s</span>
        </div>
      </CardContent>
    </Card>
  );
}

function costRange(plan: PlanResult): string {
  const w = plan.winner!;
  if (!w.costs) return `about ${usd(w.destination.estimated_cost_per_person_usd)} per person (estimate)`;
  const totals = w.costs.per_traveler.map(c => c.total_usd);
  const [lo, hi] = [Math.min(...totals), Math.max(...totals)];
  return lo === hi ? `${usd(lo)} per person` : `${usd(lo)} to ${usd(hi)} per person`;
}

const hm = (minutes: number) => `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;

function CostCard({ costs, plan }: { costs: NonNullable<NonNullable<PlanResult["winner"]>["costs"]>; plan: PlanResult }) {
  const days = plan.trip_window!.days;
  const checked = new Date(costs.checked_at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2"><Plane className="h-4 w-4" /> What it costs each person</CardTitle>
        <p className="text-xs text-muted-foreground">
          Live Google Flights and Google Hotels prices, checked {checked}.
          {costs.hotel && <> Lodging is half a room at {costs.hotel.name} (${costs.hotel.nightly_usd}/night).</>}
          {" "}Daily spending is an assumed ${Math.round(plan.winner!.costs!.per_traveler[0].daily_usd / days)} per day.
        </p>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto -mx-6 px-6">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th className="font-normal pb-2">Who</th>
                <th className="font-normal pb-2">Flight</th>
                <th className="font-normal pb-2 text-right">Lodging</th>
                <th className="font-normal pb-2 text-right">Total</th>
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody className="divide-y">
              {costs.per_traveler.map(c => (
                <tr key={c.name}>
                  <td className="py-2 pr-3 font-medium whitespace-nowrap">{c.name}</td>
                  <td className="py-2 pr-3 whitespace-nowrap">
                    {c.flight_usd !== null ? (
                      <>
                        {usd(c.flight_usd)} <span className="text-muted-foreground">from {c.origin}</span>
                        <span className="block text-xs text-muted-foreground">
                          {c.flight_minutes !== null && hm(c.flight_minutes)} · {c.stops === 0 ? "nonstop" : `${c.stops} stop${c.stops === 1 ? "" : "s"}`}
                          {c.nonstop_required && " (asked for nonstop)"}
                        </span>
                      </>
                    ) : (
                      <span className="text-muted-foreground">Estimate{c.nonstop_required ? ", no nonstop found" : ""}</span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-right">{c.source === "live" ? usd(c.lodging_usd) : "-"}</td>
                  <td className="py-2 pr-3 text-right font-medium">{usd(c.total_usd)}</td>
                  <td className="py-2 text-right">
                    <a href={c.booking_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline whitespace-nowrap">
                      Flights <ExternalLink className="h-3 w-3" />
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {costs.hotel?.url && (
          <a href={costs.hotel.url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-xs text-primary hover:underline">
            See hotels for these dates <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </CardContent>
    </Card>
  );
}

function ShortlistCard({ plan }: { plan: PlanResult }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2"><ListOrdered className="h-4 w-4" /> Top options considered</CardTitle>
      </CardHeader>
      <CardContent>
        <ol className="space-y-2 text-sm">
          {plan.shortlist.map((s, i) => (
            <li key={s.destination.id} className="flex items-baseline gap-2">
              <span className="w-4 text-muted-foreground">{i + 1}.</span>
              <span className="flex-1">
                <span className={i === 0 ? "font-medium" : ""}>{s.destination.name}</span>
                {s.weather && <span className="block text-xs text-muted-foreground">{s.weather.summary.replace(/^(Typically|Forecast): /, "")}</span>}
              </span>
              <span className="font-mono text-xs text-muted-foreground">{s.score.total}</span>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
