import { Route, Switch } from "wouter";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import Landing from "@/pages/landing";
import NewTrip from "@/pages/new-trip";
import TripPage from "@/pages/trip";
import OrganizePage from "@/pages/organize";
import NotFound from "@/pages/not-found";

export default function App() {
  return (
    <TooltipProvider>
      <Toaster />
      <Switch>
        <Route path="/" component={Landing} />
        <Route path="/new" component={NewTrip} />
        <Route path="/t/:id/organize">{params => <OrganizePage id={params.id} />}</Route>
        <Route path="/t/:id">{params => <TripPage id={params.id} />}</Route>
        <Route component={NotFound} />
      </Switch>
    </TooltipProvider>
  );
}
