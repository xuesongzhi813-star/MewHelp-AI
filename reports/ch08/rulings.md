# ch08 全部技术裁决与代价

按专属账本原顺序保留全部Ruling；早期过渡决策是历史，后续修订以spec第16节与最终报告为准。用户已选择Native并持续授权开发；未授权Git交付，故保留工作树及账本。

唯一原Minor已升级Important并修复，无延后代码级Minor。真实模型路由/指代差异按原标签记录，不冒称全部语义满分。

- Ruling: use PowerShell/Python equivalents of skill shell helpers — only WSL bash is available, no Git bash; preserve same plan-owned briefs, BASE, RED/GREEN logs, and completion contracts — cost if wrong: bookkeeping may require manual reconciliation.
- Ruling: no commits and preserve ledger — project/user instructions override skill cleanup/commit defaults — cost if wrong: no Git checkpoint until user later authorizes commit.
- Ruling: existing epoch agent deadline converts at engine boundary to monotonic remaining time — avoid clock-base mixing — cost if wrong: deadlines might expire early.
- Ruling: uv sync changed editable project path; restored original root mapping with --no-deps reinstall; future sync uses --no-install-project — preserve shared environment behavior — cost if wrong: imports might point at wrong checkout.
- Task 2: Ruling: PreparedCall includes source/remote_name/endpoint/policy_hash metadata — enables restart-safe local revalidation without handlers in State — cost if wrong: serialization adapters must retain extra fields.
- Task 2: Ruling: legacy executor behavior changes move to Task6 integration — do not temporarily replace old entrypoints before service is wired — cost if wrong: inactive new engine does not yet enforce production permissions.
- Task 3: Ruling: retain Core Connection across transaction commit for RELEASE_LOCK; injected outer transaction uses savepoint — Session commit could otherwise return locked connection to pool — cost if wrong: lock release/rollback behavior changes.
- Task 4: Ruling: use dict[str,Any] rather than bare dict for SDK1.30.0 structuredContent — observed protocol failure — cost if wrong: fall back to text parsing would be required.
- Task 5: Ruling: fake async transport cleanup uses try/finally — cancellation bypassed code after yield; preserve same-owner assertion — cost if wrong: fake lifecycle coverage could misrepresent SDK cleanup.
- Task 6: Ruling: ContextVar isolates per-node service+snapshot instead of adding keywords to every injected gateway — compatible with existing tests and metering — cost if wrong: task context propagation might drift; concurrent snapshot tests cover it.
- Task 6: Ruling: legacy obsolete retry/hidden-argument-overwrite/five-builtin tests migrate to strict new semantics; keep real bypass/metadata/artifact/concurrency safety assertions — cost if wrong: old integration compatibility changes as intentionally designed.
- Task 6: Ruling: Unicode draft output preflight belongs to Task7 draft validation and Task11 provider calibration — no draft exists earlier — cost if wrong: large legal text might exceed decision output until those tasks.
- Ruling: confirmation coordinator emits final result only after graph checkpoint, nonce may be persisted as opaque reference but grant exists only in bounded process memory; replay cannot reissue at execute entry — cost if wrong: false success or repeated write after recovery.
- Task 11: Ruling: ch08 demo W24000 rather than18000 after measured layer1=442 below one turn — decision2600 needs capacity — cost if wrong: different compression boundary, old profile unchanged.
- Task 11: Ruling: loopback SDK client trust_env=False avoids Windows proxy environment overhead/forwarding — measured deadline failure and hostile-proxy RED→GREEN — cost if wrong: local-only transport cannot use environment proxy.
- Task 11: Ruling: preserve strict semantic/longcopy/hotplug/word-order failures without changing labels or silently replacing args — engine retains verified equality — cost if wrong: users may need clarification or other routing.
- Final: Ruling: audit source Minor regraded Important and fixed — false server attribution violates explicit audit truth — cost if wrong: security investigation is misled.
- Final: Ruling: whole-message and short draft_id protocol, literal kept strict, resolved payload also bound to nonce/audit — avoids proven long-copy failure without narrowing legal range — cost if wrong: schema hash changes invalidate old definitions/previews, payload binding is essential.
- Final: Ruling: legacy confirmed_action retains full server-built text inside engine validation/capability boundary; deterministic admission refusal is rejected — preserve old500-note contract — cost if wrong: internal and model-facing argument contracts differ and require separate coverage.
- Final: Ruling: none clears draft; typed actual enum reply may fill collecting; active create_ticket missing-description reply defaults to current real user text — prevents cross-topic implicit inheritance and model recopy — cost if wrong: semantic continuation classification may still need clarification, frontend confirmation remains mandatory.
- Final: Ruling: question/order rewrite fallback preserves only parsed ticket sources for independent SQL-user verification — raw question/order safety not relaxed — cost if wrong: source verification must remain separate and fail closed.
- Final: Ruling: source text preflight uses evidence/input allowance rather than literal-output2600, since decision emits ID — cost if wrong: oversized Unicode inputs are asked to shorten when input budget cannot fit.
- Final: Ruling: map existing nullable LCQ JSON/unsignedBIGINT columns only — current schema drift broke real mapping test — cost if wrong: other installations must provision the same externally maintained schema; no migration/backfill performed.
- Final: Ruling: declined public auth/multiworker/crossDB exactly-once remains excluded — local single-process demo expectation — cost if wrong: cannot deploy/scale this as a production service.
- Final: Ruling: declined protected six changes remain untouched and regression-tested — not ch08-authored — cost if wrong: no separate security review of their original implementations.
- Final: Ruling: declined literal multi-correction success is not inferred; default reference path has real2/2 full-length success — cost if wrong: forced literal clients can still be rejected on copy mismatch.
- Final: Ruling: finishing keeps the existing named-branch worktree and ledger under the user's no commit/push/merge/deploy rule, rather than asking again or applying skill cleanup defaults — cost if wrong: no Git delivery/checkpoint until separately authorized; workspace backup remains necessary.
