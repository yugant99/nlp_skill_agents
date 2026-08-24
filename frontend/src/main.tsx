import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const isProfessorDemo = window.location.pathname.startsWith("/professor-demo");

async function renderRoot() {
  const RootComponent = isProfessorDemo
    ? (await import("./ProfessorDemoApp")).ProfessorDemoApp
    : (await import("./App")).App;

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <RootComponent />
    </StrictMode>
  );
}

void renderRoot();
