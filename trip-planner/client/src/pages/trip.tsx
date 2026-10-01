// The share link. Friends land here to add or edit their answers, and come
// back to see the plan once the organizer runs it.

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Pencil } from "lucide-react";
import type { Traveler } from "@planner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Layout, Loading, PageError } from "@/components/layout";
import { PlanResultView } from "@/components/plan-result";
import { TravelerForm } from "@/components/traveler-form";
import { api, ApiError, type PublicTrip } from "@/lib/api";
import { saved } from "@/lib/saved";

export function formatRange(from: string, to: string) {
  const f = (d: string) => new Date(`${d}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  return `${f(from)} to ${f(to)}`;
}

export default function TripPage({ id }: { id: string }) {
  const [trip, setTrip] = useState<PublicTrip | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mine, setMine] = useState<Traveler | null>(null);
  const [editing, setEditing] = useState(false);
  const own = saved.response(id);

  const load = useCallback(async () => {
    try {
      setTrip(await api.getTrip(id));
      if (own) setMine((await api.getOwnResponse(id, own.id, own.edit_token)).traveler);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 404 ? "This trip doesn't exist or has expired." : "Could not load this trip.");
    }
  }, [id, own?.id]);

  useEffect(() => { load(); }, [load]);

  if (error) return <PageError message={error} />;
  if (!trip) return <Loading />;

  const window = { from: trip.date_from, to: trip.date_to };
  const showForm = !own || editing;

  return (
    <Layout wide={!!trip.plan}>
      <div className="max-w-3xl">
        {trip.is_sample && <Badge variant="secondary" className="mb-3">Sample trip</Badge>}
        <h1 className="text-3xl sm:text-4xl font-bold">{trip.name}</h1>
        <p className="mt-2 text-muted-foreground">
          {trip.organizer_name} is planning a trip sometime {formatRange(trip.date_from, trip.date_to)}.
        </p>
        <div className="mt-4 flex flex-wrap gap-1.5">
          {trip.responses.map(r => <Badge key={r.id} variant="outline" className="font-normal">{r.name}</Badge>)}
          {trip.responses.length === 0 && <span className="text-sm text-muted-foreground">Nobody has answered yet.</span>}
        </div>
      </div>

      {trip.plan && (
        <section className="mt-10">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-4">The plan so far</h2>
          <PlanResultView plan={trip.plan} />
        </section>
      )}

      <section className="mt-10 max-w-3xl">
        {showForm ? (
          <div className="rounded-xl border bg-card p-5 sm:p-8 shadow-card">
            <h2 className="text-xl font-semibold">{own ? "Edit your answers" : "Add your answers"}</h2>
            <p className="mt-1 mb-6 text-sm text-muted-foreground">Takes about two minutes. {trip.organizer_name} sees your full answers, and the plan may mention whose budget is tightest.</p>
            <TravelerForm
              key={mine?.name ?? "new"}
              initial={mine ?? undefined}
              window={window}
              submitLabel={own ? "Save changes" : "Count me in"}
              onSubmit={async answers => {
                if (own) await api.updateResponse(id, own.id, answers, { token: own.edit_token });
                else {
                  const r = await api.submitResponse(id, answers);
                  saved.rememberResponse(id, r.id, r.edit_token);
                }
                setEditing(false);
                await load();
              }}
            />
          </div>
        ) : (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 rounded-xl border bg-card p-5">
            <div className="flex items-center gap-3">
              <CheckCircle2 className="h-6 w-6 text-emerald-600" />
              <div>
                <div className="font-medium">You're in{mine ? `, ${mine.name}` : ""}.</div>
                <div className="text-sm text-muted-foreground">{trip.organizer_name} runs the planner. Check back here for the latest plan.</div>
              </div>
            </div>
            <Button variant="outline" onClick={() => setEditing(true)}><Pencil className="h-4 w-4" /> Edit answers</Button>
          </div>
        )}
      </section>
    </Layout>
  );
}
