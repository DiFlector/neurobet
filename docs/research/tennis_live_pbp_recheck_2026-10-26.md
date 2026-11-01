# Live PBP and profile recheck — 2026-10-26

Read-only production recheck at 2026-10-26 02:34 UTC. No profile, identity, link, or queue rows were edited.

## Profile refresh and Fonbet `69155384`

Both participants have completed refresh jobs and available profile records:

| Fonbet side | Profile | Refresh job | Imported history |
|---|---|---|---:|
| A, Циципас Петрос | Flashscore `v9LewqHa` | completed, 4 attempts | 154 matches; pagination complete for the recorded overlap |
| B, Валес А | Flashscore `Y75GebJP` | completed, 7 attempts | 235 matches; source marks the full history as incomplete |

The repeated match lookup for `69155384` is durably `matched` to Flashscore `h8LO17tg`. Its latest link evidence has `external_home_is_fonbet_a=true`, based on confirmed Flashscore player IDs. The job is not producing point history: the latest fetch at 02:29:30 UTC returned HTTP 200 but saved a partial, retrospective version with **0 events**, `point_history_tab_missing`, `PBP_TAB_NOT_PUBLISHED`, and `set_tab_confirmed=false`. The point refresh is `retry` after 82 attempts. This confirms profile/job completion and match search, but not a usable PBP tape.

For event `69173294`, the durable link has since been updated with `external_home_is_fonbet_a=true` from `flashscore_scoreboard_dom_confirmed_profile_ids`. The old PBP evidence remains only 53 confirmed events, ending at set 1, 5:3; it does not reach the final 1:2 (6-4, 2-6, 0-6) score. The orientation correction does not complete that sequence.

## Current live match check

At the same snapshot, there were 11 numeric-ID live Fonbet tennis events and **0** with a matched/manual Flashscore link. The eligible retry candidate `69316735` (Мията М — Химено Н) remained `LIVE_MATCH_NOT_FOUND` after three searches; its PBP job is `waiting_match`. Thus there was no real linked live match on which to verify score synchronization and side orientation. PBP observations remain ineligible for labels until that evidence exists.

## Decision

The profile refresh and repeated lookup substeps are confirmed, but the checkbox as written is **not complete**. The source returned no point-by-point tab for `69155384`, and no live Fonbet event had a linked Flashscore PBP tape at this snapshot. Keep the item open until a linked match yields non-empty PBP that reaches the contemporaneous Fonbet score and has durable confirmed side orientation. Do not infer the side from names or use the stale 2022/2026 historical sequence as a live synchronization pass.
