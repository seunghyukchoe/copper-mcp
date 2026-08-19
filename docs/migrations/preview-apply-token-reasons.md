# Preview response versions for withheld apply-token reasons

Route and layered-route preview responses move from `1.0` to `1.1`. Placement preview responses
move from `0.1.0` to `0.2.0`; placement candidates remain `0.1.0`, so their canonical identity and
apply binding do not change.

Every new preview response contains both `apply_token` and `apply_token_withheld_reason` keys and
sets exactly one value. A caller must branch as follows:

- a non-null `apply_token` is the existing separately authorized, revision- and candidate-bound
  capability; `apply_token_withheld_reason` is null;
- a null `apply_token` has one fixed reason: `unsupported_surface`, `not_requested`,
  `apply_disabled`, `no_candidate`, `no_move`, `board_not_appliable`, `fill_bound_candidate`, or
  `replay_refused`.

Do not infer or insert a reason into a stored `1.0` route/layered response or `0.1.0` placement
preview. Re-run the preview against the original board revision and request if the reason is needed.
The version move does not migrate, refresh, or reauthorize an old token. Candidate versions,
candidate IDs, single-use token semantics, apply flags, and revision checks are unchanged.
