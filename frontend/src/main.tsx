import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

async function renderRoot() {
  const pathname = window.location.pathname;
  const RootComponent = pathname.startsWith("/transcript-pilot")
    ? (await import("./TranscriptPilotApp")).TranscriptPilotApp
    : pathname.startsWith("/professor-demo")
      ? (await import("./ProfessorDemoApp")).ProfessorDemoApp
      : (await import("./App")).App;

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <RootComponent />
    </StrictMode>
  );
}

void renderRoot();
