# Intake

A message can become a demand line. A person confirms it. The allocation is still a linear programme with a human decision. A model does not choose who gets capacity.

## What runs

The seat that confirms a line is the scheduler. That is the intake confirm role in this prototype.

`CDI_INTAKE_MODE` chooses the path:

- `regex` always uses the rules.
- `llm` calls Anthropic only when `ANTHROPIC_API_KEY` is set. With no key, the screen shows mock mode.
- unset uses the model when a key exists, and mock otherwise.

Mock mode shows a persistent banner: `MOCK: canned responses, no model called`. A canned reply that does not ground falls through to the rules. The banner stays, because no model was called.

The default model name is `claude-haiku-4-5-20251001`. Override it with `CDI_INTAKE_MODEL`.

Set `CDI_INTAKE_MODE=llm` only on a deployment that has a key and the caps below. Do not put the key in the repository.

## What the model is not allowed to fill

Margin, contractual penalty, and delay cost are typed by a person. They are not in the reply schema. A reply that includes them is rejected and the rules fill the draft instead. The same three numbers are left blank on the rules path.

## Grounding

Each extracted value needs a span that appears verbatim in the message. If the span is missing, the field is cleared and the screen says so. Quantity has to be positive. Anything above 2,000 m³ is kept visible and blocked until the scheduler accepts it. A date outside 1–30 Oct 2026 is cleared. Plant and product have to match the seeded works and mixes. Relative dates such as “next Mon” or “Isnin depan” are resolved from 24 Sep 2026 and then checked against that horizon.

Model confidence is stored and is not treated as a probability. The 0.7 cut-off was checked against the regex score on the eval set. The committed file records how often a case at or above 0.7 was fully correct. A live model threshold was not calibrated, because the model was not run.

The confirm screen shows the source text with those spans highlighted beside the fields.

## Change and cancel

`change` and `cancel` are matched to an existing line. The screen shows the diff, including `cancel EXT-…` or the old quantity and the new one. The scheduler confirms it, and the event is logged. A cancellation sets that line to Cancelled and quantity 0. It does not insert a new line. A new-order duplicate is flagged and is not merged.

## Caps

The extract route requires the scheduler passcode. A viewer receives 403.

- Daily call cap: `CDI_INTAKE_DAILY_CAP`, default 40. This is the spend control. A dollar rate is not copied into this repo.
- Hourly cap per role and client address: `CDI_INTAKE_HOURLY_CAP`, default 10.
- Messages longer than 4,000 characters are cut.
- The call times out at 20 seconds and is tried once more.
- When a cap is hit, or the reply is unusable, the draft comes from the rules (or from the canned reply when that is the mode) and the warning says so.

## What leaves the machine

Phone numbers and email addresses are removed before a model call and shown again only on this machine. They can be stored on the demand note when the scheduler saves. Customer names are included in a model call when the mode is `llm`.

Read the provider’s current terms before a real deployment. This note does not restate them:

- How long organisation data is stored: https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data
- API and data retention: https://platform.claude.com/docs/en/manage-claude/api-and-data-retention
- Zero data retention, and which products it covers: https://privacy.claude.com/en/articles/8956058-i-have-a-zero-data-retention-agreement-with-anthropic-what-products-does-it-apply-to
- Current token prices: https://platform.claude.com/docs/en/about-claude/pricing

A real deployment should review those pages and Malaysia’s Personal Data Protection Act 2010 before customer text is sent. This prototype does not give that legal review.

## Measurement

The Measurement page shows, each with n:

- the share of proposed fields the scheduler changes, for model confirmations
- median seconds to confirm, model path and manual path
- order-to-book hours, from the received time on the form to the time the line is in the book
- the share of orders booked within `CDI_INTAKE_LATE_DAYS` of the required date (default 3), or after it

The log stores a hash of the message, not the message.

Expand model intake only when n is at least 20 and fewer than 30% of proposed fields are corrected. Below 20, show n and do not treat the rate as a decision.

## Eval file

`backend/app/intake_eval.json` holds 14 synthetic messages and 3 synthetic images (a typed delivery order, a skewed photo, a handwritten-style note).

`backend/app/intake_eval_results.json` is the evidence CI reads. It does not call the API. The regex section is a measured score, including failures. The model section is `not_run` because `ANTHROPIC_API_KEY` was absent when the file was written. Vision is `not_run` for the same reason, and because `CDI_INTAKE_VISION` is off. If a later run with a key does not beat the regex score, write that in the file. That result is still the evidence.

Photo reading stays behind `CDI_INTAKE_VISION=1` and only when the mode is `llm`. Image bytes are not stored. Mock mode does not send them.

## Where this helps

| Use | Judgement |
| --- | --- |
| Read a message into a line a person confirms | Use now |
| Forecasting with a learned model | Premature |
| A model chooses the allocation | Never |
| Anomaly flags | Later |
| Reading a contract clause for a penalty | A later pilot, with a legal review. Not in this build. |
| A photo of a paper order | Behind the vision flag. Not run on the three synthetic images. |
