export type Plant = { id: number; code: string; name: string; location: string };
export type Product = {
  id: number;
  code: string;
  name: string;
  unit: string;
  inventory_value_per_m3: number;
  emergency_cost_per_m3: number;
};

export type MetaDemand = {
  id: number;
  demand_code: string;
  customer_or_project: string;
  demand_type: string;
  plant_id: number;
  product_id: number;
  required_date: string;
  requested_quantity: number;
  contribution_margin: number;
  contractual_penalty: number;
  delay_days_if_unserved: number;
  delay_cost_per_day: number;
  project_criticality: string;
  confidence_level: string;
};

export type Meta = {
  horizon: { start: string; end: string };
  plants: Plant[];
  products: Product[];
  assumptions: string[];
  samples: { ocr: string; email: string };
  demands: MetaDemand[];
  criticality_levels: string[];
  confidence_levels: string[];
};

export type DemandRow = {
  id: number;
  demand_code: string;
  demand_type: string;
  customer_or_project: string;
  customer_type: string;
  plant_name: string;
  product_name: string;
  plant_id: number;
  product_id: number;
  required_date: string;
  requested_quantity: number;
  confirmed_quantity: number;
  demand_status: string;
  confidence_level: string;
  contribution_margin: number;
  contractual_penalty: number;
  project_criticality: string;
  delay_days_if_unserved: number;
  delay_cost_per_day: number;
  source: string;
  notes: string;
};

export type AllocationLine = {
  demand_id: number;
  demand_code: string;
  demand_type: string;
  customer_or_project: string;
  customer_type: string;
  required_date: string;
  requested_quantity: number;
  allocated_quantity: number;
  unserved_quantity: number;
  confidence_level: string;
  confidence_factor: number;
  project_criticality: string;
  unit_expected_rm: number;
  unit_gross_rm: number;
  contribution_margin_rm: number;
  contractual_penalty_rm: number;
  delay_days_if_unserved: number;
  delay_cost_per_day_rm: number;
  margin_at_risk_rm: number;
  penalty_at_risk_rm: number;
  delay_cost_incurred_rm: number;
  programme_days: number;
  programme_days_avoided: number;
  consequence_avoided_rm: number;
  gross_consequence_rm: number;
  expected_consequence_rm: number;
  rank_by_expected_consequence: number;
  reason: string;
  why_not?: string;
  notes: string;
};

export type Policy = {
  policy_code: string;
  policy: string;
  method: string;
  unserved_m3: number;
  expected_consequence_rm: number;
  gross_consequence_rm: number;
  margin_at_risk_rm: number;
  programme_days: number;
  penalty_at_risk_rm: number;
  delay_cost_rm: number;
};

export type Bucket = {
  plant_id: number;
  product_id: number;
  plant_name: string;
  product_name: string;
  product_code: string;
  constrained: boolean;
  available_capacity_m3: number;
  usable_inventory_m3: number;
  available_supply_m3: number;
  total_demand_m3: number;
  shortfall_m3: number;
  internal_demand_m3: number;
  external_demand_m3: number;
  expected_consequence_rm: number;
  gross_consequence_rm: number;
  programme_days: number;
  margin_at_risk_rm: number;
  allocations: AllocationLine[];
  policies: Policy[];
  cards: {
    name: string;
    demand_type: string;
    allocated_m3: number;
    unserved_m3: number;
    requested_m3: number;
    bullets: string[];
  }[];
  inventory_projection?: {
    opening_on_hand_m3: number;
    drawn_from_inventory_m3: number;
    produced_for_allocation_m3: number;
    projected_closing_on_hand_m3: number;
    basis: string;
  };
  expedite_screen: {
    customer_or_project: string;
    unserved_quantity: number;
    emergency_cost_per_m3: number;
    emergency_cost_is_assumption: boolean;
    expedite_cost_rm: number;
    consequence_if_accepted_rm: number;
    worth_expediting: boolean;
    net_benefit_rm: number;
    comparison_basis: string;
  }[];
  decision_review?: {
    level: string;
    owner: string;
    triggers: string[];
    evidence: string;
    assumption: boolean;
  };
  explanation: {
    paragraphs: string[];
    ranking: string[];
    tradeoff: string;
    policy_comparison: string;
    expedite: string;
    maintenance: string[];
  };
  stranded_note: string;
  objective: string;
  solver: string;
};

export type AllocationResult = {
  scenario_name: string;
  scenario_notes: string[];
  totals: {
    dated_shortfall_m3: number;
    expected_consequence_rm: number;
    value_protected_vs_earliest_rm: number;
    programme_days: number;
    margin_at_risk_rm: number;
  };
  buckets: Bucket[];
  assumptions: string[];
  model?: {
    decision_variables: string[];
    objective: string;
    constraints: string[];
    solver: string;
    not_in_the_objective: string[];
    forecast_limit: string;
  };
};

export type DecisionRow = {
  id: number;
  created_at: string;
  username: string;
  plant_name: string;
  product_name: string;
  status: string;
  override_reason: string;
  consequence_recommended: number;
  consequence_final: number;
  unserved_recommended: number;
  unserved_final: number;
};
