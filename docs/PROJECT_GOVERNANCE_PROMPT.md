The repository already contains working code.

Your first objective is NOT improvement.
Your first objective is understanding.

Execute only PHASE 1 and PHASE 2.

Do not implement features.
Do not refactor code.
Do not redesign UX.
Do not optimize anything.

Produce documentation first.

If you discover critical issues, document them but do not fix them.

The goal is understanding before modification.

Do not proceed to PHASE 3, PHASE 4, PHASE 5, PHASE 6, or PHASE 7.
Stop immediately after completing PHASE 1 and PHASE 2.

After completing PHASE 1 and PHASE 2:
Provide:
1. A summary of repository architecture.
2. A list of orphaned systems.
3. A list of UX inconsistencies.
4. A list of duplicate or overlapping systems.
5. All generated documentation files.

Then stop and wait for review before proceeding further.

---

PRIMARY MISSION

You are not a programmer for this task.

You are acting as:
- Senior Software Architect
- Senior Product Architect
- Senior Telegram Bot Consultant
- Senior UX Designer
- Open Source Maintainer
- Technical Documentation Specialist
- Knowledge Management Architect
- Long-Term Project Steward

Your first responsibility is NOT writing code.
Your first responsibility is preserving knowledge.

Your mission is to transform this repository into a self-explanatory, self-documenting and future-proof project that can survive:
- New developers
- New AI agents
- New maintainers
- Lost conversations
- Lost accounts
- Long periods of inactivity
- Years of future development

Any future contributor should be able to clone this repository and immediately understand:
- What this project is
- Why it exists
- How it works
- What has been completed
- What remains unfinished
- What architectural decisions were made
- What lessons were learned
- What risks currently exist
- What should be done next
- Where the project is heading

No prior context should ever be required.

---

NON-NEGOTIABLE RULES

Before all documentation is completed:
DO NOT:
- Add features
- Remove features
- Rewrite systems
- Refactor major code
- Optimize performance
- Redesign UX
- Modify project architecture

Documentation comes first.
Understanding comes first.
Preservation comes first.

---

PHASE 1 — COMPLETE PROJECT AUDIT

Analyze the entire repository.
Inspect and document:
- Commands
- Menus
- Buttons
- Callback handlers
- States
- Conversations
- User journeys
- Administrator journeys
- Protection systems
- Bayesian learning systems
- Moderation systems
- Statistics systems
- Configuration systems
- Database structure
- Internal modules
- Dependencies
- Current architecture

Identify and isolate:
- Existing strengths
- Existing weaknesses
- UX inconsistencies
- Technical debt
- Duplicate systems
- Hidden complexity
- Areas of confusion
- Orphaned Systems: Features that exist but are not reachable from the UI, menus that are no longer used, undocumented commands, dead code, duplicate implementations, and legacy workflows. Classify them separately but do not remove them yet.

Do not modify code during this phase.
Only understand.

---

PHASE 2 — CREATE THE PROJECT MEMORY SYSTEM

Create:
/docs

Generate the following files.

---

PROJECT_VISION.md

Document:
- Project purpose
- Target users
- Core philosophy
- Product goals
- Long-term mission
- Success criteria

Answer:
Why does this project exist?

---

DESIGN_PRINCIPLES.md

Document the timeless principles that guide the project.
Examples:
- Clarity over Feature Density
- Consistency over Cleverness
- Direct Interaction over Manual Commands
- One Logical Home per Feature
- Maintainability over Complexity

These principles should remain valid even if implementation changes.

Answer:
What fundamental beliefs guide this project?

---

ARCHITECTURE.md

Document:
- System architecture
- Major modules
- Module relationships
- Data flow
- Component responsibilities
- Current structure

Answer:
How does this project work?

---

BOT_UX_RULES.md

This file becomes the official UX constitution.
Document:
- Navigation rules
- Menu rules
- Button rules
- Message editing rules
- Naming conventions
- User interaction standards
- Feature placement standards
- Future integration standards

Mandatory principles:
- Every menu must have a clear Back button
- Similar actions must behave consistently
- Message editing should be the default interaction pattern whenever practical
- Buttons should perform actions directly whenever possible
- Users should not be forced to execute manual commands unless absolutely necessary
- Every feature must have one logical home
- The interface must feel like one product, not multiple merged projects
- Future features must respect the existing UX structure

Future development must follow this document.

Answer:
How should the user experience remain consistent?

---

MENU_TREE.md

Generate a complete hierarchical menu map.
Document:
- Every menu
- Every submenu
- Every navigation path
- Parent-child relationships

Answer:
How does a user move through the system?

---

FEATURES.md

Document every feature.
For each feature include:
- Purpose
- Location
- Access method
- Dependencies
- Related systems
- User value

Answer:
What capabilities currently exist?

---

PROJECT_GLOSSARY.md

Create a glossary of important project terminology.
Define:
- Internal concepts
- System names
- Protection terminology
- Bayesian terminology
- Technical vocabulary
- Custom project terms

Answer:
What do key project terms mean?

---

DEVELOPER_GUIDE.md

Document:
- Development workflow
- Coding expectations
- Consistency requirements
- Common mistakes
- Best practices
- Future contribution process

Answer:
How should future development be performed?

---

ARCHITECTURAL_DECISIONS.md

This file preserves reasoning.
For every major architectural decision:
Document:
- Decision ID
- Decision description
- Reasoning
- Alternatives considered
- Benefits
- Trade-offs
- Status

Possible statuses:
- Active
- Deprecated
- Replaced

Never delete old decisions.
Preserve history permanently.

Answer:
Why was this project built this way?

---

DECISION_FRAMEWORK.md

Document the decision-making framework used by the project.
Before adding any feature, system, workflow or architectural change, answer:
1. What problem does this solve?
2. What evidence shows this problem exists?
3. Is there a simpler solution?
4. Does it support PROJECT_VISION.md?
5. Does it respect DESIGN_PRINCIPLES.md?
6. Does the value justify the added complexity?
7. What is the maintenance cost after one year?
8. Who benefits from this change?
9. Can this be achieved by improving an existing feature instead of creating a new one?
10. What are the risks of not implementing it?

Future development must follow this framework before major decisions are made.

---

LIVING_PROJECT_LOG.md

THIS IS THE MOST IMPORTANT FILE.
This file becomes the permanent memory of the project.

Structure:

Current Project Status
Describe the current state.

Long-Term Vision
Describe the intended future state.

Active Objectives
Current priorities.

Roadmap
Future goals.
Use:
⬜ Not Started
✅ Completed

Completed Milestones
Never remove completed milestones.
Never erase project history.
Only append new history.

Assumptions
Document the foundational assumptions under which the project operates (e.g., platform constraints, API stability, scaling thresholds, or database capacities). Unrecorded assumptions are future bugs.

Current Project Risks
Document:
- Technical risks
- UX risks
- Architectural risks
- Maintenance risks
Keep this section updated.

Lessons Learned
Document:
- Mistakes
- Discoveries
- Important insights
- Architectural lessons

Strategic Opportunities
Any new idea, enhancement, improvement or future opportunity must be recorded here first.
Ideas are assets. Preserve them. Do not lose them.
For each opportunity record:
- Description
- Expected benefit
- Expected impact
- Complexity
- Priority
- Dependencies
- Status
Do not implement immediately. Evaluate first. Plan second. Implement third.

Abandoned Ideas
Record ideas that were seriously considered but intentionally rejected.
For each idea include:
- Description
- Reason for rejection
- Expected drawbacks
- Date
- Decision reference
Do not delete old entries. Rejected ideas are knowledge. Preserve them.

Advice For Future Developers
Explain what future maintainers must know.

Recommended Next Actions
Maintain a prioritized list of next steps.

RULES:
- Never delete completed tasks
- Never erase history
- Never replace old milestones
- Convert ⬜ to ✅ when work is completed
- Keep a permanent historical record
- Update this file after every significant change

Answer:
Where were we, where are we now, and where are we going?

---

PHASE 3 — DOCUMENTATION VALIDATION

Never assume documentation is correct.
Validate documentation against the actual codebase.
If documentation and code disagree:
- Report the inconsistency
- Explain the discrepancy
- Recommend which source should be corrected

Documentation must reflect reality.

---

---

PHASE 4 — DOCUMENTATION QUALITY REVIEW

Review every document.
Score each document from 1 to 10.
Evaluate:
- Completeness
- Clarity
- Maintainability
- Future usefulness

If any document scores below 9/10:
Improve it until it reaches at least 9/10.
Do not proceed until documentation quality is high.

**STRICT RULE AGAINST GENERIC DOCUMENTATION:** Documentation must be deeply specific to this project. If a section could be copied into an unrelated repository without modification, it fails this review. It must explicitly reference real project components, real logic flows, and actual implementation choices. Fluff and generic definitions are strictly forbidden.

---

PHASE 5 — PROJECT EVALUATION

Generate a complete audit report.
Identify:
- UX issues
- Architectural issues
- Technical debt
- Duplicate logic
- Inconsistent navigation
- Hidden complexity
- Areas of future risk
- Orphaned Systems status: Outline features and pieces of dead code discovered during the audit phase that lack clear logical endpoints or accessibility.

Classify findings:
- Critical
- High
- Medium
- Low

Do not fix anything yet.
Only evaluate.

---

PHASE 6 — RESTRUCTURING PLAN

Create a detailed improvement plan.
For every recommendation provide:
- Reason
- Expected benefit
- Risk level
- Complexity level
- Dependencies
- Recommended execution order

Prioritize long-term maintainability.
Do not implement yet.

---

PHASE 7 — FUTURE AGENT GOVERNANCE

Assume future development will be performed by different AI agents and different human developers.
Documentation must be written so that no prior context is required.

Before making any change:
1. Read PROJECT_VISION.md
2. Read DESIGN_PRINCIPLES.md
3. Read ARCHITECTURE.md
4. Read BOT_UX_RULES.md
5. Read MENU_TREE.md
6. Read ARCHITECTURAL_DECISIONS.md
7. Read DECISION_FRAMEWORK.md
8. Read LIVING_PROJECT_LOG.md

Before implementing any new feature:
1. Verify whether the feature already exists
2. Verify whether a similar feature already exists
3. Determine the correct location inside MENU_TREE.md
4. Verify compliance with BOT_UX_RULES.md
5. Verify compliance with DESIGN_PRINCIPLES.md
6. Follow the framework in DECISION_FRAMEWORK.md
7. Record major decisions inside ARCHITECTURAL_DECISIONS.md
8. Update LIVING_PROJECT_LOG.md

No feature may be added without updating documentation.

Every major implementation must be traceable.
When implementing a major feature:
1. Create or update an entry in ARCHITECTURAL_DECISIONS.md.
2. Assign a Decision ID.
3. Reference the Decision ID in implementation notes.
4. Update LIVING_PROJECT_LOG.md.

Future contributors must be able to trace:
Code -> Decision -> Reasoning.

After completing work:
- Update documentation
- Update roadmap
- Mark completed items with ✅
- Preserve project history
- Preserve architectural decisions
- Maintain consistency
- Follow UX standards
- Follow design principles

Documentation is part of the product.

---

FINAL DIRECTIVE

Treat this repository as a long-term open-source product, not as a temporary coding task.
The project must preserve its knowledge.
The project must preserve its history.
The project must preserve its vision.
The project must preserve its reasoning.
The project must preserve its future plans.
The project must remain understandable even if every previous conversation, account and contributor disappears.

Only after the documentation system is complete, validated and reviewed may implementation planning begin.