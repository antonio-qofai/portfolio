import type { ReactNode, InputHTMLAttributes, SelectHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const control =
  "w-full h-11 rounded-md border border-input bg-card px-3 text-base sm:text-sm shadow-subtle " +
  "focus:outline-none focus:ring-2 focus:ring-ring/40 focus:border-ring disabled:opacity-50";

export function Field({ label, hint, error, children, className }: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("block space-y-1.5", className)}>
      <span className="text-sm font-medium">{label}</span>
      {children}
      {error ? <span className="block text-sm text-destructive">{error}</span> : hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

export function TextInput({ className, invalid, ...props }: InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }) {
  return <input className={cn(control, invalid && "border-destructive", className)} {...props} />;
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(control, "appearance-none bg-[length:16px] bg-[right_0.75rem_center] bg-no-repeat pr-9", className)}
      style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%236b7280' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")" }}
      {...props}>
      {children}
    </select>
  );
}

export function TextArea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(control, "h-auto min-h-[96px] py-2 resize-y", className)} {...props} />;
}

export function Chip({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cn(
        "h-9 rounded-full border px-3.5 text-sm transition-colors",
        selected ? "border-primary bg-primary text-primary-foreground" : "border-input bg-card hover:bg-muted",
      )}
    >
      {children}
    </button>
  );
}

export function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex items-start justify-between gap-4 py-1 cursor-pointer">
      <span>
        <span className="block text-sm font-medium">{label}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
      <span className="relative mt-0.5 shrink-0">
        <input type="checkbox" className="peer sr-only" checked={checked} onChange={e => onChange(e.target.checked)} />
        <span className="block h-6 w-11 rounded-full bg-secondary transition-colors peer-checked:bg-primary peer-focus-visible:ring-2 peer-focus-visible:ring-ring/40" />
        <span className="absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform peer-checked:translate-x-5" />
      </span>
    </label>
  );
}

export const label = (tag: string) => tag.replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase());
