import type { PlanResult } from "@planner";
import { cn } from "@/lib/utils";
import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "@/hooks/use-toast";

interface JsonViewerProps {
  data: PlanResult | null;
  className?: string;
}

export function JsonViewer({ data, className }: JsonViewerProps) {
  const [copied, setCopied] = useState(false);

  if (!data) return null;

  const handleCopy = () => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    setCopied(true);
    toast({ description: "JSON copied to clipboard" });
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={cn("rounded-lg border bg-slate-950 text-slate-50 overflow-hidden font-mono text-xs shadow-lg", className)}>
      <div className="flex items-center justify-between px-4 py-2 bg-slate-900 border-b border-slate-800">
        <span className="font-semibold text-slate-400">Backend Response (JSON)</span>
        <Button variant="ghost" size="icon" onClick={handleCopy} className="h-6 w-6 text-slate-400 hover:text-white">
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
        </Button>
      </div>
      <ScrollArea className="h-[400px] w-full p-4">
        <pre className="text-emerald-400">
          {JSON.stringify(data, null, 2)}
        </pre>
      </ScrollArea>
    </div>
  );
}
