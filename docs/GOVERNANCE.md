# Decision rights

The optimiser recommends. A person decides. There is no rule that sends the book to someone senior, and no head-office transfer price. Thresholds on this page are assumptions the parties can change, effective next cycle, after the people they affect have agreed.

A real deployment would use the company's existing identity provider, for example Microsoft 365 sign-in. The demo uses a separate secret per role in environment variables (`CDI_PASSCODE_SCHEDULER`, `CDI_PASSCODE_PLANT_SUPERVISOR`, `CDI_PASSCODE_PROJECT_PLANNER`, `CDI_PASSCODE_COMMERCIAL_OWNER`, `CDI_PASSCODE_ADMIN`). A viewer who writes receives 403. A missing or wrong passcode receives 401.

## Who does what

| Decision | Prepares | Reviews | Decides | If the review does not finish | Expedite |
| --- | --- | --- | --- | --- | --- |
| Allocation inside the review lines, matching the recommendation | Scheduler | Nobody | Scheduler, in one step | Not applicable | Plant supervisor up to the limit |
| Allocation that trips a review line, changes the recommendation, is fragile when the two plans differ, or rests on an unverified term | Scheduler | Owners of the orders whose allocation changes or ends up unserved | Those owners, both signing the same plan | At the deadline the recommendation is applied | Above the limit, project planner and commercial owner |
| A constraint on that same decision | The owner who dissents | The re-solved book, with the price shown | The constraint is a hard limit for this decision | The price stays on that owner's ledger | Not an expedite |
| Declared internal delay cost | The project planner who owns the order | Credibility factor after three realised comparisons | The effective RM/day used on the next solve | A revision waits out the cooling-off period | Not applicable |
| Review-line or expedite-limit change | Plant supervisor, project planner, or commercial owner | The other two seats | All three | The live line stays as it is | The limit itself is one of these settings |
| Demo reset | Nobody | Nobody | Admin | Not applicable | Not applicable |

The plant supervisor is consulted on an open sign-off and does not hold a veto. The plant supervisor approves emergency spend up to the expedite limit (opening assumption RM5,000). Above that, the project planner and the commercial owner both approve.

## Default rule

Sign-off stays open for 24 hours. If the first affected required date is inside 24 hours of the recommendation, the window is 2 hours. While it is open, the proposed quantities are a soft hold so the same capacity is not offered twice. The hold is not a commitment.

An hourly Vercel cron calls `GET /api/cron/governance`. The job is idempotent. `defaulted_at` is the deadline, not the time the job happened to run. If `CDI_CRON_SECRET` or `CRON_SECRET` is set, the request must send `Authorization: Bearer` that secret. Opening a page does not write.

When the required owners sign different plans and nobody has recorded a constraint, the plan with the lower expected consequence under the stated terms is the one that applies. With two seats, that rule makes the model the tie-breaker on purpose.

## Conflicts of interest

Each seat is named because the person has a stake. The scheduler wants a plan the plant can produce. The project planner wants programme days on the orders they own. The commercial owner wants penalty and margin on the orders they own. The plant supervisor wants the plant kept runnable and owns expedite spend inside the limit. Admin can restore the synthetic book and cannot set a review line.

A person does not sign a decision they prepared. A project planner signs only for orders they own. A commercial owner does the same. Signing another project's order is refused.

## Constraints

A dissent is a cap in m³, a reserve in m³, or a days limit. It names evidence: a site diary, a crew roster, or an access permit. The book is solved again. The page states the incremental cost, "honouring this costs RM X versus the recommendation", and the ledger charges that amount to the person who raised it.

At most two constraints per plant and product in a calendar month. A cap cannot exceed the smaller of 40% of that order and 80 m³. A days limit cannot exceed 14. After actuals, the record notes whether the limit bound.

## Declared delay cost

The source is a client LD clause, a holding cost, or a programme float report. Until three comparisons of realised rate to declared rate exist for that project, the credibility factor is 1. From the third comparison, later declarations are multiplied by the average of those ratios, and the factor does not rise above 1, so an inflated declaration shrinks the next one. A revision needs a reason and waits seven days. Each revision is an audit row.

## Thresholds

Opening lines, unchanged on the October book: programme days 2, expected consequence RM25,000, contractual penalty RM20,000. On the seeded book, 2 of 6 buckets trip those lines (Shah Alam G40 and Shah Alam G50). That is a minority, so the lines stay.

A proposal does not change the live value. It needs the plant supervisor, a project planner, and a commercial owner, and it takes effect on 1 Nov 2026. The reason is stored. Admin cannot edit a line.

## What is counted

Sign-offs, the median time from the plan being shown to the signature, and the acceptance rate are shown with n. Acceptance near 100% with a median under a minute, once n is at least 8, is a rubber-stamp warning. A seat that keeps missing the deadline shows up as non-response for that role, with the defaulted count beside it.
