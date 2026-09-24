import { createContext, useContext, useState, type ReactNode } from "react";

const PlannerContext = createContext<{
  name: string;
  setName: (value: string) => void;
  role: string;
  setRole: (value: string) => void;
  passcode: string;
  setPasscode: (value: string) => void;
}>({
  name: "A. Rahman",
  setName: () => undefined,
  role: "viewer",
  setRole: () => undefined,
  passcode: "",
  setPasscode: () => undefined,
});

export function PlannerProvider({ children }: { children: ReactNode }) {
  const [name, setNameState] = useState(() => localStorage.getItem("cdi-planner") || "A. Rahman");
  const [role, setRoleState] = useState(() => sessionStorage.getItem("cdi-role") || "viewer");
  const [passcode, setPasscodeState] = useState(() => sessionStorage.getItem("cdi-passcode") || "");
  const setName = (value: string) => {
    setNameState(value);
    localStorage.setItem("cdi-planner", value);
  };
  const setRole = (value: string) => {
    setRoleState(value);
    sessionStorage.setItem("cdi-role", value);
  };
  const setPasscode = (value: string) => {
    setPasscodeState(value);
    sessionStorage.setItem("cdi-passcode", value);
  };
  return <PlannerContext.Provider value={{ name, setName, role, passcode, setRole, setPasscode }}>{children}</PlannerContext.Provider>;
}

export function usePlanner() {
  return useContext(PlannerContext);
}
