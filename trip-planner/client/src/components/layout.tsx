import type { ReactNode } from "react";
import { Link } from "wouter";
import { Plane } from "lucide-react";

export function Layout({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      <header className="border-b bg-card/80 backdrop-blur sticky top-0 z-10">
        <div className={`mx-auto ${wide ? "max-w-6xl" : "max-w-3xl"} px-4 h-14 flex items-center`}>
          <Link href="/" className="flex items-center gap-2 font-display font-semibold">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-primary text-primary-foreground"><Plane className="h-4 w-4" /></span>
            Group Trip Planner
          </Link>
        </div>
      </header>
      <main className={`mx-auto w-full ${wide ? "max-w-6xl" : "max-w-3xl"} px-4 py-8 sm:py-12 flex-1`}>{children}</main>
      <footer className="border-t py-6 text-center text-xs text-muted-foreground px-4">
        Built by the owner. Trips are deleted automatically after 60 days.
      </footer>
    </div>
  );
}

export function PageError({ message }: { message: string }) {
  return (
    <Layout>
      <div className="text-center py-20">
        <h1 className="text-2xl font-semibold">{message}</h1>
        <Link href="/" className="mt-4 inline-block text-primary underline underline-offset-4">Back to the start</Link>
      </div>
    </Layout>
  );
}

export function Loading() {
  return (
    <Layout>
      <div className="py-20 text-center text-muted-foreground">Loading...</div>
    </Layout>
  );
}
