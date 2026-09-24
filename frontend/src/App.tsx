import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./Layout";
import { AllocationPage } from "./pages/Allocation";
import { CapacityPage } from "./pages/Capacity";
import { ControlTowerPage } from "./pages/ControlTower";
import { DemandPage } from "./pages/Demand";
import { ImpactPage } from "./pages/Impact";
import { MeasurementPage } from "./pages/Measurement";
import { ScenarioPage } from "./pages/Scenarios";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<ControlTowerPage />} />
        <Route path="/demand" element={<DemandPage />} />
        <Route path="/capacity" element={<CapacityPage />} />
        <Route path="/allocation" element={<AllocationPage />} />
        <Route path="/scenarios" element={<ScenarioPage />} />
        <Route path="/impact" element={<ImpactPage />} />
        <Route path="/measurement" element={<MeasurementPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
