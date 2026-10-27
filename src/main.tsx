import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "@tanstack/react-router";
import { getRouter } from "./router";

const redirect = sessionStorage.getItem("partscope-redirect");
if (redirect) {
  sessionStorage.removeItem("partscope-redirect");
  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  const target = redirect.startsWith(base + "/") || redirect === base
    ? redirect
    : base + (redirect.startsWith("/") ? redirect : "/" + redirect);
  history.replaceState(null, "", target);
}

const router = getRouter();
const root = document.getElementById("root");
if (!root) throw new Error("Не найден элемент #root");

createRoot(root).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
