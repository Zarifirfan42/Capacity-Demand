# Enhancement gap analysis

Status after the incremental pass. “Current implementation” is what the code does now. “Remaining gap” is what a real deployment would still have to close. Numbers in the app are synthetic or assumed.

| Mission requirement | Current implementation | Remaining gap | Proposed next step | Priority |
| --- | --- | --- | --- | --- |
| Capacity allocation | CBC linear programme per plant and product. Objective is expected margin, penalty, and programme-delay cost of unserved quantity. No internal or external preference. | Approval does not reserve the capacity calendar for the next solve. | Write the approved quantities back as planned production when the plant agrees that is the operating rule. | High |
| Demand planning | Twelve months of patterned synthetic history. Three-month moving average, seasonal rescale for October, planner adjustment stored but not shown as a form. Weekly table is the October order book, not the statistical forecast added in. | No planner-adjustment control in the page. History is monthly, not weekly. | Add the adjustment form. Keep the forecast out of the solver. | Medium |
| Internal / external demand | One demand book. Type is a label. Programme days count only for internal lines. | No live project pipeline. Names are demo labels. | Load both streams from the same spreadsheet export. | High |
| Current arrangement cost | Current-practice proxy on the same supply: firm orders, then due date, then margin and penalty. Programme delay is ignored. Earliest-date, internal-first, and external-first stay as named rules. On the hero book the proxy expected consequence is RM95,695 and the programme is RM66,095. | The proxy is an illustration. There is no observed allocation history. | Replace the proxy only when real past allocations exist. Until then, keep the label. | High |
| Decision governance | Scheduler inside assumed lines. Supervisor with the affected party at 2 programme days, RM25,000 expected consequence, or RM20,000 penalty. Cross-business owner when the same plant-product has an internal delay and an external penalty. | Thresholds are assumptions. A multi-plant conflict is not a separate trigger. | Put the three lines in a settings table the plant can change. | Medium |
| AI | Regex extraction with a field-coverage confidence. A person confirms before the line joins the book. | Not OCR. Not a document model. Low-confidence lines can still be saved if a person confirms them. | Keep extraction as intake. Do not let it allocate. | Low |
| Human-in-loop | Approve or override. Modified decisions need a reason category, a reason, a name, and a time. The override must be date-feasible. | The next solve still ignores the recorded decision. | Show the open decision on the next recommendation so the planner sees they are replacing it. | High |
| Business impact | Same-environment columns: practice proxy, earliest date, optimised, approved. Internal-first and external-first sit beside them. | “Actual” is recorded per decision and is not yet a fifth impact column. | Add the actual column when a decision has an actual quantity. | Medium |
| Working-capital analysis | Inventory value = on-hand × assumed unit cost. Carrying cost = value × 8% × 30/365. | The 8% rate and the unit costs are assumptions. Excess uses a fixed 14-day window. | Replace both with the plant’s carrying rate and a stated cover policy. | Medium |
| Programme-delay analysis | Unserved fraction × delay days × cost per day, internal lines only. | Delay days are an input, not a measured site outcome. | Record actual programme days against the order after delivery. | High |
| Actual vs recommendation | Decision row stores recommended, approved, and optional actual quantities, with variances. | Actuals do not feed the impact comparison or the capacity calendar. | Use recorded actuals in the impact column once they exist. | Medium |

## Fairness of the policy comparison

Every policy in one solve reads the same demand rows, the same dated capacity, the same usable inventory, and the same financial fields. Only the rule changes. The practice proxy is greedy. It is not a second linear programme.

## Where AI stays out

The allocator is CBC. The forecast is a moving average. Neither is a language model. Extraction structures a draft and stops.
