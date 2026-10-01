import { useState } from "react";
import { useLocation } from "wouter";
import { tripSchema } from "@planner";
import { Button } from "@/components/ui/button";
import { Field, TextInput } from "@/components/form";
import { Layout } from "@/components/layout";
import { api, ApiError } from "@/lib/api";
import { saved } from "@/lib/saved";

const isoToday = () => new Date().toISOString().slice(0, 10);

export default function NewTrip() {
  const [, navigate] = useLocation();
  const [f, setF] = useState({ name: "", organizer_name: "", date_from: "", date_to: "" });
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f, v: string) => setF(prev => ({ ...prev, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = tripSchema.safeParse(f);
    if (!parsed.success) {
      setErrors(Object.fromEntries(Object.entries(parsed.error.flatten().fieldErrors).map(([k, v]) => [k, v?.[0]])));
      return;
    }
    setErrors({});
    setBusy(true);
    try {
      const { id, admin_key } = await api.createTrip(parsed.data);
      saved.rememberOrganized(id, parsed.data.name, admin_key);
      navigate(`/t/${id}/organize#k=${admin_key}`);
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Could not create the trip. Try again.");
      setBusy(false);
    }
  }

  return (
    <Layout>
      <h1 className="text-3xl font-bold">Start a trip</h1>
      <p className="mt-2 text-muted-foreground">You'll get a link to send your friends. Nobody needs an account.</p>
      <form onSubmit={submit} className="mt-8 space-y-5 max-w-lg" noValidate>
        <Field label="Trip name" error={errors.name}>
          <TextInput value={f.name} onChange={e => set("name", e.target.value)} placeholder="Spring break 2027" invalid={!!errors.name} />
        </Field>
        <Field label="Your name" error={errors.organizer_name}>
          <TextInput value={f.organizer_name} onChange={e => set("organizer_name", e.target.value)} invalid={!!errors.organizer_name} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Roughly from" error={errors.date_from}>
            <TextInput type="date" min={isoToday()} value={f.date_from} onChange={e => set("date_from", e.target.value)} invalid={!!errors.date_from} />
          </Field>
          <Field label="To" error={errors.date_to}>
            <TextInput type="date" min={f.date_from || isoToday()} value={f.date_to} onChange={e => set("date_to", e.target.value)} invalid={!!errors.date_to} />
          </Field>
        </div>
        <p className="text-xs text-muted-foreground -mt-2">The window you're considering. Everyone picks the days they're free inside it.</p>
        {formError && <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{formError}</p>}
        <Button type="submit" size="lg" className="w-full h-12 text-base" disabled={busy}>{busy ? "Creating..." : "Create trip"}</Button>
      </form>
    </Layout>
  );
}
