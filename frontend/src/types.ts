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
  penalty_type?: string;
  delay_type?: string;
  lump_sum_trigger?: string;
  penalty_type_unverified?: number;
  types_unverified?: number;
  delay_type_unverified?: number;
  minimum_useful_delivery_m3?: number;
};

export type AllocationLine = {
  demand_id: number;
  demand_code: string;
  demand_type: string;
  customer_or_project: string;
  customer_type: string;
  required_date: string;
  requested_quantity: number;
  confirmed_quantity?: number;
  tranche_note?: string;
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
  partial_service_no_penalty_avoided?: boolean;
  minimum_useful_delivery_m3?: number;
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
  stockable?: boolean;
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
    demand_id?: number;
    customer_or_project: string;
    unserved_quantity: number;
    close_m3?: number;
    emergency_cost_per_m3: number;
    emergency_cost_is_assumption: boolean;
    expedite_cost_rm: number;
    penalty_and_delay_avoided_rm?: number;
    avoided_per_rm?: number;
    worth_expediting: boolean;
    net_benefit_rm: number;
    comparison_basis: string;
    steps_closed?: string[];
  }[];
  expedite_proposal?: {
    extra_m3: number;
    cost_rm: number;
    avoids_rm: number;
    net_benefit_rm: number;
    recommended: boolean;
    needs_approval: boolean;
    note: string;
  };
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
    tie_break?: string;
    other_close_calls?: string;
  };
  stranded_note: string;
  objective: string;
  solver: string;
  plant_mode?: "shadow" | "pilot";
  open_decision?: {
    id: number;
    username: string;
    created_at: string;
    status: string;
    locked: boolean;
    lines: { demand_id: number; customer_or_project: string; committed_m3: number; recommended_m3: number }[];
  } | null;
  objective_epsilon_rm?: number;
  recommendation_basis?: string;
  comparison?: {
    typed_plan_true_rm: number;
    proportional_plan_true_rm?: number;
    proportional_regret_under_true_terms_rm?: number;
    typed_plan_if_scored_proportional_rm?: number;
    proportional_plan_if_scored_proportional_rm?: number;
    plans_differ?: boolean;
    note: string;
    typed_allocations?: { demand_id: number; customer_or_project: string; allocated_m3: number; unserved_m3: number }[];
    proportional_allocations?: { demand_id: number; customer_or_project: string; allocated_m3: number; unserved_m3: number }[];
  };
  unverified_minimax?: {
    orders: string[];
    cells_rm: Record<string, number>;
    max_regret_linear_plan_rm: number;
    max_regret_lump_plan_rm: number;
    chosen: string;
    note: string;
  } | null;
  party_burden?: {
    before: { internal: PartySlice; external: PartySlice } | null;
    after: { internal: PartySlice; external: PartySlice };
    note: string;
  };
  headline_gap?: {
    true_rm: number;
    best_rule?: string;
    best_rule_label?: string;
    if_scored_proportional_rm: number;
    effect_rm: number;
    note: string;
  };
};

export type PartySlice = {
  unserved_m3: number;
  consequence_rm: number;
  unserved_share: number;
  consequence_share: number;
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
