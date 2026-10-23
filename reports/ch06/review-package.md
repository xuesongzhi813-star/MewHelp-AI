# ch06 backend review package

Native execution; user forbids commits. BASE=HEAD=43516c8; HEAD diff includes old uncommitted ch05 changes, so review CURRENT listed files and approved spec/plan, not an empty commit range. Baseline hashes: reports/ch06/baseline.json. No claim baseline source snapshots exist.

Spec: docs/superpowers/specs/2026-10-23-mewhelp-ch06-router-design.md
Plan: docs/superpowers/plans/2026-10-23-mewhelp-ch06-router.md
Ledger: .superpowers/sdd/2026-10-23-mewhelp-ch06-router/progress.md

USER RULINGS: native LangGraph interrupt + Command resume required; intent uses main model ONLY, future small-model config seeds inert. No frontend code review (explicit Vibe Coding exception). No DDL, migrations, resets, dependency upgrades, commits, external writes. Refund application is a confirmed synthetic after-sales ticket, never a money refund.

Changed/new backend and tests:
- app/api/chat_actions.py
- app/config.py
- app/demo_orders.py
- app/kb/contracts.py
- app/kb/filters.py
- app/kb/multi_query.py
- app/kb/query.py
- app/llm.py
- app/schemas.py
- app/tools.py
- app/workflow/actions.py
- app/workflow/agent.py
- app/workflow/contracts.py
- app/workflow/graph.py
- app/workflow/knowledge.py
- app/workflow/models.py
- app/workflow/nodes.py
- app/workflow/orders.py
- app/workflow/refund_actions.py
- app/workflow/router_prompts.py
- app/workflow/routing.py
- app/workflow/runtime.py
- app/workflow/understanding.py
- scripts/demo_ch06.py
- scripts/eval_ch05.py
- scripts/eval_ch06.py
- tests/test_eval_ch06.py
- tests/test_intent_config_seeds.py
- tests/test_kb_multi_query.py
- tests/test_langgraph_interrupt_contract.py
- tests/test_order_selection_api.py
- tests/test_refund_actions.py
- tests/test_workflow_graph.py
- tests/test_workflow_knowledge.py
- tests/test_workflow_models.py
- tests/test_workflow_orders.py
- tests/test_workflow_refund.py
- tests/test_workflow_routing.py
- tests/test_workflow_understanding.py

Evidence: baseline637; Task2 full655; Task4 full671; Task5 full677; Task6 related27. Native SQLite interrupt/restart and Command(update) preserving pause verified. Router real dev16/16; final acceptance pending. Frontend synthetic browser verified separately, not for your review.

Review Focus: ambiguous multiple-order pronouns; new chat invalidates old selection; concurrent clicks/disconnect/duplicate confirmation; partial retrieval failure cannot hide behind other hits; fixed card/order/logistics data consistency. Also inspect prompt injection, condition preservation, schema integrity, budgets, archive/checkpoint failure and transient fields.

Report concrete Critical/Important/Minor findings with file/line, reproduction and effect; include declined-to-judge scope. Read-only: do not edit product or run real writes. Save report to reports/ch06/review.md if possible (sole allowed output file).