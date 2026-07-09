# swebench-iter1 — Pilot Status & Resume Plan

**Date**: 2026-05-09 (afternoon UTC+2)
**Author**: Jakub Sikora (synthesized from 3 parallel subagent passes)
**Status**: **PAUSED — subscription quota exhausted, ETA resume ~22h**

---

## Bottom line

- Toolkit + setups + RC fix all functional end-to-end on Linux x86 cloud VM.
- 53 cells produced of the prereg-target 990 before subscription Gemini hit daily cap.
- **Most cells (39/53) are quota-exhausted noise** — will be auto-cleared on resume; no real loss.
- Salvageable real-agent cells: **~14** (5 OK + 9 with extractor-disagreement flags worth post-hoc inspection).
- **Earlier pilot artifacts (v1, v2 regrade, RC-fix smoke) are LOST** — the old VM holding them was torn down 2026-05-09 morning. The numbers cited in prior interactions (B 40% / A 60% resolved, B +0.43 repo_fit, B −30% tokens) are NOT independently verifiable from on-disk evidence on the current VM.
- 12 standards-compliance gaps (5 SHOWSTOPPER, 7 BLOCKING) prevent whitepaper publication AS-IS.
- Resume is mechanical: wait ~22h, re-launch the same script (resume-friendly: skips cells with valid `meta.json`).

---

## 1. Current state of the VM

- **Host**: Scaleway PRO2-XS, `nl-ams-1`, IP `51.15.95.42`. 4 vCPU / 15GB / 225GB disk (5GB used).
- **Server ID** (for tear-down later): `f2a3bfeb-7647-49e9-a045-78470aa0d86a`
- **Idle cost**: €0.11/h while paused. €2.65/day if left running.
- **Software state**: gemini CLI 0.41.2, Python venv with google-genai 1.75.0, swebench 2.1.8, mcp/httpx/portalocker, RC checkout at commit `6c048c9` (HEAD of main, includes diff-audit fix at `a29327d`), eval-setups-gemini patched for VM paths. All 57 toolkit unit tests pass.
- **Auth**: Gemini OAuth subscription logged in (operator did this manually post-bootstrap).
- **Sweep paused**: `pkill -f run-d2-full` ran. No active processes. Disk clean.

---

## 2. What ran, what didn't

### 2a. Pilot D2 v1 + v2 + RC-fix smoke — **artifacts LOST**

These ran on a previous VM (`d262ab4b-...`) that was torn down at ~09:55 UTC 2026-05-09 to provision the bigger box. The earlier-cited numbers were captured live in the chat transcript at the time but the source `REPORT.md` / `decision.json` / `*-rerun.json` files are gone.

**Honest framing for the whitepaper**: cite the chat-transcript-archived numbers as "pilot observations" with the caveat that on-disk reproducibility of v1/v2 is lost. The full sweep on the current VM is the load-bearing artifact.

### 2b. Full D2 attempt (current, paused at 53/990) — **on-disk evidence**

Verified directly from `/root/eval-cells/d2-full/cells/{A,B}/*/run-*/meta.json`:

| Bucket | A | B | total |
|---|---|---|---|
| runs produced | 27 | 26 | 53 |
| `error` contains `QUOTA_EXHAUSTED` | 20 | 19 | **39** (73.6%) |
| `extractor_disagreement: true` | 6 | 3 | 9 |
| no error, no resolved flag | 1 | 4 | 5 |

**0 cells reached swebench grader** (sweep didn't get to step-2 grading before quota cap hit).

**Action on resume**: delete the 39 quota-exhausted cells (script ready) so they're retried; preserve the 14 real-agent cells; sweep picks up from instance 9-ish of 165.

### 2c. Iter-3 cross-iteration baseline — **fully verified**

iter-3 whitepaper at `2026-05-08-iter3-eval-whitepaper.md` is on-disk and whitepaper-quality.

| metric | iter-3 (Claude+Circit, n=24) | swebench-iter1 v1+v2 (transcript-cited) |
|---|---|---|
| B repo_fit Δ | +0.43 | ~+0.43 (matches direction) |
| B tokens | −8.2% | −30% (stronger) |
| B locked / resolved | 1.00 vs A 0.92 (saturated) | 40% vs A 60% (B LOSES on swebench) |
| B wall | +98s/run | +73s/run (matches direction) |

**Directional concordance on rubric quality + token economics. Divergence on correctness % — SWE-bench is harder ceiling than Circit; B's denser-plan-but-less-execution pattern shows up.** Per D2 prereg `external_validity_claim`, magnitudes NOT poolable.

---

## 3. Why we wait

**Subscription Gemini quota exhausted on `gemini-2.5-flash`** after ~30 real agent calls. Error from API:
> `'You have exhausted your capacity on this model. Your quota will reset after 21h53m56s.'`

Reset time observed: **~21h53m from quota-hit moment** (2026-05-09 ~11:43 UTC).

ETA resume window opens: **2026-05-10 ~09:36 UTC** (≈ +22h from pause).

### Alternatives (rejected for now)

1. **Paid Gemini API** (~$50–100 for 990 cells × ~10k tok × $0.15/M) — would unblock immediately. Operator preferred to wait + use subscription.
2. **Switch model to `gemini-2.5-pro`** — same subscription, separate quota, but earlier observed `retryWithBackoff` exhaustion → likely also rate-limited. Quota counters may be shared.
3. **Cut scope to n=1** — would fit subscription daily quota but loses statistical claim. Operator did not authorize.

---

## 4. How we resume

### 4.1 Pre-resume cleanup (manual, ~1 min)

```sh
ssh root@51.15.95.42
# Delete the 39 quota-exhausted cells; retain the 14 real cells
python3 <<'PY'
import json, shutil
from pathlib import Path
deleted = 0
for m in Path('/root/eval-cells/d2-full/cells').rglob('meta.json'):
    d = json.loads(m.read_text())
    e = d.get('error') or ''
    if 'QUOTA' in e or 'exhausted your capacity' in e:
        shutil.rmtree(m.parent)
        deleted += 1
print(f'deleted {deleted} quota-cells')
PY
```

### 4.2 Verify quota reset

```sh
gemini -m gemini-2.5-flash -p "reply OK" -o json --yolo --skip-trust 2>&1 | jq -r .response
# expect: "OK"  (any QUOTA_EXHAUSTED → still capped)
```

### 4.3 Re-launch (resume-friendly; sweep skips cells with valid meta.json)

```sh
bash /root/launch-d2.sh
# tails /root/d2-full.log; watches for next-cell heartbeat
```

The threaded sweep (`max_workers=2`) will pick up from the first instance that doesn't have all 6 cells (3 reps × 2 arms) complete. Expected wall: **~24h** at the v2-pace (4 cells / 5 min × 2 workers ≈ 50 cells/h × 990 cells = ~20h, +5h overhead).

### 4.4 Subscription pacing (DO NOT exceed)

Subscription daily cap empirically: ~30-50 real-agent cells per day on flash. Strategy:
- Pause sweep ~14h after resume (reaches ~700 cells if pace holds)
- Wait next quota window (~10h)
- Resume; finish remaining 290 cells in ~6h

Total wall to complete: **~3 days** (24h work + 2× quota wait). Or **~12h with a $50 paid API key**.

### 4.5 Post-sweep pipeline (~3h after sweep done)

1. swebench Docker grader on both arms (cached images make 2nd-arm fast)
2. 3-judge × 2-art × 990 cells = 5,940 Scaleway calls (~€6, ~30 min)
3. Krippendorff α per BARS dim (already in `aggregate.py:krippendorff_alpha`)
4. Aggregate + REPORT.md + decision.json

---

## 5. Compliance gaps (whitepaper readiness)

From compliance-auditor subagent. **Each must be addressed before publication:**

### SHOWSTOPPERS (5)
1. No prereg has actually been frozen (D1+D2 `frozen_at_commit_sha` still `TBD_AT_COMMIT_TIME`).
2. Subset manifest live SHA differs from any pinned value. Manifest never frozen.
3. Model-alias-vs-dated: subscription doesn't expose dated `gemini-2.5-pro-preview-XX-XX`. Currently using floating alias `gemini-2.5-flash`. Methodology amendment required.
4. Patch-extractor disagreement metric is structurally unmeasurable (Path-2 SDK extractor was stubbed, never implemented). Kill-switch is dead code.
5. **No D1 sweep has run.** Only Setup-A side of pilot D2 implicitly covers D1 territory; needs a clean D1 sweep + standalone REPORT.

### BLOCKING (7)
1. n=3 not yet achieved (full D2 paused mid-flight; pilot v1+v2 was n=1)
2. Krippendorff α not yet computed in any REPORT
3. Audit-trail SHA pins never captured (D1 lists 25 files; `freeze.py` `frozen_at: TBD`)
4. `validate-prereg --check-audit-trail` gate never invoked pre-sweep
5. Subset sourcing deviates silently from prereg (jatinganhotra blog inaccessible; used local proxy "no-hints + longest test_patch")
6. Judge swap (vibe+claude → 3× Scaleway open-weight) without amendment doc
7. Stop hook (`post_assistant_diff_audit`) added to Setup B post-Phase-8; not in D2 prereg `hook_chain` enumeration

### Required amendment files (before next sweep freezes)
- `prereg-D2-v1.1.md` — model alias, judge swap, subset sourcing, plan/impl artifact definitions, Stop hook addition
- Same shape as iter-3's `iter3-prereg-v2.md` precedent

---

## 6. Honest open questions for full sweep

1. Does RC fix `a29327d` (recovery guidance + validate_unified_diff + Stop hook) eliminate the malformed-diff failure mode at n=3 + 990-cell scale, or does residual H2 (delta-accumulator buffer truncation) leak through?
2. Does the rubric/resolved% paradox (B writes denser/cleaner but executes less correctly on harder benchmarks) replicate at scale, or collapse to ceiling like iter-3?
3. Does B −30% token discount (swebench v1 transcript) hold consistent under flash, OR was it an artifact of flash-specific behavior?
4. Holdout-subset analysis (30 random instances vs 135 curated): does resolved% diverge by >15pp?
5. Judge α: do the 3 Scaleway open-weight judges achieve α≥0.6 per dim, or does Scaleway-shared-infra correlation deflate cross-family α?

---

## 7. Engineering decision log (chronological)

- **Phase 0-3 (2026-05-08)**: split D1/D2 preregs, pip-install swebench (not vendor), Phase 9 pivot SDK→CLI subprocess for treatment integrity
- **Phase 4-6 (2026-05-08)**: cell artifact schema, dual-extractor stub, 5-BARS rubric, 3-judge HTTP framework
- **Phase 7-9 (2026-05-08)**: smoke validation (sympy-11618 resolved, 174s), 10-instance pilot v1, regrade v2
- **2026-05-09 morning**: malformed-diff RCA (RC agent), `a29327d` fix landed, smoke verified on 2 prev-failed cells (1 flipped, 1 still error — n=1 noise)
- **2026-05-09 mid-day**: provision PRO2-XS, regenerate 165-instance manifest, fire full D2, hit 4 issues in succession:
  1. Model alias (used flash, not dated)
  2. SSH/Cato anti-DDoS (resolved by zone migration)
  3. `--skip-trust` flag missing (added to agent_loop.py)
  4. Quota exhaustion (paused; awaiting reset)
- **Now**: PAUSED.

---

## 8. Cost ledger

| Line item | spent |
|---|---|
| Scaleway VMs (3 of: PLAY2-MICRO, PRO2-XXS, PRO2-XS) | ~€2 |
| Scaleway judge calls (~80) | ~€0.10 |
| Gemini subscription | free (quota-capped) |
| **Total iter-1 to date** | **<€3** |

Forecast for full prereg-compliant sweep:
- VM 3-day wall (with subscription): €8
- Scaleway judges 5,940 calls: €6
- Gemini subscription: free
- **Total to complete**: **~€15** (or €70-120 if paid Gemini API used)

---

## 9. Cross-references

- iter-3 whitepaper: `2026-05-08-iter3-eval-whitepaper.md`
- D1 prereg: `swebench-gemini-prereg-D1.json`
- D2 prereg: `swebench-gemini-prereg-D2.json`
- iter-1 meta: `swebench-iter1-meta.md`
- RC malformed-diff RCA: `2026-05-09-swebench-iter1-setup-b-malformed-diff-rca.md`
- Plan v2: `thoughts/shared/plans/2026-05-08-swebench-gemini-eval-toolkit.md`

---

## 10. Resume checklist (operator copy-paste)

- [ ] 2026-05-10 ~09:36 UTC: ssh `root@51.15.95.42`, run quota-test (§4.2)
- [ ] If quota OK: cleanup quota-cells (§4.1) + `bash /root/launch-d2.sh` (§4.3)
- [ ] Watch for ~700 cells produced over ~14h (subscription pacing)
- [ ] Pause if quota hits again; resume next window
- [ ] After ~990 cells: fire post-sweep pipeline (`/root/run-d2-postsweep.py`)
- [ ] Write 6 amendment files (model, judge, subset, plan/impl artifact, Stop hook) before declaring frozen
- [ ] Compute audit-trail SHAs in freeze.py + commit prereg with frozen_at_commit_sha populated
- [ ] Then start whitepaper draft against the new REPORT.md
