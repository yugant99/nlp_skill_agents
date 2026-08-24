import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { ProfessorDemoApp } from "./ProfessorDemoApp";
import "./styles.css";

const isProfessorDemo = window.location.pathname.startsWith("/professor-demo");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {isProfessorDemo ? <ProfessorDemoApp /> : <App />}
  </StrictMode>
);
