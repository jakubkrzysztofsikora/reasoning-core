---
date: 2026-05-10
commit: n/a (research document, not code)
branch: main
tags: [swebench, gemini, eval, resume-plan, continuation]
status: complete
---
# Research: SWE-bench iter1 Resume Plan — Post-Gemini-Quota Interruption

## Summary

SWE-bench iter1 pilot was **paused 2026-05-09 ~11:43 UTC** when the Gemini subscription quota exhausted after producing 53/990 D2 cells. The Scaleway VM (`51.15.95.42`, server `f2a3bfeb`) appears offline (SSH timeout 2026-05-10). 12 compliance gaps (5 SHOWSTOPPER, 7 BLOCKING) block whitepaper publication. A structured resume plan with 4 phases is proposed below.

## Files Involved

### Eval Toolkit
| File | Purpose |
|------|---------|
| `/Users/jakubsikora/research-gemini-swebench-eval-scripts/eval/sweep.py` | Top-level sweep orchestrator; resume-friendly (skips cells with valid meta.json) |
| `/Users/jakubsikora/research-gemini-swebench-eval-scripts/eval/agent_loop.py` | Multi-turn google-genai SDK loop |
| `/Users/jakubsikora/research-gemini-swebench-eval-scripts/eval/patch_extractor.py` | Dual regex+SDK extractor (needs `repair_unified_diff_structure` fix from RCA) |
| `/Users/jakubsikora/research-gemini-swebench-eval-scripts/scripts/run-d2-sweep.sh` | D2 sweep shell wrapper |

### Research / Planning
| File | Purpose |
|------|---------|
| `thoughts/shared/research/2026-05-09-swebench-iter1-pilot-status.md` | Detailed status at pause time |
| `thoughts/shared/research/2026-05-09-swebench-iter1-setup-b-malformed-diff-rca.md` | RCA for Setup-B malformed diffs + proposed `repair_unified_diff_structure` fix |
| `thoughts/shared/research/swebench-iter1-meta.md` | iter-1 meta doc (design rationale) |
| `thoughts/shared/research/swebench-gemini-prereg-D1.json` | D1 prereg (Setup A baseline, 495 cells) |
| `thoughts/shared/research/swebench-gemini-prereg-D2.json` | D2 prereg (A vs B, 990 cells) |
| `thoughts/shared/plans/2026-05-08-swebench-gemini-eval-toolkit.md` | Plan v2 (33KB, 9 phases) |

## Current State (2026-05-10)

### VM Status
- **Scaleway PRO2-XS** (`nl-ams-1`, IP `51.15.95.42`): SSH timeout → likely stopped or expired.
- Server ID: `f2a3bfeb-7647-49e9-a045-78470aa0d86a`
- Cost if still running: €0.11/h (~€2.65/day)

### Sweep Progress
- **D2 full sweep**: 53/990 cells produced before quota exhaustion
  - 39 cells: `QUOTA_EXHAUSTED` errors (noise, to be deleted on resume)
  - 9 cells: `extractor_disagreement: true` (real agent output, worth inspecting)
  - 5 cells: no error, no resolved flag
  - **0 cells reached swebench grader**
- **D1 sweep**: never run (SHOWSTOPPER #5)
- **Pilot artifacts (v1, v2, RC-fix smoke)**: LOST (previous VM torn down 2026-05-09 morning)

### Quota
- Gemini subscription quota reset: ~2026-05-10 09:36 UTC (expired by now)
- But VM may be down, so quota window is moot until VM is restored

## Compliance Gaps (from pilot-status §5)

### SHOWSTOPPERS (5)
1. **No prereg frozen** — D1+D2 `frozen_at_commit_sha` still `TBD_AT_COMMIT_TIME`
2. **Subset manifest SHA never pinned** — manifest never frozen
3. **Model alias vs dated** — using floating `gemini-2.5-flash` not dated variant; methodology amendment required
4. **Patch extractor disagreement unmeasurable** — Path-2 SDK extractor stubbed, never implemented; kill-switch dead code
5. **No D1 sweep run** — only Setup-A side of pilot D2 implicitly covers D1

### BLOCKING (7)
1. n=3 not achieved (paused mid-flight)
2. Krippendorff α not computed
3. Audit-trail SHA pins never captured
4. `validate-prereg --check-audit-trail` gate never invoked
5. Subset sourcing deviates from prereg (jatinganhotra blog inaccessible)
6. Judge swap without amendment (vibe+claude → 3× Scaleway open-weight)
7. Stop hook added to Setup B post-Phase-8 not in D2 prereg

## Resume Plan — 4 Phases

### Phase R1: VM + Infrastructure (1-2 hours)

1. **Check Scaleway console** — is server `f2a3bfeb` still provisioned?
   - If stopped: restart it, verify SSH access
   - If destroyed: provision new PRO2-XS (`nl-ams-1`), re-bootstrap from toolkit
2. **If new VM needed**: re-run bootstrap:
   ```sh
   # On new VM
   apt update && apt install -y python3-venv git docker.io
   git clone <toolkit-repo> ~/research-gemini-swebench-eval-scripts
   cd ~/research-gemini-swebench-eval-scripts
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Gemini auth**: manual OAuth login required (`gemini` CLI subscription login)
4. **Verify quota reset**:
   ```sh
   gemini -m gemini-2.5-flash -p "reply OK" -o json --yolo --skip-trust 2>&1 | jq -r .response
   ```

### Phase R2: Pre-Sweep Fixes (2-3 hours)

These must land BEFORE any new sweep cells fire:

1. **Land `repair_unified_diff_structure`** in `eval/patch_extractor.py`
   - Full implementation in RCA doc (swebench-iter1-setup-b-malformed-diff-rca.md §4)
   - Add 3 unit tests from RCA §5
   - This fixes the 2/10 malformed-diff cells in Setup B

2. **Resolve SHOWSTOPPER #3 (model alias)**
   - Either: pin exact dated model string from Google registry
   - Or: write amendment documenting `gemini-2.5-flash` as deliberate choice
   - Decision needed from operator

3. **Resolve SHOWSTOPPER #4 (patch extractor)**
   - Either: implement Path-2 SDK extractor
   - Or: amendment removing dual-extractor from prereg (simpler)

4. **Write amendment files** (prereg-D1-v1.1.md, prereg-D2-v1.1.md) covering:
   - Model alias decision
   - Judge swap (vibe+claude → 3× Scaleway)
   - Subset sourcing deviation
   - Stop hook addition to Setup B
   - Plan/impl artifact definitions

5. **Freeze preregs**: populate `frozen_at_commit_sha` + audit-trail SHAs via `freeze.py`

### Phase R3: D1 Sweep First (6-8 hours)

**Run D1 before D2** — resolves SHOWSTOPPER #5 and produces standalone baseline:

1. Validate prereg: `python3 -m eval.cli validate-prereg --prereg .../swebench-gemini-prereg-D1.json --check-audit-trail --check-tbds-resolved`
2. Run D1 sweep: `bash scripts/run-d1-sweep.sh` (495 cells, Setup A only)
3. Subscription pacing: ~30-50 cells/day → D1 takes **10-17 days** with subscription, or **~6h** with paid API ($50)

**Recommendation**: Use paid Gemini API key ($50-100) to complete in one sitting instead of 2+ weeks of subscription pacing.

### Phase R4: D2 Sweep + Post-Pipeline (24-48 hours)

1. Cleanup 39 quota-exhausted cells from previous attempt
2. Run D2 sweep: 990 cells (165 instances × 2 arms × 3 reps)
3. Post-sweep pipeline:
   - swebench Docker grader (both arms)
   - 3-judge × 2 artifact × 990 cells = 5,940 Scaleway calls (~€6, ~30 min)
   - Krippendorff α per BARS dim
   - Aggregate + REPORT.md + decision.json
4. Whitepaper draft

## Cost Forecast

| Approach | D1 (495 cells) | D2 (990 cells) | Total |
|----------|-----------------|-----------------|-------|
| Subscription only | $0 (10-17 days) | $0 (20-34 days) | €15 VM |
| Paid API | $50-90 | $100-180 | €15 VM + $150-270 |
| **Hybrid** (paid D1, sub D2) | $50-90 | $0 (3 days) | €15 VM + $50-90 |

## Decision Points for Operator

1. **VM status**: restart old VM or provision new one?
2. **Model alias**: pin dated variant or amend to allow floating `gemini-2.5-flash`?
3. **Patch extractor Path-2**: implement or remove from prereg?
4. **Funding**: subscription-only (slow, free) vs paid API (fast, $150-270)?
5. **D1 vs D2 order**: run D1 first (SHOWSTOPPER fix) or resume D2 where it left off?

## Recommended Sequence

1. Check/restart VM → 30 min
2. Land patch-extractor fix + write amendments → 2-3h
3. Freeze preregs → 30 min
4. Run D1 sweep (paid API recommended) → 6-8h
5. Run D2 sweep (paid API recommended) → 12-24h
6. Post-sweep pipeline → 3h
7. Whitepaper draft → async

**Total wall time with paid API: ~3-4 days. With subscription only: ~5-7 weeks.**

## Cross-References

- Pilot status: `2026-05-09-swebench-iter1-pilot-status.md`
- Malformed-diff RCA: `2026-05-09-swebench-iter1-setup-b-malformed-diff-rca.md`
- iter-1 meta: `swebench-iter1-meta.md`
- D1 prereg: `swebench-gemini-prereg-D1.json`
- D2 prereg: `swebench-gemini-prereg-D2.json`
- Plan v2: `thoughts/shared/plans/2026-05-08-swebench-gemini-eval-toolkit.md`
- iter-3 whitepaper: `2026-05-08-iter3-eval-whitepaper.md`
