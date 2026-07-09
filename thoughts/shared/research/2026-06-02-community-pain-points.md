# Where the industry feels the pain reasoning-core addresses

## TL;DR

Scope creep from AI coding agents (files touched outside explicit instructions) and rule-file unreliability (CLAUDE.md / .cursorrules ignored mid-session) are the loudest, best-evidenced pain categories with dozens of engineer-authored primary sources. Hallucinated imports and over-engineering are a strong second tier. Token waste from runaway agent loops is real and increasingly quantified in cost terms ($200–$500/month average API spend for heavy users). Demand for local/no-cloud enforcement is rising but is mostly framed as a security and privacy concern rather than a pure guardrail need. Coupling/cohesion violations and repo-convention drift are the thinnest categories in community discourse — they appear in industry-analyst articles and vendor marketing rather than in engineers' own words.

---

## 1. Scope creep / "agent edits files I didn't ask for"

**[1.1]** "The AI agent consistently violates user-defined rules... specifically ignoring scope boundaries and the 'Stop and Confirm Rule'." — **DanSF** (engineer), Cursor Community Forum, Dec 29 2025.
Source: [Cursor Forum — agent ignores scope rules #147589](https://forum.cursor.com/t/agent-repeatedly-ignores-user-rules-and-makes-changes-beyond-explicit-scope-despite-stop-and-confirm-rule/147589)

**[1.2]** "Agent acknowledges the instruction. Agent immediately violates scope by modifying multiple files without permission." — **DanSF**, same thread, documenting the exact failure pattern. Dec 29 2025.
Source: same as [1.1]

**[1.3]** "This issue has increased in severity in the past week. The agent seems to ignore most of the user rules, and even parts of the plan." — **DanSF**, follow-up Dec 31 2025.
Source: same as [1.1]

**[1.4]** "[Cursor] confirmed: 'This is a known issue. The agent can sometimes go outside explicit instructions even with rules.'" — **deanrie (Cursor staff)**, acknowledging the bug, Dec 29 2025.
Source: same as [1.1]

**[1.5]** "Claude Code is powerful but unreliable in long sessions. Without guardrails it will: Make autonomous decisions without asking (scope creep, design changes)." — **weilhalt** (engineer, 3 months daily use, 68 documented failures), GitHub issue #29795, Mar 1 2026.
Source: [anthropics/claude-code #29795](https://github.com/anthropics/claude-code/issues/29795)

**[1.6]** "Claude reads the rules, 'understands' them, and then ignores them under pressure. You need technical enforcement — hooks that physically block forbidden actions." — **weilhalt**, same issue.
Source: same as [1.5]

**[1.7]** "Agent mode is more flexible but also more prone to tangential changes if instructions aren't crystal clear. You end up with changes in random files you never intended to touch." — anonymous reviewer, EngineLabsAI blog, 2025.
Source: [Cursor AI In-Depth Review 2025 — Engine Labs](https://blog.enginelabs.ai/cursor-ai-an-in-depth-review)

**[1.8]** "Ask before acting on ambiguity. Claude tends to interpret questions as tasks and start implementing before the user has confirmed the approach." — **weilhalt**, GitHub #29795, Mar 2026.
Source: same as [1.5]

**Adversarial counter-voice:** Cursor's October 2025 changelog introduced a classifier subagent that reviews shell and fetch tool calls before execution, explicitly targeting the "agent changed files I didn't ask it to" problem. Some users report the feature meaningfully reduced unsolicited edits. Source: [Cursor changelog](https://cursor.com/changelog)

---

## 2. Pattern blindness / reinvention of existing helpers

**[2.1]** "AI-generated code can include up to 8× more duplication than human-authored code. Instead of reusing existing functions, AI tools create new ones, leading to inconsistencies in how the same business rules are applied." — **Huzefa Motiwala**, AlterSquare (15+ codebase rescues), Mar 14 2026.
Source: [We've Rescued 15+ Codebases That AI Tools Helped Break — AlterSquare](https://altersquare.io/rescued-15-plus-codebases-ai-tools-pattern/)

**[2.2]** "Only 1.1% of AI-driven refactorings address code duplication, compared to 13.7% for human developers." — same article, citing empirical analysis. Mar 2026.
Source: same as [2.1]

**[2.3]** "AI suggestions introduce textbook patterns that ignore your architectural conventions. Code reviews catch unfamiliar helper classes, duplicate utilities, and APIs violating team standards." — Augment Code technical guide, 2025.
Source: [AI Coding Assistants for Large Codebases — Augment Code](https://www.augmentcode.com/tools/ai-coding-assistants-for-large-codebases-a-complete-guide)

**[2.4]** "AI is really good at regurgitating variations but struggles with novelty." — **CuriouslyC**, HN comment on "The 70% problem", Dec 6 2024.
Source: [HN: The 70% problem — item 42336553](https://news.ycombinator.com/item?id=42336553)

**[2.5]** "I find the error rate much higher once I start asking it to write code using specific libraries. It's also terrible with DSLs that probably don't have much training data." — **andnand**, HN "2025 State of AI Code Quality", Jun 2025.
Source: [HN: 2025 State of AI Code Quality — item 44257283](https://news.ycombinator.com/item?id=44257283)

**[2.6]** "The AI code assistant only saw the 50 lines that changed, not the 50,000 lines that depended on them." — **Alex Mercer**, cubic.dev, quoted in AlterSquare post, Mar 2026.
Source: same as [2.1]

**Thin evidence note:** No viral engineer-authored HN or Reddit thread was found specifically using the phrase "pattern blindness." The phenomenon is consistently described in vendor/consultant articles and as a secondary comment in quality discussions, not as a stand-alone grievance thread. The pattern exists in primary sources but lacks a canonical complaint post.

---

## 3. Spec drift / refactor sprawl

**[3.1]** "AI generated a new service class, a background worker, several hundred lines of code in the main file. I rejected the PR. I implemented the same functionality with two new methods and one extra field." — **ilitirit**, HN "2025 State of AI Code Quality", Jun 2025.
Source: [HN: 2025 State of AI Code Quality — item 44257283](https://news.ycombinator.com/item?id=44257283)

**[3.2]** "LLMs lack the self-reflective capability to see that maybe an incremental change for a write-heavy op shouldn't be 200 lines of code." — **mattgreenrocks**, same HN thread, Jun 2025.
Source: same as [3.1]

**[3.3]** "Over-engineering: Claude adds extra abstractions and premature refactoring unless told otherwise." — Claude Code best-practices writeup (community, 2026). Note: Anthropic's own docs echo this warning.
Source: [Claude Code Best Practices — Anthropic](https://www.anthropic.com/engineering/claude-code-best-practices)

**[3.4]** "AI writes bad code by default, but it lowers friction to adding code faster than the team can properly absorb it." — **xiaolu627**, HN "Be intentional about how AI changes your codebase", Mar 2026.
Source: [HN: Be intentional about how AI changes your codebase — item 47446373](https://news.ycombinator.com/item?id=47446373)

**[3.5]** "The intentionality has to come before you prompt, not after you review." — **divyanshu_dev**, same HN thread, Mar 2026.
Source: same as [3.4]

**[3.6]** "Step 2: Iterate. I review the spec carefully. The agent will make assumptions. I correct them, add constraints I forgot, remove scope creep." — **Owain Lewis**, dev newsletter "How I Code With AI Agents", Feb 2026.
Source: [How I Code With AI Agents — Owain Lewis newsletter](https://newsletter.owainlewis.com/p/how-i-code-with-ai-agents-spec-driven)

**[3.7]** "AI agents fill gaps, but not in the way you'd want. Without explicit scope, agents make assumptions and head in the wrong direction fast." — Augment Code "What Is Spec-Driven Development?", ~Jun 2026.
Source: [What Is Spec-Driven Development? — Augment Code](https://www.augmentcode.com/guides/what-is-spec-driven-development)

**[3.8]** "LLMs tend to pick up key phrases or technologies, then build their own context about what they think you need. Responses degrade massively and you need to start over." — **ilitirit**, HN item 44257283, Jun 2025.
Source: same as [3.1]

---

## 4. Token waste from off-plan agent loops

**[4.1]** "70% of tokens were waste. Not 'overhead' or 'necessary scaffolding.' Waste." — Morph blog, quoting a tracked study of 42 agent runs on a FastAPI codebase, Apr 2026.
Source: [AI Coding Costs 2026 — Morph](https://www.morphllm.com/ai-coding-costs)

**[4.2]** "One documented case turned a $0.50 fix into a $30 bill through 47 iterations." — same source.
Source: same as [4.1]

**[4.3]** "When a coding agent gets stuck, it doesn't stop. It loops." — Morph, Apr 2026.
Source: same as [4.1]

**[4.4]** "One careless command away from wiping a repository or exposing credentials... you are just clicking 'yes' to keep things moving. That is usually when risk creeps in." — **Mo** (engineering practitioner), Medium post on adding guardrails to their coding agent, Apr 6 2026.
Source: [We Gave Our AI Coding Agent Guardrails — Medium/@mightymo](https://medium.com/@mightymo/we-gave-our-ai-coding-agent-guardrails-and-finally-stopped-clicking-yes-47-times-8bc6a32fd417)

**[4.5]** "The most expensive AI incident most teams have ever had wasn't a wrong answer. It was a loop — an agent that decided to retry, retry, retry, with each retry appending more context, until the bill exceeded the monthly budget in hours." — Falconer Guides, "Why AI agents burn tokens hunting for answers they should already have", May 2026.
Source: [Why your AI agents burn tokens — Falconer Guides](https://falconer.com/guides/ai-agent-token-waste/)

**[4.6]** "Starting subagents despite explicit ban (happened 3+ times before hook)." — **weilhalt**, documenting a specific token-waste failure mode at GitHub #29795, Mar 2026.
Source: [anthropics/claude-code #29795](https://github.com/anthropics/claude-code/issues/29795)

**[4.7]** "87% of tokens went to finding code rather than writing it." — analysis cited in Morph blog, Apr 2026.
Source: same as [4.1]

**[4.8]** "$350 in overages reported by one Cursor user in a single week." — Morph blog, Jun 2025 incident cited.
Source: same as [4.1]

---

## 5. Demand for local / no-cloud enforcement

**[5.1]** "Only AI services that maintain no service-side long term storage, memory, or training impact from submitted data may be used." — **Patrick Walsh** (CEO, IronCore Labs), company AI usage policy, Jan 26 2026.
Source: [AI Coding Agents: Our Privacy Line in the Sand — IronCore Labs](https://ironcorelabs.com/blog/2026/ai-coding-agents-drawing-the-line/)

**[5.2]** "If you've been using AI coding tools with 'zero retention' settings, thinking your data was safe, here's the uncomfortable truth: it isn't." — **Patrick Walsh**, same post, Jan 2026.
Source: same as [5.1]

**[5.3]** "One of the biggest troubles with current AI is its penchant for automatically grabbing context and sending it off to other people's servers." — **Patrick Walsh**, same post.
Source: same as [5.1]

**[5.4]** "There's going to be more and more agents deployed in enterprise settings, and no one has figured security out yet." — **Steven Jung** (co-founder, CodeIntegrity), GeekWire, 2026. CodeIntegrity raised $5M to build a runtime control layer for AI agents.
Source: [CodeIntegrity raises $5M — GeekWire](https://www.geekwire.com/2026/codeintegrity-raises-4-8m-to-put-permanent-guardrails-on-unpredictable-ai-agents/)

**[5.5]** "Everything runs locally. Zero network calls. Built in pure Bash with jq. No frameworks. No hidden supply chain exposure." — LaneKeep project description (open source local agent governance), cited in Mo's Medium post, Apr 2026.
Source: [We Gave Our AI Coding Agent Guardrails — Medium/@mightymo](https://medium.com/@mightymo/we-gave-our-ai-coding-agent-guardrails-and-finally-stopped-clicking-yes-47-times-8bc6a32fd417)

**[5.6]** "The LLM vendors are not going to save us! We need to avoid the lethal trifecta combination of tools ourselves to stay safe." — **Simon Willison** (creator of Django, Datasette), defining the lethal trifecta (private data + untrusted content + external comms), Jun 16 2025.
Source: [The lethal trifecta for AI agents — Simon Willison](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)

**[5.7]** "When using coding agents over entire files or repositories that aren't public, use our self-hosted AI models." — **Patrick Walsh**, IronCore Labs policy, Jan 2026.
Source: same as [5.1]

**Thin evidence note:** Engineer-authored Reddit and HN threads specifically demanding "local guardrails" as a product category were not found. The discourse frames this as a privacy/security concern rather than a quality/governance concern. The demand is real but diffuse across enterprise security blogs rather than concentrated in developer-community rant threads.

---

## 6. Hallucinated APIs / made-up functions

**[6.1]** "I define hallucinations as a particular class of mistakes where the LLM invents a function or method that does not exist. They show up the moment you try to run the code." — **simonw** (Simon Willison), HN "LLM Hallucinations in Practical Code Generation", Jul 2025.
Source: [HN: LLM Hallucinations in Practical Code Generation — item 44353241](https://news.ycombinator.com/item?id=44353241)

**[6.2]** "I've often experienced the loop of pasting the error back to the LLM, only to get a confident yet non-working response using hallucinated APIs." — **imiric**, same HN thread, Jul 2025.
Source: same as [6.1]

**[6.3]** "Keys are just hallucinated based on expectations of what the keys would be called and are either wrong or they flat out don't exist." — **nerdjon** (on IAM policies), same HN thread, Jul 2025.
Source: same as [6.1]

**[6.4]** "The bot hallucinated a non-existent MongoDB PowerShell cmdlet, complete with documentation on how it works." — **stego-tech** (engineer at large tech company), HN "AI can't stop making up software dependencies", Apr 12 2025.
Source: [HN: AI can't stop making up software dependencies — item 43663777](https://news.ycombinator.com/item?id=43663777)

**[6.5]** "Seems to also especially love making up options and settings for command line tools." — **WhitneyLand**, same HN thread, Apr 12 2025.
Source: same as [6.4]

**[6.6]** "The amount of times it just hallucinated parameters and arguments that were not even there were such a huge waste of time." — **rootnod3**, HN "Generative AI coding tools and agents do not work for me", Jun 2025.
Source: [HN: Generative AI coding tools do not work for me — item 44294633](https://news.ycombinator.com/item?id=44294633)

**[6.7]** "My favorite is when the LLM hallucinates some function or an entire library and you call it out, then it proceeds to write the very library it just invented." — **perrygeo**, HN "AI can't stop making up software dependencies", Apr 12 2025.
Source: same as [6.4]

**[6.8]** "What a world: AI hallucinated packages are validated and rubber-stamped by another AI that is too eager to be helpful." — **alganet**, same thread, Apr 12 2025.
Source: same as [6.4]

---

## 7. Existing-tool gaps (CLAUDE.md, cursor rules ignored)

**[7.1]** "CLAUDE.md is a wish list, not a contract." — **DavidAI311** (self-described 12+ hour/day Claude Code power user), DEV Community, ~Mar 8 2026.
Source: [I Wrote 200 Lines of Rules for Claude Code. It Ignored Them All. — DEV Community](https://dev.to/minatoplanb/i-wrote-200-lines-of-rules-for-claude-code-it-ignored-them-all-4639)

**[7.2]** "Rules in prompts are requests. Hooks in code are laws." — same author, same post.
Source: same as [7.1]

**[7.3]** "Claude.md files can get pretty long, and many times Claude Code just stops following a lot of the directions." — **nico**, HN discussion on CLAUDE.md, ~Dec 2025.
Source: [HN: Claude often ignores CLAUDE.md — item 46102048](https://news.ycombinator.com/item?id=46102048)

**[7.4]** "The longer the context window gets, the more likely it is to forget rules and instructions." — **dkersten**, same HN thread.
Source: same as [7.3]

**[7.5]** "Claude would adhere to instructions somewhat reliably at the beginning and end of the conversation, but was likely to ignore them during the middle." — **chickensong**, same HN thread.
Source: same as [7.3]

**[7.6]** "What is the point of rules if they are ignored?" — **AndyRoid**, Cursor Forum "Why does Cursor ignore rules?", Oct 13 2025.
Source: [Cursor Forum: Why does Cursor ignore rules? — #137219](https://forum.cursor.com/t/why-does-cursor-ignore-rules/137219)

**[7.7]** "I write detailed rules, I mark all rules important, then when the time comes, they are applied partially." — **kyurkchyan**, same thread, Oct 31 2025.
Source: same as [7.6]

**[7.8]** "This is a known issue: Cursor sometimes follows rules inconsistently even when they're clearly formatted." — **deanrie (Cursor staff)**, Oct 13 2025.
Source: same as [7.6]

**[7.9]** "Files like .cursor/rules/project_rules.md and AGENTS.md and referenced files are usually ignored, with responses ignoring specific instructions from the prompt." — user report, Cursor Forum "Model auto agent mode does never follow the prompt", Mar 2026.
Source: [Cursor Forum: Model auto agent mode ignores prompt — #153953](https://forum.cursor.com/t/model-auto-agent-mode-does-never-follow-the-prompt-and-lies/153953)

**Adversarial counter-voice (rules DO work):** Arize AI's Oct 2025 study showed that optimizing Cline rule files (clinerules) improved SWE-bench accuracy by 10–15%, and Anthropic recommends keeping CLAUDE.md tightly scoped (one instruction per session) rather than growing it unbounded. The failure mode is largely a length and ambiguity problem, not a fundamental impossibility. Source: [Optimizing Coding Agent Rules — Arize AI](https://arize.com/blog/optimizing-coding-agent-rules-claude-md-agents-md-clinerules-cursor-rules-for-improved-accuracy/)

---

## What's NOT well-covered by community discourse

**Coupling/cohesion violations (cat. 4 in reasoning-core):** Thin evidence. No primary-source engineer rant was found specifically complaining that an AI agent wrote code in the wrong architectural layer or broke module boundaries. This appears in vendor/analyst prose (Augment Code, AlterSquare) as part of a broader "ignores conventions" complaint, but there is no dedicated thread treating it as a distinct pain point. The community frames it as "AI doesn't understand our architecture" rather than "AI violated layer X."

**Repo-convention drift (cat. 8):** Moderate secondary evidence. Multiple guides note that AI produces "textbook patterns" rather than project-idiomatic code, but few engineers have written dedicated rants about it. The canonical complaint is usually mixed with scope creep or reinvention complaints. Augment Code's guide is the clearest statement, but it is vendor-authored, not a community post.

**"Cleanup" over-engineering as a standalone category:** Documented in Anthropic's own best-practices page and by the weilhalt github issue, but community-level discourse tends to fold it into "AI made too many changes" rather than naming it as "unnecessary error handling" or "unwanted abstractions" specifically.

---

## Source quality notes

**Primary (engineer's own words, direct experience):**
- weilhalt, GitHub issue #29795 — highest-quality primary source. 3 months of documented failures, 68 failure entries, reproducible. Covers scope creep, rule bypass via tool-switching, context loss, token waste.
- DanSF, Cursor Forum #147589 — direct bug report with request IDs and step-by-step reproduction.
- simonw (Simon Willison), HN item 44353241 — trusted independent voice, co-creator of Django, direct definition of hallucination failure mode.
- perrygeo, WhitneyLand, stego-tech, nerdjon — HN comment-section engineers, single-incident reports, no institutional affiliation.
- DavidAI311 (DEV Community) — partly AI-drafted by author's own admission; treat quotes as illustrative rather than hard evidence.
- Patrick Walsh (IronCore Labs blog) — company policy primary source; trustworthy on privacy/local-enforcement framing, but represents a security vendor's perspective.

**Secondary (journalist or vendor summaries):**
- Morph blog cost analysis — no byline; statistics presented without linked methodology. Treat as directionally correct, not citable for exact numbers.
- AlterSquare "rescued codebases" — consulting firm marketing content; specific numbers (8× duplication) not linked to a primary study.
- Augment Code guide — vendor guide. Useful framing but self-serving in recommending their product.
- Falconer Guides "token waste" — no byline, appears AI-generated or lightly edited; directionally consistent with primary sources.

**Institutional/research:**
- CodeIntegrity $5M raise (GeekWire) — corroborates market demand for guardrails; founder quotes are primary.
- Karpathy X thread (Dec 2025) — high-trust primary for the "agents now work" counter-narrative; not directly complaining about reasoning-core pain points.
- Simon Willison "lethal trifecta" — primary; most authoritative voice on agent security. Directly relevant to local-enforcement demand.
