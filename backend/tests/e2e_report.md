# E2E Test Report — Three Execution Paths
Generated: 2026-04-13 12:06:49

## Summary
- **Total tests**: 17
- **Passed**: 15  **Failed**: 0  **Errors**: 2  **Skipped**: 0

## Phase 1: One-Shot

### ✅ 1.1: wayfarer — simple query (no files)
- **Status**: PASS
- **Timing**: 25.2s
- **Response length**: 1579 chars
- **Response preview**: Because there were no internal documents or website URL provided for this workspace, I lack the internal context to confirm this entity's exact identity. I would need the company's website, a pitch deck, or a company summary to be absolutely certain.   However, based on venture capital and startup d
- **Notes**: Got meaningful response from workspace context alone.

### ✅ 1.2: wayfarer — query with PDF selected
- **Status**: PASS
- **Timing**: 19.7s
- **Response length**: 2089 chars
- **Response preview**: Here is a summary of the pitch deck for **Wayfarer Labs**:  **Company Overview & Core Product** *   **The Concept:** Wayfarer Labs is building an "Interactive Media Engine" (an AI model) designed to transform unstructured media (like video) into interactive, playable simulations (Page 2). *   **The 
- **Notes**: PDF sent native via build_context_parts.

### ✅ 1.5: Elastro — query with image selected
- **Status**: PASS
- **Timing**: 11.5s
- **Response length**: 632 chars
- **Response preview**: Based on the provided image, this is a line item from a financial ledger or bank statement showing an outgoing transaction.   Here are the specific details visible in the record: * **Date:** June 05, 2025 * **Description:** ONLINE DOMESTIC WIRE TRANSFER A/C: ELASTRO, INC. WATERTOWN MA 02472- US TRN:
- **Notes**: Image sent as native binary via build_context_parts.

### ✅ 1.6: scenic — 34MB PDF (compression)
- **Status**: PASS
- **Timing**: 29.2s
- **Response length**: 2211 chars
- **Response preview**: Based on the provided pitch deck, this presentation is for a startup called **SceniX**, which is developing a **"game engine for robotics."**   Here is a breakdown of what the pitch deck covers:  *   **Core Value Proposition:** SceniX aims to enable rapid development and deployment of robotic learni
- **Notes**: 34MB PDF compressed + sent native.

### ✅ 1.7: wayfarer — Extract Info preset
- **Status**: PASS
- **Timing**: 4.8s
- **Notes**: Extract Info preset runs as one-shot.

## Phase 2: ReAct Agent

### ✅ 2.1: wayfarer — workspace browse (ReAct)
- **Status**: PASS
- **Timing**: 10.1s
- **Steps**: 4
- **Response length**: 458 chars
- **Step trace**:
  1. Queued...
  2. Model running (may take a while)...
  3. Browsing workspace tree...
  4. Done
- **Response preview**: Here is the current structure of the workspace, which contains 4 files and 4 folders:  *   **Data Room/**     *   `wayfarer lab.pdf` *(A pitch deck for Wayfarer Labs, a startup developing an AI-powered inference engine for interactive media generation)* *   **Deliverables/**     *   `extract_info.js
- **Notes**: Completed in 4 steps.

### ✅ 2.2: scenic — read docx (ReAct)
- **Status**: PASS
- **Timing**: 14.1s
- **Steps**: 5
- **Response length**: 1145 chars
- **Step trace**:
  1. Queued...
  2. Model running (may take a while)...
  3. Reading Data Room/Scenix Materials/SceniX Executive Summary.docx...
  4. Composing reply...
  5. Done
- **Response preview**: Based on the Executive Summary, **SceniX is building a SaaS platform for large-scale robotic evaluation using a physics- and learning-based simulation engine.**   Its goal is to bridge the gap between software simulation and real-world robotics deployment (the "Sim to Real" gap). Specifically, Sceni

### ✅ 2.3: scenic — 34MB PDF via agent (ReAct)
- **Status**: PASS
- **Timing**: 68.3s
- **Steps**: 5
- **Response length**: 2435 chars
- **Step trace**:
  1. Queued...
  2. Model running (may take a while)...
  3. Reading Data Room/Scenix Materials/Evidence/Scenix-v2 Deck.pdf...
  4. Reading Deliverables/Memos/scenix-deck-analysis.md...
  5. Done
- **Response preview**: Here is a summary of the key points from the SceniX pitch deck, focusing on technical capabilities, milestones, and product readiness:  ### 1. Technical Capabilities SceniX is building a "Game Engine for Robotics" that utilizes a hybrid approach, combining traditional physics-based models with moder
- **Notes**: 34MB PDF compressed + base64 via agent tool.

### ✅ 2.4: scenic — image via agent (ReAct)
- **Status**: PASS
- **Timing**: 12.1s
- **Steps**: 4
- **Response length**: 593 chars
- **Step trace**:
  1. Queued...
  2. Model running (may take a while)...
  3. Reading Data Room/Scenix Materials/Scenix - 200k.png...
  4. Done
- **Response preview**: The image shows a snippet of a bank statement or transaction record detailing an outgoing wire transfer. Here are the specific details visible:  *   **Date:** Apr 02, 2025 *   **Transaction Type:** Outgoing wire transfer *   **Amount:** -$200,000.00 *   **Description/Memo:** ONLINE DOMESTIC WIRE TRA
- **Notes**: Image sent native via base64 in agent tool response.

### 💥 2.6: Abinitia Labs — Red Team preset (ReAct)
- **Status**: ERROR
- **Timing**: 90.1s
- **Error**: `ReadTimeout: 
Traceback (most recent call last):
  File "/Users/caspar/Coding_Projects/vc-agent/venv/lib/python3.14/site-packages/httpx/_transports/default.py", line 101, in map_httpcore_exceptions
    yield
  File "/Users/caspar/Coding_Projects/vc-agent/venv/lib/python3.14/site-packages/httpx/_transports/default.py", line 394, in handle_async_request
    resp = await self._pool.handle_async_request(req)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/caspar/Coding_Projects/`

### 💥 2.9: Elastro — data room gap analysis (ReAct)
- **Status**: ERROR
- **Timing**: 60.0s
- **Error**: `ReadTimeout: 
Traceback (most recent call last):
  File "/Users/caspar/Coding_Projects/vc-agent/venv/lib/python3.14/site-packages/httpx/_transports/default.py", line 101, in map_httpcore_exceptions
    yield
  File "/Users/caspar/Coding_Projects/vc-agent/venv/lib/python3.14/site-packages/httpx/_transports/default.py", line 394, in handle_async_request
    resp = await self._pool.handle_async_request(req)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/caspar/Coding_Projects/`

### ✅ 2.11: Elastro — legacy .doc file (ReAct)
- **Status**: PASS
- **Timing**: 71.3s
- **Steps**: 6
- **Response length**: 2698 chars
- **Step trace**:
  1. Queued...
  2. Model running (may take a while)...
  3. Reading Data Room/Elastro Data Room/Evidences/2. Agreements Regarding Securities/2a. Equity Incentiv
  4. Searching 'Stock Incentive Plan'...
  5. Reading Data Room/Elastro Data Room/Evidences/Data Room/2. Agreements Regarding Securities/2a. Equit
  6. Done
- **Response preview**: Based on the **Elastro, Inc. 2025 Stock Incentive Plan**, here is a summary of the key terms:  ### 1. Plan Size & Eligibility * **Share Pool:** 1,500,000 shares of Common Stock are reserved for issuance. Returned or canceled shares go back into the pool. * **Eligible Participants:** Employees, offic
- **Notes**: Legacy .doc extracted via LibreOffice conversion.

## Phase 3: Deep Agent

### ✅ 3.1: wayfarer — workspace browse (Deep Agent)
- **Status**: PASS
- **Timing**: 8.1s
- **Steps**: 4
- **Response length**: 311 chars
- **Step trace**:
  1. Queued...
  2. Model running (may take a while)...
  3. Browsing workspace tree...
  4. Done
- **Response preview**: The workspace currently contains the following files and folders:  **Folders:** - `Data Room/` - `Deliverables/`   - `Reports/` - `Inbox/`  **Files:** - `Data Room/wayfarer lab.pdf` (Pitch deck for Wayfarer Labs) - `Deliverables/Reports/risk_analyze.md` - `Deliverables/extract_info.json` - `WORKSPAC
- **Notes**: Deep Agent completed in 4 steps.

### ✅ 3.2: scenic — Red Team preset (Deep Agent)
- **Status**: PASS
- **Timing**: 94.4s
- **Steps**: 5
- **Response length**: 412 chars
- **Step trace**:
  1. Queued...
  2. Model running (may take a while)...
  3. Reading Data Room/Scenix Materials/Evidence/CEO Willian O’ Farrell’s Columbia Website Introduction_.
  4. Reading Data Room/Scenix Materials/Scenix - 200k.png...
  5. Done
- **Response preview**: {"_vc_chat": "artifact_card", "node_id": "401f7180-eed6-4470-81ce-f15261352918", "entity_id": "ec04890b-74cd-4019-857e-b27a31e36784", "preset_label": "Red team diligence", "deliverable_type": "report", "artifact_title": "risk_analyze", "version": 8, "status": "draft", "summary": "Created deliverable
- **Notes**: Deep Agent red team: 412 chars in 5 steps.

## Phase 4: Process Inbox

### ✅ 4.1: All entities — description coverage
- **Status**: PASS
- **Timing**: 0.0s
- **Response preview**:   Cybernexus: 37/37 files have descriptions   scenic: 6/7 files have descriptions   Elastro: 42/43 files have descriptions   Abinitia Labs: 13/15 files have descriptions   wayfarer: 2/4 files have descriptions
- **Notes**: Overall: 100/106 files have descriptions.

### ✅ 4.2: Cybernexus — legal binder structure
- **Status**: PASS
- **Timing**: 0.0s
- **Response preview**: Found: ['CyberNexus Series Pre-A Closing Binder', '1. Transaction Documents', '2. Group Resolutions', '3. Ancillary Documents'] Missing: []
- **Notes**: All 4 expected nested folders present. Total nodes: 91.

### ✅ 4.3: Elastro — data room taxonomy
- **Status**: PASS
- **Timing**: 0.0s
- **Response preview**: Found sections: ['1. Basic Corporate Documents', '2. Agreements Regarding Securities', '11. Business Plan and Financial Information', '12. Product'] Missing: []
- **Notes**: All 4 checked sections present. Total nodes: 151.

## Bugs Found

1. **[2.6] Abinitia Labs — Red Team preset (ReAct)**: ReadTimeout: 
Traceback (most recent call last):
  File "/Users/caspar/Coding_Projects/vc-agent/venv/lib/python3.14/site-packages/httpx/_transports/default.py", line 101, in map_httpcore_exceptions
  
2. **[2.9] Elastro — data room gap analysis (ReAct)**: ReadTimeout: 
Traceback (most recent call last):
  File "/Users/caspar/Coding_Projects/vc-agent/venv/lib/python3.14/site-packages/httpx/_transports/default.py", line 101, in map_httpcore_exceptions
  
