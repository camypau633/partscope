import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";
import "../styles.css";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: RootComponent,
  notFoundComponent: () => (
    <main className="flex min-h-dvh items-center justify-center p-6 text-center">
      <div><h1 className="text-5xl font-bold">404</h1><p className="mt-3 text-muted-foreground">Страница не найдена</p></div>
    </main>
  ),
});

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  return <QueryClientProvider client={queryClient}><Outlet /></QueryClientProvider>;
}
