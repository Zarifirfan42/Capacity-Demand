import { createContext, useContext, useState, type ReactNode } from "react";

const PlannerContext = createContext<{ name: string; setName: (value: string) => void }>({
  name: "A. Rahman",
  setName: () => undefined,
});

export function PlannerProvider({ children }: { children: ReactNode }) {
  const [name, setNameState] = useState(() => localStorage.getItem("cdi-planner") || "A. Rahman");
  const setName = (value: string) => {
    setNameState(value);
    localStorage.setItem("cdi-planner", value);
  };
  return <PlannerContext.Provider value={{ name, setName }}>{children}</PlannerContext.Provider>;
}

export function usePlanner() {
  return useContext(PlannerContext);
}
