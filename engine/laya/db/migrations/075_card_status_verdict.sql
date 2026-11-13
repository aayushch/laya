-- Columns for the status that the stager LLM assigns to a card.
--
-- The stager says what each event does to its work item (the pull request,
-- ticket, thread, meeting or page the event belongs to). The emit step uses
-- that answer to create the card as `ready` or `done` and to close earlier
-- cards of the same entity. See laya/pipeline/card_status.py.
--
-- status_effect: the stager's answer for this card's event. One of `none`,
--   `concludes`, `reopens`, `standalone_notice`. NULL when the stager gave no
--   usable answer. The state of a work item at a point in time is read from
--   the most recent `concludes` or `reopens` row of its entity.
-- status_reason: one sentence saying why Laya set this card to `done`. NULL on
--   cards Laya did not close.
-- status_set_by: `laya` when Laya set the card to `done`. `user` when the user
--   reopened a card that Laya had closed; Laya never closes such a card again.
--   NULL in every other case.
--
-- All three are nullable and existing rows are left as NULL. Cards created
-- before this migration have no recorded answer, and the code treats a work
-- item with no recorded answer as open.
ALTER TABLE action_cards ADD COLUMN status_effect TEXT;
ALTER TABLE action_cards ADD COLUMN status_reason TEXT;
ALTER TABLE action_cards ADD COLUMN status_set_by TEXT;
