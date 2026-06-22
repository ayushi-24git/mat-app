---
name: optum-marketing-automation
description: >
  End-to-end marketing automation skill for Optum Engage B2C campaigns. Covers
  the full workflow: reading the BRD from Google Drive, building Databricks SQL
  audience queries (email and direct mail), generating HTML approval reports,
  running ROI analysis, and updating Jira. Use this skill for any request
  involving: audience files, data pulls, member queries, OPM tickets,
  Valero/Nationwide/Eaton/Optum Engage campaigns, campaign delivery reports,
  ROI reports, or Jira workflow steps. Trigger on: "audience file", "data pull",
  "email query", "direct mail query", "OPM-", "campaign audience", "member file",
  "progress campaign", "incentive campaign", "ROI report", "delivery report",
  "approval report", or any request to pull members for a marketing deployment.
---

# Optum Marketing Automation Skill

## Purpose

Full end-to-end automation for Optum Engage B2C marketing campaigns. Covers:
1. **Audience query building** — production-ready Databricks SQL for email and direct mail files
2. **Sanity CMS config lookups** — employer IDs, affiliation IDs, plan dates, activity rules
3. **BRD intake** — read from Google Drive, extract all campaign parameters
4. **HTML approval reports** — inline-styled, Gmail-compatible, dual sign-off
5. **ROI analysis** — t=0 vs t+N KPI comparison per segment
6. **Jira workflow** — post-campaign comments, share files after approval

---

## ⚠️ Critical: MCP Connectivity Rule

**If any MCP tool is unavailable, not loading, or returning a connection error — STOP immediately and ask the user to reconnect the MCP. Do NOT attempt any alternative methods, workarounds, browser navigation, Jira attachments, or any other substitution.**

Example: If Google Drive MCP is not connected and you cannot search the Marketing BRD folder, say:
> "The Google Drive MCP doesn't appear to be connected. Can you please reconnect it so I can access the BRD?"

Then wait. Do not proceed until the user confirms the MCP is back.

---

## Workflow Overview

```
1. Read BRD from Google Drive (folder: 1WSBQ5c4iFjQlDNoQgQYGxlzxfNWUaE6l) using WF number
2. Extract key parameters from BRD
3. Look up client config in Sanity CMS (employer + affiliation docs)
4. Build the Databricks SQL query (email and/or direct mail)
5. Apply standard suppressions, pre-checks, and dedup rules
6. Generate HTML approval report (inline-styled, Gmail-compatible)
7. Post Jira comment (on hold pending approval)
8. After approval: upload to Drive, post folder link in Jira
```

---

## Jira Intake

**Intake mode: Manual** — a ticket number is always provided by the user or a team member. There is no automated pull.

### Steps

1. **Receive the Jira ticket number** from the user (e.g., `OPM-67`)
2. **Read the Jira ticket** → extract the **WF number** (e.g., `WF22031341`)
3. **Search Google Drive** Marketing BRD folder (`1WSBQ5c4iFjQlDNoQgQYGxlzxfNWUaE6l`) using the WF number:
   ```
   parentId = '1WSBQ5c4iFjQlDNoQgQYGxlzxfNWUaE6l' and title contains 'WF<NUMBER>'
   ```
4. **Read and understand the BRD** — extract all campaign parameters (see Step 1 below)
5. **Summarize the BRD** — after reading, present a short summary to the user covering:
   - Campaign type (e.g., progress, activity-completion, awareness)
   - Channel(s) (email, direct mail, or both)
   - Affiliations in scope
   - Key filters (relationship codes, age, geography, suppressions, activity/incentive logic if any)
   - Deployment dates

   Wait for the user to confirm the summary is correct before proceeding.

**End of intake.** Everything after (Sanity CMS lookup, query build) is the audience build phase.

> To add scheduled intake (e.g., 8 AM daily Jira poll), update this section.

---

## Step 1 — Read BRD from Google Drive

All BRDs are uploaded to:
**Google Drive folder ID:** `1WSBQ5c4iFjQlDNoQgQYGxlzxfNWUaE6l` (folder name: "Marketing BRD")

**Always search using the WF number from the Jira ticket:**
```
query: "parentId = '1WSBQ5c4iFjQlDNoQgQYGxlzxfNWUaE6l' and title contains 'WF<NUMBER>'"
```

Then use `Google Drive:read_file_content` on the matching file to extract:
- **Client name** (e.g., Valero)
- **Campaign type** (progress, registration, activity-completion, etc.)
- **Affiliations in scope** (e.g., affiliations 2–11)
- **Audience segment** (e.g., $0–$199 earned)
- **Channel(s)** (Email, Direct Mail, or both)
- **Deployment schedule** (dates per channel)
- **Suppressions** (age exclusions, opt-out rules, holdout, prior campaign exclusions, etc.)
- **Expected count**
- **Account codes / coverage type**

---

## Audience Builder

---

### Step 1 — Sanity CMS Lookup

Use `Sanity:query_documents` with:
```json
{
  "resource": {"projectId": "c4naai3b", "dataset": "production_20241231092151"}
}
```

#### 1a. Get employer IDs
```groq
*[_type == "employer" && name match "<ClientName>*" && !(_id in path("drafts.**"))]{
  _id, name, clientId, childOrgId, programId, campaignId, childProgramId
}
```
Key fields to extract: `clientId`, `childOrgId` — used to filter `member_dimension` by `org_id`.

#### 1b. Get affiliations in scope
```groq
*[_type == "affiliation"
  && employer->name == "<ClientName>"
  && !(_id in path("drafts.**"))
]{
  _id,
  name,
  "externalAffiliationId": externalAffiliationId.current,
  "employerClientId": employer->clientId,
  "childOrgId": employer->childOrgId,
  relationshipStatus,
  plans[]->{ planName, planStartDate, planEndDate, rewardMaxCap, rewardEarningEndDate,
             giftCardRunout, activityRunout }
} | order(name asc)
```

Key things to confirm from affiliation results:
- Which affiliation numbers are in scope (per BRD)
- `rewardMaxCap` → confirms the progress segment ceiling (e.g., $200 cap → $0–$199 segment)
- `rewardEarningEndDate` → confirm plans are still active (validated in query via `production_sanity_data.affiliation`)
- `award_media` → check if null at activity level (inherited from plan); plan name containing "GIFT" = Gift Card only

#### 1c. Affiliation Filter Optimisation — IN vs NOT IN

After retrieving all affiliations from Sanity and the inclusion list from the BRD, always use whichever list is shorter to minimise query size:

| Situation | Filter to use |
|---|---|
| Inclusion list is shorter | `affiliation_id IN ('02', '05', '07')` |
| Exclusion list is shorter | `affiliation_id NOT IN ('01')` |

**Decision rule:**
1. Count total affiliations from Sanity (e.g., 10)
2. Count affiliations the BRD says to include (e.g., 9)
3. Exclusion count = total − inclusion count (e.g., 1)
4. Use whichever filter list has fewer items

**Example A — exclusion list is shorter → use `NOT IN`**
- Client has affiliations 01–10 (10 total)
- BRD includes 02–10 (9 affiliations) → only 01 is excluded
- → `affiliation_id NOT IN ('01')`

**Example B — inclusion list is shorter → use `IN`**
- Client has affiliations 01–10 (10 total)
- BRD includes 02, 05, 07 (3 affiliations) → 7 are excluded
- → `affiliation_id IN ('02', '05', '07')`

---

### Step 2 — Build the Databricks SQL Query

#### Architecture Overview

| Table | Purpose |
|---|---|
| `production_sanity_data.affiliation` | Affiliation config + reward earning end date check |
| `production_sanity_data.activity` | Activity config — `external_activity_id`, `target_rule_id` |
| `optum_extracts.member_dimension` | Member eligibility (Type-2 SCD) |
| `optum_extracts.incentive_earning_detail` | Earnings — progress segment filter |
| `optum_prod_source_delta.target_loyalty__user_target` | Per-user activity completion status |
| `read_api_9000084.users` | Email opt-in check (email file only) |
| `optum_prod_source_delta.nsadmin__messages` | Prior campaign exclusion |
| `optum_prod_source_delta.veneno_data_details__control_group_users_history` | Prior campaign control group exclusion |

---

#### Critical Query Rules

1. **`member_dimension` is Type-2 SCD** — always filter `end_date = '9999-12-31'` for active record.

2. **org_id filter** — use `childOrgId` from Sanity (e.g., `org_id = 9000288` for Valero).

3. **Affiliation filter** — always use whichever list is shorter (see Step 1c for full decision logic):
   - If the inclusion list is shorter → `affiliation_id IN ('02', '05', '07')`
   - If the exclusion list is shorter → `affiliation_id NOT IN ('01')`
   - Compare counts: total affiliations from Sanity vs inclusions from BRD vs exclusions — pick the smaller set.

4. **NEVER apply `plan_start_date` directly on `member_dimension`** — the plan year is already controlled by `reward_earning_end_date >= CURRENT_DATE()` on the affiliation. Filtering `md.plan_start_date` is incorrect and will cause data loss for new hires and late enrollees.

5. **Test user exclusion** — always include:
   ```sql
   AND md.efid NOT LIKE '%test%'
   AND md.client_name NOT LIKE '%Pseudomenudo Test Client%'
   AND md.partner_name NOT LIKE '%pseudo_test%'
   ```

6. **Mandatory member_dimension filters for email files** — always include ALL of:
   ```sql
   AND md.status = 'ACTIVE'
   AND md.member_termination_date > CURRENT_DATE()
   AND md.email IS NOT NULL
   ```

7. **Email field name** — the email field in `member_dimension` is `email`, NOT `email_address`. Always use `md.email`. Never use `md.email_address`.

8. **Always SELECT `md.org_id`** in the members CTE — it is required for all `optum_prod_source_delta` joins downstream.

9. **BRD check-type determination** — during Jira intake when reading the BRD, always identify which of the three scenarios applies before building any query. This determines which tables are needed:

    | Scenario | Description | Tables needed |
    |---|---|---|
    | 1 — Simple filter pull | Audience based only on filters (affiliation, relationship code, gender, geography, etc.) | `member_dimension` only |
    | 2 — Total incentive check | Check whether members have met/crossed their overall incentive limit | `target_loyalty__user_target` + `primary_capping_target_rule_id` |
    | 3 — Specific activity completion | Check whether members have completed one or more specific activities (e.g. health survey, biometric screening) | `target_loyalty__user_target` OR `incentive_earning_detail` |

    **If Scenario 3:** look up the `external_activity_id` for each activity from Sanity CMS → present the list to the user for confirmation → proceed only after confirmation.

10. **Award media** — do NOT filter on `award_media` in earnings CTE; `awardMedia` is null at activity level in Sanity and inherited from plan. Filter is unnecessary when org has a single reward type.

11. **source_delta tables** — ALWAYS prefix with `optum_prod_source_delta.` — e.g., `optum_prod_source_delta.target_loyalty__user_target`. This is mandatory — NEVER use `source_delta.` alone. No exceptions.

12. **source_delta join keys** — when joining `optum_prod_source_delta.target_loyalty__user_target`, always join on ALL THREE of: `user_id`, `org_id`, AND `target_rule_id`. Never join on fewer.

13. **Three BRD scenarios — how to check each:**

    **Scenario 1 — Simple filter pull**
    No incentive or activity tables needed. Apply member_dimension filters only (relationship code, geography, age, affiliation, etc.).

    **Scenario 2 — Total incentive completion check**
    Use `optum_prod_source_delta.target_loyalty__user_target` with `primary_capping_target_rule_id`:
    - `primary_capping_target_rule_id` is NOT exposed in the Sanity MCP — always query `production_sanity_data.affiliation` directly in Databricks:
      ```sql
      SELECT external_affiliation_id, name, primary_capping_target_rule_id
      FROM production_sanity_data.affiliation
      WHERE employer_name = '<ClientName>'
        AND reward_earning_end_date >= CURRENT_DATE()
      ORDER BY name
      ```
    - The value may differ per affiliation group — always pull per-affiliation and include in the client affiliations CTE
    - Join on `user_id + org_id + target_rule_id`
    - `achieved_value >= target_value` = incentive limit met or crossed

    **Scenario 3 — Specific activity completion check**
    First, identify the `external_activity_id` for each activity from Sanity CMS. Present to user for confirmation before proceeding.

    Two valid methods to check completion — both are acceptable:

    **Method A — `target_loyalty__user_target`**
    - Get `target_rule_id` from `production_sanity_data.activity` filtered by `external_activity_id`
    - Join `optum_prod_source_delta.target_loyalty__user_target` on `user_id + org_id + target_rule_id`
    - `achieved_value >= target_value` = activity complete
    - `target_rule_id` may differ per affiliation for the same activity — always resolve per affiliation

    **Method B — `incentive_earning_detail`**
    - Check whether the `external_activity_id` is present as `activity_id` for that user in `optum_extracts.incentive_earning_detail`
    - **Presence alone = completed.** No need to check `activity_completion_date` or `award_date`
    - If no record exists for that user + activity_id → not completed
    - This is a source table check and is reliable
    - Use this method when a simpler existence check is preferred over joining through `target_rule_id`

14. **Affiliation ID resolution for activity lookup** — `production_sanity_data.affiliation` has two ID fields:
    - `external_affiliation_id` — the Sanity CMS slug (matches `member_dimension.affiliation_id`)
    - `affiliation_id` — internal platform ID (joins to `production_sanity_data.activity.affiliation_id`)
    - **Resolution chain:** `member_dimension.affiliation_id` = `production_sanity_data.affiliation.external_affiliation_id` → `production_sanity_data.affiliation.affiliation_id` = `production_sanity_data.activity.affiliation_id`
    - In the `activity_target_rules` CTE, always expose `external_affiliation_id` (not internal `affiliation_id`) so it can join back to `member_dimension.affiliation_id`

15. **No redundant `active_affiliations` CTE** — when a client-specific affiliation CTE already exists, do NOT create a separate `active_affiliations` CTE. Put `reward_earning_end_date >= CURRENT_DATE()` inside the client affiliation CTE and join `member_dimension` directly to it.

16. **Campaign exclusion pattern** — to exclude members who received a prior campaign, always UNION both tables:
    ```sql
    prior_campaign AS (
        SELECT DISTINCT user_id
        FROM optum_prod_source_delta.nsadmin__messages
        WHERE org_id = 9000084
          AND campaign_id IN (<campaign_id>)
        UNION
        SELECT DISTINCT user_id
        FROM optum_prod_source_delta.veneno_data_details__control_group_users_history
        WHERE org_id = 9000084
          AND campaign_id IN (<campaign_id>)
    )
    ```
    `org_id` is always `9000084` (UHG parent org) for both tables. Only `campaign_id` changes per campaign.

17. **Base table as temp view for multi-email campaigns** — when building campaigns with multiple email sends, always wrap the base table in `CREATE OR REPLACE TEMP VIEW <client>_<year>_base_table AS`. Individual email queries are built on top of this temp view.

18. **Multi-email overlap QA** — before unioning individual email temp views, always run overlap QA to confirm zero users appear in more than one email:
    ```sql
    SELECT COUNT(*) AS overlap_em1_em2
    FROM <client>_em1 em1
    INNER JOIN <client>_em2 em2 ON em1.user_id = em2.user_id;
    -- repeat for all pairs
    ```
    All overlap counts must be 0 before proceeding.

19. **Relationship code filter from BRD** — apply the filter on `md.relationship_code` in `member_dimension` based on BRD. Exact casing required:

    | BRD Value | `member_dimension` filter |
    |---|---|
    | Subscriber only | `AND md.relationship_code = 'Employee'` |
    | Spouse only | `AND md.relationship_code = 'Spouse'` |
    | Dependent only | `AND md.relationship_code = 'Dependent'` |
    | Subscriber + Spouse/Domestic Partner | **No filter needed** — covers all relationship types |
    | All three (Subscriber + Spouse + Dependent) | **No filter needed** |

    - `Subscriber` in BRD = `'Employee'` in DB (capital E)
    - `Spouse/Domestic Partner` (with slash) = covers all types → no filter needed

---

#### Knowledge Base

All campaign-specific learnings — especially activity-related fetching methods — are stored in `Campaign_Knowledge_Base.md` (same directory as this file), NOT in this master skill.

**Rules for the knowledge base:**
- **Always check it first** before building any query — look for a prior campaign of the same type
- **Activity-related details always go there** — how external activity IDs were fetched, how target rule IDs were resolved, any activity-specific logic. Three sources to toggle between for activity lookups:
  1. **CRD** — the BRD/campaign requirements document
  2. **Sanity CMS via MCP** — live config (employer, affiliation, activity docs)
  3. **Databricks `production_sanity_data`** — mirror of Sanity data in Databricks, queryable directly
  
  As more campaigns are fed in, patterns will emerge on exactly which source to use first — reducing the need to toggle between all three every time
- **Cross-client application is allowed** — if the method is exactly the same, it can be applied to a different client even if specific IDs differ
- **Auto-save rule** — during any audience pull, if something is campaign-specific rather than generic, save it to the knowledge base automatically
- **Strict match rule** — only apply knowledge base learnings when the campaign type and method are exactly the same. Never apply to a slightly similar campaign or a different client with different logic
- **Master skill stays generic** — only update this file when explicitly instructed

---

### Step 3 — Databricks Notebook Creation

After the query is built and confirmed, create a Databricks notebook to run it.

**Notebook naming convention:**
```
{OPM-ticket}_{WF-number}_{Full Campaign Name}
```
Example: `OPM-67_WF22031341_Valero_2026_Progress_Campaign`

**Two modes:**

**Mode A — Manual (UI-driven)**
Present the user with two options in the UI:
1. **Copy query** — user copies the query and pastes it into a Databricks notebook manually
2. **Create notebook in Databricks** — yes/no prompt. If yes, use **OAuth (U2M)** to authenticate and create the notebook directly in Databricks. No PAT required — OAuth handles authentication via browser flow.

**Mode B — Scheduled**
When running as a scheduled job, a **personal access token (PAT)** is required for Databricks to authenticate automatically without a browser flow. PAT must be configured before scheduling.

---

### Step 4 — Audience Output File Rules

**No PII fields** — the output SELECT must never include:
- Name (first, last, full)
- Date of birth
- Email address
- Phone number
- Address fields
- Gender

**Always include:**
- `user_id`
- `efid`
- `affiliation_id`
- `affiliation_description`

**BRD-scenario-specific columns** — add columns relevant to the campaign type so the file can also serve as the **T+0 baseline for the ROI report**:

| BRD Scenario | Additional columns to include |
|---|---|
| Simple filter pull | No additional columns needed |
| Incentive-based | `total_earned_usd` (amount earned at time of pull) — used to compare against post-campaign earnings in ROI |
| Activity-based | One column per activity (e.g., `health_survey_complete`) with value `Completed` or `Not Completed` at time of pull. **Also always include `total_earned_usd`** — incentive amount earned at T+0. Incentive completion is the ultimate ROI measure for ALL campaign types, including activity-based. Without this column, the incentive side of the ROI report cannot be computed. |

**Why:** The audience file pulled at T+0 is the baseline. The ROI report compares this snapshot against post-campaign data (T+N). Without these columns saved at pull time, the comparison cannot be made accurately.

---

## Standard CTE Structure — Simple Progress Campaign

```sql
CREATE OR REPLACE TEMP VIEW <client>_<year>_base_table AS

WITH email_optins AS (
    SELECT DISTINCT user_id
    FROM read_api_9000084.users
    WHERE subscription_status_email_bulk = 'OPTIN'
),

<client>_affiliations AS (
    -- Reward earning end date check lives here — no separate active_affiliations CTE needed
    SELECT
        aff.affiliation_id,            -- internal platform ID (joins to activity table)
        aff.external_affiliation_id    -- Sanity slug (matches member_dimension.affiliation_id)
    FROM production_sanity_data.affiliation aff
    WHERE aff.external_affiliation_id IN (
        '<external_affiliation_id_1>',  -- 02 <description>
        '<external_affiliation_id_2>',  -- 03 <description>
        ...
    )
    AND aff.reward_earning_end_date >= CURRENT_DATE()
),

<client>_members AS (
    SELECT
        md.user_id, md.efid, md.org_id,
        md.first_name, md.last_name, md.date_of_birth,
        md.email,                          -- NOT email_address
        md.affiliation_id, md.affiliation_description,
        md.terms_and_conditions_acceptance_date
    FROM optum_extracts.member_dimension md
    INNER JOIN <client>_affiliations ca ON md.affiliation_id = ca.external_affiliation_id
    INNER JOIN email_optins eo           ON md.user_id = eo.user_id
    WHERE
        md.org_id = <childOrgId>
        AND md.end_date = '9999-12-31'
        AND md.status = 'ACTIVE'
        AND md.member_termination_date > CURRENT_DATE()
        AND md.email IS NOT NULL
        -- NO plan_start_date filter here — reward_earning_end_date on affiliation controls plan year
        AND DATEDIFF(CURRENT_DATE(), md.date_of_birth) / 365.25 >= 18
        AND md.state NOT IN ('PR','GU','VI','AS','MP')
        AND md.efid NOT LIKE '%test%'
        AND md.client_name NOT LIKE '%Pseudomenudo Test Client%'
        AND md.partner_name NOT LIKE '%pseudo_test%'
),

<client>_earnings AS (
    SELECT
        ied.user_id,
        SUM(ied.awarded_amount) AS total_earned_usd
    FROM optum_extracts.incentive_earning_detail ied
    WHERE
        ied.user_id IN (SELECT user_id FROM <client>_members)
        AND ied.fulfillment_status != 'Void'
        AND ied.is_gatekeeper = 0
    GROUP BY ied.user_id
)

SELECT
    cm.user_id, cm.efid, cm.org_id,
    cm.first_name, cm.last_name, cm.date_of_birth,
    cm.email,
    cm.affiliation_id, cm.affiliation_description,
    cm.terms_and_conditions_acceptance_date,
    COALESCE(ce.total_earned_usd, 0) AS total_earned_usd
FROM <client>_members cm
LEFT JOIN <client>_earnings ce ON cm.user_id = ce.user_id;
```

---

## Standard CTE Structure — Activity Completion Base Table

Use this pattern when campaign logic requires per-activity completion flags (e.g., health survey, biometric screening, tobacco attestation).

```sql
CREATE OR REPLACE TEMP VIEW <client>_<year>_base_table AS

WITH email_optins AS (
    SELECT DISTINCT user_id
    FROM read_api_9000084.users
    WHERE subscription_status_email_bulk = 'OPTIN'
),

<client>_affiliations AS (
    SELECT
        aff.affiliation_id,
        aff.external_affiliation_id
    FROM production_sanity_data.affiliation aff
    WHERE aff.external_affiliation_id IN (
        '<external_affiliation_id_1>',
        '<external_affiliation_id_2>',
        ...
    )
    AND aff.reward_earning_end_date >= CURRENT_DATE()
),

activity_target_rules AS (
    -- Get target_rule_id per activity per affiliation
    -- Expose external_affiliation_id so it can join back to member_dimension.affiliation_id
    SELECT
        a.external_activity_id,
        ca.external_affiliation_id,
        a.target_rule_id
    FROM production_sanity_data.activity a
    INNER JOIN <client>_affiliations ca ON a.affiliation_id = ca.affiliation_id
    WHERE a.external_activity_id IN (
        '<EXTERNAL_ACTIVITY_ID_1>',
        '<EXTERNAL_ACTIVITY_ID_2>',
        ...
    )
),

<client>_members AS (
    SELECT
        md.user_id, md.efid, md.org_id,
        md.first_name, md.last_name, md.date_of_birth,
        md.email,
        md.affiliation_id, md.affiliation_description,
        md.terms_and_conditions_acceptance_date
    FROM optum_extracts.member_dimension md
    INNER JOIN <client>_affiliations ca ON md.affiliation_id = ca.external_affiliation_id
    INNER JOIN email_optins eo           ON md.user_id = eo.user_id
    WHERE
        md.org_id = <childOrgId>
        AND md.end_date = '9999-12-31'
        AND md.status = 'ACTIVE'
        AND md.member_termination_date > CURRENT_DATE()
        AND md.email IS NOT NULL
        AND DATEDIFF(CURRENT_DATE(), md.date_of_birth) / 365.25 >= 18
        AND md.state NOT IN ('PR','GU','VI','AS','MP')
        AND md.efid NOT LIKE '%test%'
        AND md.client_name NOT LIKE '%Pseudomenudo Test Client%'
        AND md.partner_name NOT LIKE '%pseudo_test%'
),

activity_status AS (
    SELECT
        nm.user_id,

        -- Activity 1 (e.g., Health Survey)
        MAX(CASE WHEN lut_1.achieved_value >= lut_1.target_value THEN 1 ELSE 0 END) AS <activity_1>_complete,
        MAX(atr_1.target_rule_id)                                                    AS <activity_1>_target_rule_id,
        MAX(atr_1.external_activity_id)                                              AS <activity_1>_external_activity_id,

        -- Activity 2 (e.g., Biometric Screening)
        MAX(CASE WHEN lut_2.achieved_value >= lut_2.target_value THEN 1 ELSE 0 END) AS <activity_2>_complete,
        MAX(atr_2.target_rule_id)                                                    AS <activity_2>_target_rule_id,
        MAX(atr_2.external_activity_id)                                              AS <activity_2>_external_activity_id

        -- ... repeat for each activity

    FROM <client>_members nm

    -- Activity 1
    LEFT JOIN activity_target_rules atr_1
        ON nm.affiliation_id = atr_1.external_affiliation_id
        AND atr_1.external_activity_id = '<EXTERNAL_ACTIVITY_ID_1>'
    LEFT JOIN optum_prod_source_delta.target_loyalty__user_target lut_1
        ON nm.user_id = lut_1.user_id
        AND nm.org_id = lut_1.org_id
        AND atr_1.target_rule_id = lut_1.target_rule_id

    -- Activity 2
    LEFT JOIN activity_target_rules atr_2
        ON nm.affiliation_id = atr_2.external_affiliation_id
        AND atr_2.external_activity_id = '<EXTERNAL_ACTIVITY_ID_2>'
    LEFT JOIN optum_prod_source_delta.target_loyalty__user_target lut_2
        ON nm.user_id = lut_2.user_id
        AND nm.org_id = lut_2.org_id
        AND atr_2.target_rule_id = lut_2.target_rule_id

    -- ... repeat for each activity

    GROUP BY nm.user_id
)

SELECT
    nm.user_id, nm.efid, nm.org_id,
    nm.first_name, nm.last_name, nm.date_of_birth,
    nm.email,
    nm.affiliation_id, nm.affiliation_description,
    nm.terms_and_conditions_acceptance_date,

    COALESCE(act.<activity_1>_complete,              0) AS <activity_1>_complete,
    act.<activity_1>_target_rule_id,
    act.<activity_1>_external_activity_id,

    COALESCE(act.<activity_2>_complete,              0) AS <activity_2>_complete,
    act.<activity_2>_target_rule_id,
    act.<activity_2>_external_activity_id

    -- ... repeat for each activity

FROM <client>_members nm
LEFT JOIN activity_status act ON nm.user_id = act.user_id
ORDER BY nm.user_id;
```

---

## Campaign Exclusion Pattern

To exclude members who received a prior campaign, always use BOTH tables with UNION:

```sql
prior_campaign AS (
    SELECT DISTINCT user_id
    FROM optum_prod_source_delta.nsadmin__messages
    WHERE org_id = 9000084         -- always 9000084 (UHG parent org), never changes
      AND campaign_id IN (<campaign_id>)
    UNION
    SELECT DISTINCT user_id
    FROM optum_prod_source_delta.veneno_data_details__control_group_users_history
    WHERE org_id = 9000084
      AND campaign_id IN (<campaign_id>)
)
-- Then LEFT JOIN and filter: WHERE prior_campaign.user_id IS NULL
```

---

## Email File Rules

- **Pre-check 1:** Client affiliation CTE join with `reward_earning_end_date >= CURRENT_DATE()`
- **Pre-check 2:** `INNER JOIN email_optins` on `user_id`
- **Mandatory member filters:** `status = 'ACTIVE'`, `member_termination_date > CURRENT_DATE()`, `email IS NOT NULL`
- **Dedup:** 1 record per unique email address
  ```sql
  email_dedup AS (
      SELECT *, ROW_NUMBER() OVER (
          PARTITION BY TRIM(email)
          ORDER BY user_id
      ) AS email_rank
      FROM audience
      WHERE email IS NOT NULL AND TRIM(email) != ''
  )
  -- Final SELECT: WHERE email_rank = 1
  ```
- **Columns:** `user_id, efid, first_name, last_name, date_of_birth, email, affiliation_id, affiliation_description, terms_and_conditions_acceptance_date` + campaign-specific fields

---

## Direct Mail File Rules

- **Pre-check 1:** Client affiliation CTE join with `reward_earning_end_date >= CURRENT_DATE()`
- **No email opt-in check** for direct mail
- **Dedup:** 1 record per household using ALL address fields combined with `COALESCE(TRIM(...), '')` to normalize NULLs
  ```sql
  mail_dedup AS (
      SELECT *, ROW_NUMBER() OVER (
          PARTITION BY
              COALESCE(TRIM(address_line_1), ''),
              COALESCE(TRIM(address_line_2), ''),
              COALESCE(TRIM(city), ''),
              COALESCE(TRIM(state), ''),
              COALESCE(TRIM(zip_code), ''),
              COALESCE(TRIM(country), '')
          ORDER BY user_id
      ) AS household_rank
      FROM audience
      WHERE address_line_1 IS NOT NULL AND TRIM(address_line_1) != ''
  )
  -- Final SELECT: WHERE household_rank = 1
  ```
- **Columns:** `user_id, efid, first_name, last_name, date_of_birth, address_line_1, address_line_2, city, state, zip_code, country, affiliation_id, affiliation_description, terms_and_conditions_acceptance_date` + campaign-specific fields

---

## Multi-Email Campaign Pattern (QA + Union)

```sql
-- STEP 1: QA — all overlaps must be 0 before proceeding
SELECT COUNT(*) AS overlap_em1_em2
FROM <client>_em1 em1 INNER JOIN <client>_em2 em2 ON em1.user_id = em2.user_id;

SELECT COUNT(*) AS overlap_em1_em3
FROM <client>_em1 em1 INNER JOIN <client>_em3 em3 ON em1.user_id = em3.user_id;

SELECT COUNT(*) AS overlap_em2_em3
FROM <client>_em2 em2 INNER JOIN <client>_em3 em3 ON em2.user_id = em3.user_id;

-- Row counts per email
SELECT 'EM1' AS email, COUNT(*) AS total FROM <client>_em1
UNION ALL SELECT 'EM2', COUNT(*) FROM <client>_em2
UNION ALL SELECT 'EM3', COUNT(*) FROM <client>_em3;

-- STEP 2: Final union view (only after all overlaps = 0)
CREATE OR REPLACE VIEW optum_marketing.<client>_<year>_<campaign> AS
SELECT *, 'EM1' AS email_campaign FROM <client>_em1
UNION ALL
SELECT *, 'EM2' AS email_campaign FROM <client>_em2
UNION ALL
SELECT *, 'EM3' AS email_campaign FROM <client>_em3;

-- Preview
SELECT email_campaign, COUNT(*) AS total
FROM optum_marketing.<client>_<year>_<campaign>
GROUP BY email_campaign;
```

---

## Key Data Sources Reference

| Table | Purpose |
|---|---|
| `optum_extracts.member_dimension` | Member eligibility, demographics, affiliation (Type-2 SCD — filter `end_date = '9999-12-31'`). Email field is `email` (NOT `email_address`). Always select `org_id`. Mandatory filters: `status = 'ACTIVE'`, `member_termination_date > CURRENT_DATE()`, `email IS NOT NULL`. NEVER filter on `plan_start_date` directly. |
| `optum_extracts.incentive_earning_detail` | Earnings by member. Exclude `fulfillment_status = 'Void'` and `is_gatekeeper = 1`. |
| `production_sanity_data.affiliation` | Affiliation config — `reward_earning_end_date`, `external_affiliation_id` (matches `member_dimension.affiliation_id`), internal `affiliation_id` (joins to `activity` table), `primary_capping_target_rule_id` (for cap completion checks — Scenario A). **Note:** `primary_capping_target_rule_id` is NOT exposed in the Sanity MCP; always query this table directly in Databricks. |
| `production_sanity_data.activity` | Activity config — `external_activity_id`, internal `affiliation_id`, `target_rule_id`. Target rule ID may differ per affiliation for the same activity. |
| `optum_prod_source_delta.target_loyalty__user_target` | Per-user activity completion — join on `user_id + org_id + target_rule_id`. Complete if `achieved_value >= target_value`. |
| `read_api_9000084.users` | Email opt-in status (`subscription_status_email_bulk = 'OPTIN'`). |
| `optum_prod_source_delta.nsadmin__messages` | Prior campaign send log — use with `org_id = 9000084` for campaign exclusions. |
| `optum_prod_source_delta.veneno_data_details__control_group_users_history` | Prior campaign control group — use with `org_id = 9000084` for campaign exclusions. Always UNION with `nsadmin__messages`. |

---

## Valero-Specific Config (OPM-44 Reference)

| Field | Value |
|---|---|
| `clientId` | `0907816` |
| `childOrgId` | `9000288` |
| `programId` | `19` |
| `campaignId` | `7973` |
| Plan year | 2026 |
| `rewardMaxCap` | $200 (all affiliations 02–11) |
| `rewardEarningEndDate` | `2026-12-31` (all affiliations 02–11) |
| Award media | Gift Card only (inherited from plan; `GIFT` in all plan names) |
| Affiliation 01 | **Excluded** — "No Rewards Plan" for 2026 |
| Affiliations in scope | 02–11 |

### Valero Affiliation Map (2026)

| Aff # | Name | Carrier | Relationship |
|---|---|---|---|
| 02 | Valero UHC EE | UHC | EE |
| 03 | Valero UHC SPEE/DPEE | UHC | SP/DP |
| 04 | Valero BCBS SD Wellmark EE GIFT | Wellmark | EE |
| 05 | Valero BCBS SD Wellmark SPEE DPEE GIFT | Wellmark | SP/DP |
| 06 | Valero BCBS TN EE GIFT | BCBS TN | EE |
| 07 | Valero BCBS TN SPEE DPEE GIFT | BCBS TN | SP/DP |
| 08 | Valero Cigna EE GIFT | Cigna | EE |
| 09 | Valero Cigna SPEE DPEE GIFT | Cigna | SP/DP |
| 10 | Valero Kaiser EE GIFT | Kaiser | EE |
| 11 | Valero Kaiser SPEE DPEE GIFT | Kaiser | SP/DP |

### OPM-44 Campaign Rules

- **Segment:** Progress — members who have earned $0–$199 (not yet at $200 max cap)
- **Email deployments:** March 3, May 5, July 7, September 1
- **Mail deployment:** June 2 (one per household)
- **Age suppression:** Exclude members under 18
- **Geography:** US 50 states only (exclude PR, GU, VI, AS, MP)
- **Email opt-in:** Required (`subscription_status_email_bulk = 'OPTIN'`)

---

## Nationwide-Specific Config (OPM-50 Reference)

| Field | Value |
|---|---|
| `childOrgId` | `97000388` |
| `clientId` | `0715014` |
| `programId` | `19` |
| Plan year | 2026 |
| `rewardMaxCap` | $100 (EREWD — employer-paid reward dollars) |
| `rewardEarningEndDate` | `2026-10-31` (affiliations 02–05, 07–08) |
| Affiliations excluded | 01 (No Rewards), 06 (Med Waive/No Rewards) |
| Affiliations in scope | 02, 03, 04, 05, 07, 08 |

### Nationwide Affiliation Map (2026)

| Aff # | External Affiliation ID | Description |
|---|---|---|
| 02 | `optum_nationwide_my_health_aff_201512341_173019_ecz8` | UHC EE |
| 03 | `optum_nationwide_my_health_aff_201512341_173118_uro3` | UHC SP |
| 04 | `optum_nationwide_my_health_aff_20161219_132728_le2g` | NON UHC EE |
| 05 | `optum_nationwide_my_health_aff_20161219_132728_glzz` | NON UHC SP |
| 07 | `optum_nationwide_my_health_aff_07_-_nationwide_non-uhc_surest_ee_erewd` | SUREST EE |
| 08 | `optum_nationwide_my_health_aff_08_-_nationwide_non-uhc_surest_sp_erewd` | SUREST SP |

### Nationwide 2026 My Health Activity Map

| Activity Code | External Activity ID | Description |
|---|---|---|
| R9 | `OPTUM.HEALTHSURVEY.COMPLETE` | Health Survey (gatekeeper) |
| S3 | `OPTUM.BIOSCREEN.COMPLETE` | Biometric Screening |
| B9 | `OPTUM.BIOMETRIC.LDL.COMPLETE` | LDL Goal |
| B5 | `OPTUM.BIOMETRIC.BLOODGLUCOSE.COMPLETE` | Blood Glucose Goal |
| B26 | `OPTUM.BLOODPRESSURE.COMPLETE` | Blood Pressure Goal |
| F16 | `TOBACCO.ANY` | Tobacco-Free Attestation |

> B9, B5, B26 are grouped — completion of ANY ONE = `biometric_targets_complete = 1`

### OPM-50 Campaign Rules

- **EM1 (Tobacco Attestation):** Health survey complete + tobacco NOT complete. Biometric screening and biometric targets can be either. Exclude EM2 audience.
- **EM2 (New Hire):** Not in Feb campaign (campaign_id: `51882`). Not completed ALL 4 steps. Saved as temp view `nationwide_2026_em2`.
- **EM3 (Unengaged):** Zero activities completed across all 4 steps. Exclude EM2 audience.
- **Final view:** `optum_marketing.nationwide_2026_my_health`

---

## Delivery Rules

- **Email only** — only build email audience queries. Even if the BRD mentions a Direct Mail (DM) component, skip it entirely. DM will be handled in a future phase.
- For multi-email campaigns, build and confirm the base table temp view first, then build each email query one at a time
- Always confirm with the user before running — these are production audience files

---

## Before Starting Any Query — Summarize Steps First

When asked to build a query for any OPM ticket, **do NOT immediately start reading files**. First present a short summary of the steps you plan to take:

1. Read the Jira ticket to get the WF number
2. Search for the BRD in the Marketing BRD folder using the WF number
3. Read the BRD to extract client, affiliations, relationship codes, channel, and suppressions
4. Look up client config in Sanity (employer + affiliations)
5. Check if the BRD requires any activity or incentive completion logic — if yes, ask which approach to use (`target_loyalty__user_target` vs `incentive_earning_detail`); if no, skip straight to building
6. Build the query

**Wait for the user's go-ahead before proceeding.**

---

## Eaton-Specific Config (OPM-53 Reference)

| Field | Value |
|---|---|
| `clientId` | `0914680` |
| `childOrgId` | `9000095` |
| `programId` | `19` |
| `campaignId` | `7973` |
| Plan year | 2026 |
| `rewardMaxCap` | $900 (affiliations 02–05, 09) |
| `rewardEarningEndDate` | `2026-12-31` (affiliations 02–05, 09) |
| Affiliations excluded | 01 (UHC DP NONE), 06 (EXPAT NONE), 07 (RETIREE NONE), 10 (HAWAII NONE) |
| Affiliations in scope | 02, 03, 04, 05, 09 |

### Eaton Affiliation Map (2026)

| Aff # | External Affiliation ID | Description |
|---|---|---|
| 02 | `optum_eaton_corp_aff_20181228_164843_tzrv` | UHC EE |
| 03 | `optum_eaton_corp_aff_20181228_164843_dacn` | UHC SP |
| 04 | `optum_eaton_corp_aff_20181228_164843_qkjd` | NON UHC EE |
| 05 | `optum_eaton_corp_aff_20181228_164843_ret6` | NON UHC SP |
| 09 | `optum_eaton_corp_aff_09_-_eaton_-_bind_ee_sp_erepr` | SUREST EE SP |

### OPM-53 Campaign Rules

- **Campaign:** Sweepstakes awareness — quarterly emails + flyer
- **Channel:** Email + Collateral (flyer)
- **Audience:** Subscriber + Spouse/Domestic Partner = all relationship types → no `relationship_code` filter
- **Affiliations:** 02, 03, 04, 05, 09
- **Age suppression:** Exclude under 18
- **Geography:** US 50 states only
- **No activity/incentive completion logic** — pure awareness send
- **No campaign exclusion lookback**
- **Expected count:** ~30,000

---

## Approval Gate

### What triggers it
Audience build complete → generate HTML approval report (KPIs only — no query) → export as PDF → email both to recipients → wait for reply.

### HTML Report Content
- Audience summary KPIs only (counts, key metrics based on BRD scenario)
- **No query** included in the report
- Two signatories: Stacy Swicegood (visible approver) + Souradeep Bhattacharjee (Data Analyst)
- Gmail-compatible inline styles (see HTML Approval Report Standards below)

### Email Delivery
- Send via Gmail
- Email body: HTML report pasted inline (Ctrl+A → Ctrl+C from browser into Gmail compose)
- Attachment: PDF version of the same HTML report (so recipients can download and share)
- Default recipient: **Stacy Swicegood**
- UI allows adding/editing recipients (comma-separated)

### Approvers
| Name | Role | Visible in report/email |
|---|---|---|
| Stacy Swicegood | Marketing Manager / Reviewer | Yes |
| Souradeep Bhattacharjee | Backend approver (testing only) | No — reply from Souradeep also triggers next process |

### Approval Monitoring

**Scheduled flow:** Poll Gmail every 30–60 minutes, matching on **subject line only** — do not scan the general inbox. Subject line format:
```
[APPROVAL REQUIRED] OPM-{ticket} | {Client} {Year} {Campaign} — Audience Review
```
Only threads with this exact subject pattern are scanned for replies.

Scan reply body for keywords:

| Keyword(s) | Action |
|---|---|
| "approved", "looks good" | Mark as approved → proceed to next steps |
| "rejected" | Stop — do nothing for now (future: Slack alert) |
| "redo", "re-pull", "rework" | Flag for re-pull on specified date — T-pull flow (to be detailed later) |
| No reply | Mark master spreadsheet column "Approved?" as "Not yet" — keep polling |

**Approval email must include reply instructions** — always add this block at the bottom of the email body:
> *To respond, please reply to this email with one of the following:*
> - **APPROVED** — audience file is approved for deployment
> - **REJECTED** — audience file is rejected, no further action
> - **REDO** — audience file needs to be re-pulled (please specify the date)

**Manual flow (UI):** User clicks "Mark as Approved / Rejected" in the UI after seeing the reply. Simpler but not suitable for scheduled flow.

**Future:** When deployed as a full tool, approvers log in directly, see the HTML report + PDF, and approve/reject in-app. No email parsing needed.

### Rejection Flow
- Current: stop, no further action
- Future: send Slack alert to notify the team

### Redo / Re-pull Flow
- If reply contains redo/rework keywords → flag the ticket for a re-pull on the date specified in the reply
- T-pull concept — detailed flow to be defined later

### Master Spreadsheet
One row per campaign. See `## Master Spreadsheet — Scheduler` section below for full schema. The scheduler reads this sheet to drive approval polling and T+1/T+2 monitoring auto-send.

---

## Jira Workflow — Post-Pull Actions

### When reviewer approval is still pending (default state after a pull)
- **Do NOT** post audience summary, counts, or file links in Jira.
- Post a single short comment (1–2 lines): ticket is on hold, pending reviewer approval.
  > "Audience pull for [campaign] is complete. On hold — pending reviewer approval before files are shared.
  > *Comment added by marketing automation tool.*"
- Save HTML report + local audience CSVs locally. Do NOT create the Drive campaign folder yet.

### Approval process
- Approval is via **Gmail only** using the HTML approval report
- Send the HTML report as both:
  1. **Pasted HTML body** — copy from browser (Ctrl+A → Ctrl+C) into Gmail compose
  2. **PDF attachment** — download the HTML as PDF and attach to the same email so recipients can share it with other parties without needing a browser

### When user explicitly gives approval to share
1. **Create a campaign subfolder** inside the "Audience Files Complete" parent folder (ID: `1ECKNeNIUQ9AlXySSrJvXE_ObMPeuclw-`).
   - Folder naming: `{WF#} | {CampaignName} | OPM-{ticket}`
2. **Upload the audience CSV files** (summary — no PII) into that subfolder, one file per channel.
3. **Post in Jira**: Share only the campaign **folder link** (not individual file links). One short comment:
   > "Approved audience files for [campaign]: [folder link]
   > *Comment added by marketing automation tool.*"
   No summary tables, no counts, no breakdowns.

---

## Monitoring

### Purpose
Track campaign delivery performance after campaign launch. A single HTML delivery report is generated on demand and emailed — it always reflects data from the launch date to the report date.

### Trigger

The report is generated **on demand at any point after launch** — there is no fixed T+1/T+2 schedule in the manual flow:
- The user enters the campaign ID and launch date, then clicks **Generate Report**
- The tool calculates **Day N** automatically: N = report date − launch date
- The report is labelled `Day N Report (as of [date])` based on when it is run
- The query always fetches from the launch date to the current date — later runs include more data

The launch date must be recorded in the master spreadsheet at the time of audience approval — this is the only reference for T+0. No launch date = no monitoring.

### Data source

All delivery metrics are pulled from Databricks using the `read_api_9000084` schema. Six tables are joined:

| Table | Purpose |
|---|---|
| `read_api_9000084.contact_info` | One row per user per campaign event — the join hub (aliased `a`) |
| `read_api_9000084.campaigns` | Campaign metadata — `campaign_id`, `name` |
| `read_api_9000084.date` | Date dimension — resolves `dim_event_date_id` to a calendar date |
| `read_api_9000084.campaign_delivery_status` | Status label — Delivered / Opened / Clicked / Invalid / Not-Applicable (column: `campaign_legend_lebel`) |
| `read_api_9000084.communication_channel` | Channel type — Email, SMS, etc. |
| `read_api_9000084.campaign_group` | Group classification — TEST vs CONTROL (left join; `group_type` column) |

> **Note:** The status column is spelled `campaign_legend_lebel` (typo in the source table — use as-is).

**Reference base query:**
```sql
SELECT
    a.dim_event_user_id               AS user_id,
    x.campaign_legend_lebel           AS delivery_status,
    cc.channel,
    cg.group_type,
    campaign_campaigns.name           AS campaign_name,
    campaign_campaigns.campaign_id,
    d.date
FROM read_api_9000084.contact_info AS a
INNER JOIN read_api_9000084.campaigns AS campaign_campaigns
    ON a.dim_campaign_id = campaign_campaigns.campaign_id
JOIN read_api_9000084.date d
    ON d.date_id = a.dim_event_date_id
JOIN read_api_9000084.campaign_delivery_status AS x
    ON a.dim_campaign_delivery_status_id = x.status_id
JOIN read_api_9000084.communication_channel cc
    ON cc.id = a.dim_communication_channel_id
LEFT JOIN read_api_9000084.campaign_group cg
    ON a.dim_campaign_group_id = cg.id
WHERE campaign_campaigns.campaign_id = <campaign_id>
  AND d.date >= '<launch_date>'
```

**Metric derivation from the base query:**
- `group_type = 'TEST'` → Sent group (funnel metrics apply)
- `group_type = 'CONTROL'` (or NULL — left join miss) → Control group (excluded from funnel rates)
- `d.date >= launch_date` ensures all data from launch date onwards is included — later runs automatically cover more days
- Count distinct `user_id` per `delivery_status` value to compute Sent, Delivered, Opened, Clicked, Invalid, Not-Applicable totals

### Metric definitions

**Campaign Send Overview (full audience):**

| Metric | Definition |
|---|---|
| Total Audience | All records in the campaign system for this campaign ID |
| Sent (TEST Group) | Emails the system attempted to send — excludes control group and unsubscribed |
| Control Group | Members intentionally held out from this send to measure incremental lift |
| Invalid Emails | Send attempted but hard-bounced (bad email address) |

**Delivery Funnel (TEST group only — control group excluded from all rates):**

| Metric | Formula |
|---|---|
| Delivered | Count of users with status Delivered OR Opened OR Clicked (cumulative — higher status implies delivery) |
| Opened | Count of users with status Opened OR Clicked (cumulative — clicked implies opened) |
| Clicked | Count of users with status Clicked |
| Delivery Rate | Delivered ÷ Sent × 100 |
| Open Rate | Opened ÷ Delivered × 100 |
| Click Rate | Clicked ÷ Delivered × 100 |
| Click-to-Open Rate | Clicked ÷ Opened × 100 |

**NOT-APPLICABLE Breakdown:**
- All NOT-APPLICABLE users must belong to the CONTROL group
- Verify via `campaign_group` table: `dim_campaign_group_id` = control group value
- `unsubscription_status` should be `NOT_YET` for all — they were held out, not opted out

### Report structure
The HTML delivery report follows the template established in `Campaign_52136_Delivery_Report.html`:

1. **Header** — dark navy (`#0a2540`) background, campaign WF name, campaign ID badge, launch date badge, generated date badge
2. **Metric note banner** (green) — explains cumulative bucketing and control group exclusion from funnel rates
3. **Day N block** — single section labelled `Day N Report (as of [date])`:
   - Campaign Send Overview — 4 KPI cards (Total Audience, Sent TEST, Control Group, Invalid)
   - Delivery Funnel — 4 KPI cards (Delivered, Opened, Clicked, Click-to-Open Rate)
   - NOT-APPLICABLE breakdown — 1 KPI card (Control Group) + purple control group verification note
4. **Daily Breakdown table** — Date | Sent | Delivered | Opened | Clicked | Invalid | Not-Applicable
5. **Status & Bucket Legend**
6. **Sign-off** — Data Analyst: Souradeep Bhattacharjee | Marketing Manager: Stacy Swicegood
7. **Footer** — `read_api_9000084` schema, pull timestamp, campaign ID, control group verification note

### Email delivery
- **Transport:** Gmail (same as Approval Gate — smtplib + App Password from `.env`)
- **Subject line:** `[DELIVERY REPORT Day {N}] OPM-{ticket} | {Client} {Year} {Campaign}`
- **Body:** HTML report inline
- **Recipients:** same as Approval Gate (default: Stacy Swicegood)

### Scheduled flow (future)
Once the master spreadsheet and scheduler are enabled, the tool can auto-generate reports at configured day intervals (e.g., Day 1, Day 2, Day 7). Each scheduled run generates a single Day N snapshot for that specific day and sends it automatically.

### Manual flow (current)
User opens **Page 4 — Monitoring** in the Streamlit app, enters the campaign ID and launch date, clicks **Generate Delivery Report**. Day N is calculated automatically from today. The user can generate and send on any day after launch.

---

## ROI

### Purpose

Compare T+0 audience baseline against post-campaign data. Calculates incentive completion lift, bucket migration, and incremental impact.

**All campaign types include incentive analysis** — even activity-based campaigns — because incentive completion is the ultimate measure of programme impact. The activity section answers "did members take the action?" while the incentive section answers "did it move the needle on their overall earnings?"

### Trigger

- **Scheduler (future):** Auto-generated at T+15 (configurable per campaign in master spreadsheet)
- **App (current):** On-demand via **Page 5 — Post-Campaign ROI** — user enters Campaign ID, launch date, and campaign type, then clicks Generate

### Campaign type handling

| Campaign Type | Activity section | Incentive section |
|---|---|---|
| Incentive-based (Progress) | N/A | Delivery + completion change + bucket distribution + migration matrix |
| Incentive-based (Completion) | N/A | Delivery + before/after completion counts only |
| Activity-based | Activity completion change (T+0 vs T+N per activity) | Full incentive report — always included |
| Simple Filter | N/A | Delivery metrics only |

### T+0 baseline

The T+0 baseline is captured **from the audience file saved at pull time**. Required columns — always stored at pull regardless of campaign type:

| Column | Required for | Notes |
|---|---|---|
| `total_earned_usd` | All campaign types | Incentive amount earned at T+0. Without this, the incentive ROI section cannot be computed. |
| `health_survey_complete` (or equivalent activity column) | Activity-based only | Value: `Completed` or `Not Completed` at T+0 |

> **If `total_earned_usd` is missing from the saved audience file, the ROI incentive section cannot be computed.** This is why it must always be included in the audience pull — even for activity-based campaigns.

### T+N user population

**Never re-run the base audience query for T+N.** The base query uses Sanity config (affiliations, dates, caps) that may have changed since T+0, which would contaminate the population. Instead, use the campaign delivery system as the fixed source of truth.

```sql
-- T+N user population (TEST + CONTROL) — scoped to the exact people in this campaign
SELECT DISTINCT
    a.dim_event_user_id AS user_id,
    COALESCE(cg.group_type, 'CONTROL') AS group_type
FROM read_api_9000084.contact_info AS a
LEFT JOIN read_api_9000084.campaign_group cg
    ON a.dim_campaign_group_id = cg.id
WHERE a.dim_campaign_id = <campaign_id>
```

Join these `user_id` values back to the incentive / activity tables to get T+N status.

### T+N data sources

| Data needed | Source | Filter |
|---|---|---|
| Current incentive completion % (`total_earned_usd` at T+N) | `production_sanity_data.incentive_earning_detail` | Scoped to T+N user IDs from campaign delivery query |
| Activity completions (activity-based) | Databricks activity table (same external activity IDs used at T+0) | Scoped to T+N user IDs |

### Incentive bucket definition

Buckets reflect where users sit in their incentive completion journey. **There are no default buckets.** The bucket structure is always determined by what that specific campaign was targeting — it is defined in the BRD and stored in the Campaign Knowledge Base after the first pull for each campaign type.

**Do not assume any bucket structure.** Always check the Campaign Knowledge Base for a prior matching campaign (same client + campaign type) before defining buckets. If no prior entry exists, read the BRD to determine the correct ranges.

**Example — Valero 2026 Progress Campaign (0–99% targeting):**

| Bucket | Range |
|---|---|
| 0% | 0% earned |
| 1–25% | > 0% and ≤ 25% |
| 26–50% | > 25% and ≤ 50% |
| 51–75% | > 50% and ≤ 75% |
| 76–99% | > 75% and < 100% |
| 100% | 100% earned (max cap reached) |

This specific structure applies to this campaign only — it targeted users at 0–99% completion. A different campaign (e.g. targeting only 51–99%) would have different buckets.

### Report structure

The ROI report follows the template established in `Campaign_52136_ROI_Report.html`:

1. **Header** — dark navy (`#0a2540`) background, campaign WF name, campaign ID badge, T+N label, generated date badge
2. **Delivery summary** — mirrors the monitoring report KPI card layout (Sent, Delivered, Opened, Clicked, Control, Invalid)
3. **Incentive completion change** — T+0 vs T+N comparison:
   - Overall, TEST group, and CONTROL group shown side-by-side
   - Incremental lift = TEST delta − CONTROL delta
   - Users who crossed 100% shown separately (TEST and CONTROL)
4. **Bucket distribution** — T+0 vs T+N count and % per bucket (Progress campaigns only)
5. **Migration matrix** — where each T+0 bucket's users moved by T+N (Progress campaigns only)
6. **Activity completion** (Activity-based campaigns only) — T+0 vs T+N per activity, TEST vs CONTROL
7. **Sign-off** — Data Analyst: Souradeep Bhattacharjee | Marketing Manager: Stacy Swicegood
8. **Footer** — data sources, pull timestamp, T+N label, campaign ID

### Notebook integration

After computing T+N values, **save the T+N query back to the original campaign Databricks notebook**. This keeps all data logic for the campaign in one place and makes future ROI re-runs fully reproducible without needing to reconstruct the population logic.

### Ask MAT — additional metrics

If the standard report doesn't cover a needed metric (e.g. gift card redemptions, click-to-activity conversion, redemption rate by affiliation), the user types a plain-English question into the **Ask MAT** input box. MAT will:

1. Identify the relevant table from this skill or the Campaign Knowledge Base
2. Generate a query scoped to the T+N user IDs as a CTE anchor
3. Present the query for review before running

Once the user confirms the query is correct, **save the metric and query approach immediately to the Campaign Knowledge Base** using the ROI Entry Template.

### Self-learning rule

Any non-standard metric, custom bucket definition, unusual T+N scoping, or non-standard data source discovered during ROI must be saved to the Campaign Knowledge Base immediately after confirmation. Use the **ROI Entry Template**. Do not wait until the end of the session.

### Email

| Field | Value |
|---|---|
| Subject | `[ROI REPORT T+{N}] Campaign {cid} \| {Client} {Campaign}` |
| Body | Full HTML ROI report (inline) |
| Recipients | Same as monitoring — default: Stacy Swicegood |

---

## Master Spreadsheet — Scheduler (Google Sheets)

### Purpose
Central tracking layer for the **scheduled / automated flow only** — not used by the manual Streamlit app. The scheduler reads and writes this sheet to manage campaign progress, approval polling, and automated monitoring reports.

**Storage:** Google Sheets (accessed via `gspread` Python library)
**Scope:** Optum campaigns only
**Sheet name:** Campaign Tracker
**Spreadsheet ID:** `1Mbui5tDyXPkYdrbIg7EdiCeFifN1Vsdj24S-FMK4MiI`
**URL:** https://docs.google.com/spreadsheets/d/1Mbui5tDyXPkYdrbIg7EdiCeFifN1Vsdj24S-FMK4MiI/edit
**Drive folder:** `1u9ThOs5dLR7eHhk1WdS8gU_QafjnVj_y`

### Schema — one row per campaign

#### Block 1 — Identification
| Column | Written by | Notes |
|---|---|---|
| `opm_ticket` | Scheduler / App | Primary lookup key — e.g. `OPM-67` |
| `wf_number` | Scheduler / App | e.g. `WF22031341` |
| `client` | BRD | e.g. `Valero` |
| `campaign_name` | BRD | e.g. `2026 Progress Campaign` |
| `campaign_type` | BRD | Simple / Incentive-based / Activity-based |
| `channel` | BRD | Email / DM / Both |
| `brd_link` | Google Drive | URL of the BRD file — used by scheduler for reference |
| `intake_date` | Auto | Timestamp when row was created by the scheduler |

#### Block 2 — Audience Build
| Column | Written by | Notes |
|---|---|---|
| `notebook_link` | Databricks API | Full URL to the Databricks notebook |

#### Block 3 — Approval
| Column | Written by | Notes |
|---|---|---|
| `approval_subject_line` | App / Scheduler | Exact subject line sent — used by Gmail polling to find the right thread |
| `approval_sent_at` | App / Scheduler | Timestamp; polling starts ~1 hour after this |
| `approval_status` | Scheduler | `Not Yet` → `Approved` / `Rejected` / `Redo` |
| `approval_updated_at` | Scheduler | Updated on every status change |
| `approval_recipients` | App / Scheduler | Comma-separated; default: `stacy.swicegood@optum.com` |

#### Block 4 — Monitoring
| Column | Written by | Notes |
|---|---|---|
| `campaign_id` | App / Scheduler | Numeric ID in `read_api_9000084` (e.g. `52136`) |
| `launch_date` | BRD / App | T+0 anchor — required before T+1/T+2 automation can run |
| `t1_report_sent_at` | Scheduler | Filled after Day 1 report is sent |
| `t2_report_sent_at` | Scheduler | Filled after Day 2 report is sent |

#### Block 5 — ROI
| Column | Written by | Notes |
|---|---|---|
| `roi_report_generated_at` | App / Scheduler | Timestamp when ROI report was generated |

#### Block 6 — Meta
| Column | Written by | Notes |
|---|---|---|
| `row_created_at` | Auto | First write |
| `last_updated_at` | Auto | Updated on any write to this row |
| `created_by` | Auto | Gmail sender identity from `.env` |

### Scheduler sweeps

**Sweep 1 — Approval polling (every 30–60 min)**
1. Read all rows where `approval_status = "Not Yet"` and `approval_sent_at` is set
2. Search Gmail for the exact `approval_subject_line` stored in that row — no general inbox scan
3. Scan reply body for keywords: `APPROVED` / `REJECTED` / `REDO`
4. Update `approval_status` and `approval_updated_at`

**Sweep 2 — Monitoring auto-send (daily check)**
1. Read all rows where `launch_date` is set
2. If today ≥ T+1 AND `t1_report_sent_at` is blank → generate + send Day 1 report → write timestamp
3. If today ≥ T+2 AND `t2_report_sent_at` is blank → generate + send Day 2 report → write timestamp

> **Separation of concerns:** The manual Streamlit app uses a separate SQLite database (`campaigns.db`). The Google Sheet is for the scheduler only. The two systems do not share storage. See `Marketing_Automation_UI.md` for the app's storage design.

---

## HTML Approval Report Standards

### Approval Sign-off Section
Always use exactly **two signatories** — no more, no less:

| Role | Name |
|---|---|
| Data Analyst | Souradeep Bhattacharjee |
| Marketing Manager / Reviewer | Stacy Swicegood |

Do NOT add Consultant, Approval Date, or any other sign-off boxes.

### KPI Section
- Always use an **inline-styled HTML table** (not CSS Grid) for KPI cards — required for Gmail copy-paste compatibility
- 3 columns × 2 rows layout
- Card padding: `11px 12px`
- Number font size: `22px`
- Label/note font size: `10px`
- Border-spacing: `6px`
- Left border colors: teal `#007B8F` (default), amber `#F57F17` (warn), green `#2E7D32` (ok), red `#C62828` (critical)

### Email Compatibility
- KPI cards must use inline styles only — Gmail strips `<style>` blocks and CSS Grid on paste
- All other sections (tables, alerts, query blocks) render correctly via copy-paste from browser into Gmail
- User copies from Chrome (Ctrl+A → Ctrl+C) and pastes into Gmail compose body

---

## Shared Drive Folders

| Folder | ID |
|---|---|
| Audience Files Complete (approved files, per-campaign subfolders) | `1ECKNeNIUQ9AlXySSrJvXE_ObMPeuclw-` |
| Schneider campaign working folder | `1Tl9erfAySSAAO5X_Yl6BFSnLXTUsJmgz` |
| Marketing BRD folder | `1WSBQ5c4iFjQlDNoQgQYGxlzxfNWUaE6l` |

---

## File Naming Convention

### Local files
- HTML report: `OPM-{ticket}_{Client}_{Year}_{Campaign}_Audience_Report.html`
- Email CSV (no PII): `OPM-{ticket}_{Client}_{Year}_{Campaign}_EM{n}_Audience.csv`
- DM CSV (PII): `OPM-{ticket}_{Client}_{Year}_{Campaign}_DM{n}_Audience.csv`
- Save location: `C:\Users\souradeep.bhattachar\Documents\Claude\Audience Builder\`

### Google Drive files (summary — no PII, uploaded only after approval)
Stored inside: `{WF#} | {CampaignName} | OPM-{ticket}` subfolder

File naming depends on number of email sends in the campaign:

| Scenario | File name |
|---|---|
| Single email send | `OPM-{ticket}_{Client}_{Year}_{Campaign}_Email_Audience_Summary.csv` |
| Multiple sends (EM1, EM2, EM3…) | `OPM-{ticket}_{Client}_{Year}_{Campaign}_EM1_Audience_Summary.csv`, `_EM2_…`, etc. — one file per send |
| Subset of sends (e.g. EM2 + EM3 only) | `OPM-{ticket}_{Client}_{Year}_{Campaign}_EM2EM3_Audience_Summary.csv` |

---

## Audience File Column Specifications

### Email files — NO PII
Only include these columns:
- `user_id`
- `efid`
- `affiliation_id` (2-char code derived from affiliation_description)
- `affiliation_description`
- Activity columns if applicable (e.g. `terms_and_conditions_acceptance_date`)
- **Nothing else** — no name, DOB, email address, address, gender, phone

### Direct Mail files — Full PII, exact aliases required
Always use this exact SELECT with these column aliases:
```sql
md.user_id AS Indv_ID,
md.first_name AS FrstName,
md.last_name AS LastName,
md.address_line_1 AS Addr1,
md.address_line_2 AS Addr2,
md.city AS City,
md.state AS State,
md.postal_code AS Zip,
md.mobile_phone_number AS MobilePhoneNumber,
md.gender AS Patient_Gender,
md.date_of_birth AS Patient_DOB,
md.email AS Email,
CONCAT(COALESCE(md.first_name,''),' ',COALESCE(md.last_name,'')) AS FullName,
md.group_policy_number AS GroupPolicyNumber,
CONCAT(COALESCE(md.address_line_1,''),' ',COALESCE(md.address_line_2,''),', ',COALESCE(md.city,''),', ',COALESCE(md.state,''),' ',COALESCE(md.postal_code,''),', ',COALESCE(md.country,'')) AS FullAddress,
md.member_effective_date AS MemberEffectiveDate,
md.member_termination_date AS MemberTerminationDate,
md.population_id AS PopulationID,
md.client AS Client,
md.client_id AS ClientID,
md.client_name AS ClientName,
md.affiliation_id AS AffiliationID,
md.affiliation_description AS AffiliationDescription,
md.incentive_indicator AS IncentiveIndicator
```


---

## HTML Creative QA Validation

### Purpose

Validate the developer-built HTML email against the customer-approved PDF and Figma design before send. Catches Outlook Classic rendering issues, padding drift, colour palette deviations, missing alt text, broken links, dynamic-token issues, and per-section visual mismatches.

**This is a parallel concern, not a serial step.** It runs independently of audience-build status. HTML development happens in parallel with audience pulling; QA does not wait on audience approval.

### When it runs

Triggered manually by a marketer from a standalone sidebar page (not numbered). Whenever an HTML candidate is ready — early dev iteration, post-dev handoff, post-fix re-QA — they upload it and run QA.

### Inputs

| Input | Required | Source |
|---|---|---|
| HTML file | Yes | Uploaded by the marketer every run |
| Reference folder | Yes | Picked from a dropdown of subfolders under the base Drive `assets/` directory. Sticky: persists across sessions via `db.py`. |
| Jira ticket URL | No | Optional. Enables QA-result writeback (comment + status transition). Skipped silently if blank. |

### Pipeline (4 composite skills)

| Stage | Sub-tasks | Wall-clock |
|---|---|---|
| **ingest** | Jira fetch (MCP/REST), Drive folder pull (rclone/Drive API), W-code parsing, file classification, campaign-meta synthesis | ~3-10 sec |
| **pre-flight** | Style audit (palette/font/tokens/size), compat check (Outlook Classic / Gmail / Outlook New — 31 rules), link QA — all parallel | ~0.1 sec |
| **visual-validation** | PDF → PNG, Playwright render, pixel diff, per-section crops, Claude-vision per-section review (×12 parallel) | ~35-45 sec |
| **report-and-writeback** | Aggregate findings, render HTML report, post Jira comment, transition status | ~0.5 sec |

Total wall-clock per run: ~40-55 seconds with vision, ~4-15 seconds without.

### Output

Each run produces:

- `opmXX_reports/qa_report.html` — self-contained HTML report (marketer-readable), matching the OPM-67 sample-report convention
- JSON artifacts under the campaign's working directory (`campaigns/<slug>/qa/*.json`) for downstream consumption
- Jira comment + status transition if a ticket URL was provided

### Decision matrix

| Aggregate findings | Decision | Jira transition (OPM project) |
|---|---|---|
| Any critical | **MUST FIX** | "In Dev" (transition id 2) |
| Zero critical, ≥1 major or minor | **READY WITH WARNINGS** | "QA Approved for Release" (transition id 6) |
| Zero findings | **READY TO SEND** | "QA Approved for Release" (transition id 6) |

### Suppressions (codified false positives)

The pipeline suppresses findings matching `/view_in_browser/i`. This token is an ESP-side macro Capillary's ESP fills at send time; rules that flag its presence/absence are noise.

Extend `ALWAYS_SUPPRESS` in `email_qa/scripts/pre-flight.mjs` to add more patterns. Each entry should have a comment explaining *why* it's suppressed.

### Architecture references

Authoritative specs live in `email_qa/skills/`:

- `email-qa.md` — master orchestrator (public entry point)
- `ingest.md` — assemble campaign folder from HTML + Drive + Jira
- `pre-flight.md` — composite static analysis
- `visual-validation.md` — composite visual checks
- `report-and-writeback.md` — report HTML + Jira writeback

Pipeline code (Node.js) lives in `email_qa/scripts/`. Streamlit integration is not yet implemented; see `email_qa/INTEGRATION.md` for the three runtime-integration paths (A: Python rewrite, B: multi-runtime container, C: separate service — recommended).

### Provenance

Built and maintained in `ayushi-24git/optum-email-pipeline`. Mirrored here for integration into the broader Marketing Automation pipeline.
