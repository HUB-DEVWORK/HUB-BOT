import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createHashRouter, Navigate, RouterProvider } from "react-router-dom";

import Layout from "./components/Layout";
import { getToken } from "./api/client";
import { AppProvider } from "./state/app";
import "./theme.css";

import AiSupport from "./screens/AiSupport";
import Blacklist from "./screens/Blacklist";
import Broadcasts from "./screens/Broadcasts";
import BotButtons from "./screens/BotButtons";
import BotImages from "./screens/BotImages";
import Campaigns from "./screens/Campaigns";
import Dashboard from "./screens/Dashboard";
import Login from "./screens/Login";
import Maintenance from "./screens/Maintenance";
import Miniapp from "./screens/Miniapp";
import Notifications from "./screens/Notifications";
import Partners from "./screens/Partners";
import Payments from "./screens/Payments";
import Promogroups from "./screens/Promogroups";
import Promos from "./screens/Promos";
import Reminders from "./screens/Reminders";
import Sales from "./screens/Sales";
import Servers from "./screens/Servers";
import Settings from "./screens/Settings";
import Smart from "./screens/Smart";
import Tariffs from "./screens/Tariffs";
import Tickets from "./screens/Tickets";
import Users from "./screens/Users";

function Guard({ children }: { children: React.ReactElement }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  return children;
}

const router = createHashRouter([
  { path: "/login", element: <Login /> },
  {
    path: "/",
    element: (
      <Guard>
        <Layout />
      </Guard>
    ),
    children: [
      { index: true, element: <Dashboard /> },
      { path: "users", element: <Users /> },
      { path: "tariffs", element: <Tariffs /> },
      { path: "promos", element: <Promos /> },
      { path: "bot-buttons", element: <BotButtons /> },
      { path: "bot-images", element: <BotImages /> },
      { path: "miniapp", element: <Miniapp /> },
      { path: "broadcasts", element: <Broadcasts /> },
      { path: "smart", element: <Smart /> },
      { path: "notifications", element: <Notifications /> },
      { path: "reminders", element: <Reminders /> },
      { path: "campaigns", element: <Campaigns /> },
      { path: "partners", element: <Partners /> },
      { path: "sales", element: <Sales /> },
      { path: "promo-groups", element: <Promogroups /> },
      { path: "payments", element: <Payments /> },
      { path: "tickets", element: <Tickets /> },
      { path: "ai-support", element: <AiSupport /> },
      { path: "blacklist", element: <Blacklist /> },
      { path: "servers", element: <Servers /> },
      { path: "settings", element: <Settings /> },
      { path: "maintenance", element: <Maintenance /> },
    ],
  },
]);

const qc = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <AppProvider>
        <RouterProvider router={router} />
      </AppProvider>
    </QueryClientProvider>
  </StrictMode>,
);
