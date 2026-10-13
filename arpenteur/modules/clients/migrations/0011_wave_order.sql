-- The order of a wave's steps, held by the schema without comparing clocks.
--
-- 0010 required `tested_at >= rendered_at` and `approved_at >= tested_at`.
-- Each of those timestamps is `now()`, which is the start of its own
-- transaction, and a server whose clock is stepped backwards between two
-- transactions refuses a wave its own test. Seen on 13.10.2026 under WSL2: a
-- wave rendered at 21:55:45.325 was refused a test stamped 21:55:44.655, a
-- step of 0.67 s, and the same test passed on the next run.
--
-- The order the checks exist for is already held by the writes: a render
-- clears the test and the approval, and a test clears the approval. So the
-- checks keep their "not before" halves and drop the comparison of two clocks.
ALTER TABLE crm.campaign_wave
    DROP CONSTRAINT campaign_wave_check,
    DROP CONSTRAINT campaign_wave_check1,
    ADD CONSTRAINT campaign_wave_tested_after_render
        CHECK (tested_at IS NULL OR rendered_at IS NOT NULL),
    ADD CONSTRAINT campaign_wave_approved_after_test
        CHECK (approved_at IS NULL OR tested_at IS NOT NULL);
