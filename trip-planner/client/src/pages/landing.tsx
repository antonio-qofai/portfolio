import { useState } from "react";
import { Link, useLocation } from "wouter";
import { ArrowRight, CalendarCheck, Link2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Layout } from "@/components/layout";
import { api } from "@/lib/api";
import { saved } from "@/lib/saved";
import { toast } from "@/hooks/use-toast";

const STEPS = [
  { icon: Link2, title: "Send one link", body: "Friends open it on their phone and answer a short form: dates, budget, home airport, what they want to do." },
  { icon: CalendarCheck, title: "Find what works", body: "The planner finds the days everyone is free, rules out anything someone can't do or afford, and ranks the rest." },
  { icon: Sparkles, title: "Get the plan", body: "One destination, a runner-up, and a day-by-day itinerary, with a plain explanation of the tradeoffs." },
];

export default function Landing() {
  const [, navigate] = useLocation();
  const [busy, setBusy] = useState(false);
  const organized = Object.entries(saved.organized()).sort((a, b) => b[1].created.localeCompare(a[1].created));

  async function trySample() {
    setBusy(true);
    try {
      const { id, admin_key } = await api.createSampleTrip();
      saved.rememberOrganized(id, "Sample trip", admin_key);
      navigate(`/t/${id}/organize#k=${admin_key}`);
    } catch (e) {
      toast({ variant: "destructive", description: e instanceof Error ? e.message : "Could not create the sample trip." });
      setBusy(false);
    }
  }

  return (
    <Layout>
      <section className="text-center pt-6 sm:pt-12">
        <h1 className="text-4xl sm:text-6xl font-bold leading-[1.05]">
          Plan a group trip everyone can actually go on.
        </h1>
        <p className="mx-auto mt-5 max-w-xl text-lg text-muted-foreground">
          Everyone says when they're free, what they can spend, and what they want to do. The planner finds the dates,
          the destination that works for the most people, and a day-by-day plan.
        </p>
        <div className="mt-8 flex flex-col sm:flex-row justify-center gap-3">
          <Button size="lg" className="h-12 px-6 text-base" asChild>
            <Link href="/new">Plan a trip <ArrowRight className="h-4 w-4" /></Link>
          </Button>
          <Button size="lg" variant="outline" className="h-12 px-6 text-base" onClick={trySample} disabled={busy}>
            {busy ? "Setting it up..." : "Try a sample trip"}
          </Button>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">The sample comes with six made-up friends who have already answered.</p>
      </section>

      <section className="mt-16 sm:mt-24 grid gap-6 sm:grid-cols-3">
        {STEPS.map(({ icon: Icon, title, body }, i) => (
          <div key={title} className="feature-card rounded-xl p-5">
            <div className="flex items-center gap-2 text-primary">
              <Icon className="h-5 w-5" />
              <span className="text-xs font-semibold uppercase tracking-wider">Step {i + 1}</span>
            </div>
            <h2 className="mt-3 text-lg font-semibold">{title}</h2>
            <p className="mt-1 text-sm text-muted-foreground leading-relaxed">{body}</p>
          </div>
        ))}
      </section>

      {organized.length > 0 && (
        <section className="mt-16">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">Trips you organize</h2>
          <ul className="mt-3 divide-y rounded-lg border bg-card">
            {organized.map(([id, t]) => (
              <li key={id}>
                <Link href={`/t/${id}/organize#k=${t.admin_key}`} className="flex items-center justify-between px-4 py-3 hover:bg-muted/50">
                  <span>{t.name}</span>
                  <ArrowRight className="h-4 w-4 text-muted-foreground" />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Layout>
  );
}
