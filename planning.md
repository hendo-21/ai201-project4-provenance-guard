## What Provenance Guard Does

Provenance Guard is a backend system that any creative sharing platform could plug into to classify submitted content, score confidence in that classification, surface a transparency label to users, and handle appeals from creators who believe they've been misclassified.

---

## Architecture

```
User submits text (frontend)
      │
      ▼
POST /submit  {"user_id": str, "text": str}
      │
      ▼
rate_limiter  ←── keyed on user_id, 429 Too Many Requests if exceeded
      │
      ▼
parse_request()  ←── extracts user_id and text from JSON body
      │
      ├──▶ signal_1_llm()        ←── Groq llama-3.3-70b-versatile prompt
      │         │                     returns float 0.0–1.0 (weight: 0.65)
      │
      ├──▶ signal_2_stylometric() ←── pure Python heuristics
      │         │                     returns float 0.0–1.0 (weight: 0.35)
      │
      ▼
combine_scores()  ←── weighted average
      │                 score = (0.65 * s1 + 0.35 * s2) / (0.65 + 0.35)
      │
      ▼  confidence: float 0.0–1.0
      │
      ▼
generate_label()  ←── maps score to label variant
      │                 0.0–0.4 → "high-confidence human"
      │                 0.4–0.7 → "uncertain"
      │                 0.7–1.0 → high-confidence AI"
      │
      ├──▶ log_decision()  ←── side effect: writes to SQLite audit log
      │                         stores: content_id, user_id, text, s1, s2,
      │                         combined score, label, timestamp
      │
      ▼
POST /submit response  {"content_id": str, "confidence": float, "label": str}
      │
      ▼
Label displayed to user + appeal button shown (frontend)
      │
      ▼  [if user appeals]
      │
POST /appeal  {"content_id": str, "user_id": str, "appeal_text": str}
      │
      ▼
store_appeal()  ←── writes appeal to SQLite, sets status → "under_review"
      │
      ├──▶ log_decision()  ←── side effect: appends appeal event to audit log
      │
      ▼
POST /appeal response  {"message": "Your appeal has been received and will be reviewed."}
      │
      ▼
Confirmation message displayed to user (frontend)
```

A user submits their text and user ID to the `/submit` endpoint, where `Flask-Limiter` first checks whether they've exceeded their request quota before any classification work begins. If the request is allowed through, the text is analyzed by two independent signals, an LLM semantic analysis via Groq and a set of pure Python stylometric heuristics, whose scores are combined into a single confidence float using a weighted average that favors the LLM signal. That confidence score is mapped to one of three transparency labels, the full decision is written to the SQLite audit log, and the response is returned to the frontend for display.

---

## API Surface

### POST /submit
Accepts a piece of text content for attribution analysis.

**Request body:**
```json
{
    "user_id": "string",
    "text": "string"
}
```

**Response:**
```json
{
    "content_id": "string",
    "confidence": 0.0,
    "label": "string"
}
```

**Errors:**
- `400` — missing or malformed request body
- `429` — rate limit exceeded

---

### POST /appeal
Submits an appeal against an existing attribution decision.

**Request body:**
```json
{
    "content_id": "string",
    "user_id": "string",
    "appeal_text": "string"
}
```

**Response:**
```json
{
    "appeal_id": "string",
    "message": "Your appeal has been received and will be reviewed."
}
```

**Errors:**
- `400` — missing or malformed request body
- `404` — content_id not found

---

### GET /log
Returns the audit log. 

**Response:**
```json
[
    {
        "content_id": "string",
        "user_id": "string",
        "text": "string",
        "s1_score": 0.0,
        "s2_score": 0.0,
        "confidence": 0.0,
        "label": "string",
        "status": "string",
        "timestamp": "string"
    }
]
```

---

## Detection Signals

#### Signal 1: LLM Inference:

An LLM will be used to pattern match the input text against its own training corpus. AI writing tends to stylistic and structural regularities that it should be able to detect with a system prompt.  No explanation is required or requested; the signal will just return the float. An example of a system prompt is below.

**What it measures:** The semantic and stylistic properties of the text as assessed by a large language model. This signal leverages the LLM's internalized knowledge of how AI-generated and human-authored text differ in ways that are difficult to enumerate explicitly. It measures these stylistic and structural regularities in the input text and returns a single float value between 0 and 1 representing the text's similarity to AI writing patterns. The response is parsed and validated; if the output is not a clean float in range, the pipeline falls back to a neutral 0.5.

**Output:** Float 0.0–1.0. 0.0 = confidently human-authored, 1.0 = confidently AI-generated.

**Weight in combined score:** 0.65

```
# System prompt:

You are an AI authorship classifier. 
Analyze the following text and return only a single float between 0.0 and 1.0 representing the likelihood it was AI-generated. 
0.0 = human-authored, 1.0 = AI-generated. 
Return the number only, no other text.
```

#### Signal 2: Stylometric Heuristics:

**What it measures:** Surface-level statistical properties of the text that differ systematically between human and AI writing. Rather than relying on a single metric, this signal aggregates five heuristics into a single score, each normalized to 0.0–1.0 and averaged.

**Heuristics used:**
| Heuristic | What it captures | AI tendency | Human tendency |
|---|---|---|---|
| Sentence length variance | Consistency of sentence length throughout the text | Low variance, uniform rhythm | High variance, erratic rhythm |
| Type-token ratio (TTR) | Vocabulary diversity relative to text length | Lower TTR, repetitive vocabulary | Higher TTR, diverse vocabulary |
| Punctuation density | Frequency of punctuation characters relative to total characters | Conservative, sparse punctuation | Expressive, varied punctuation |
| Paragraph length consistency | Consistency of paragraph length throughout the text | Even paragraph lengths | Uneven, idea-driven paragraph lengths |
| Function word frequency | Distribution of common function words (the, and, of, that) | Characteristic AI distribution | Stable but distinct human distribution |

**Weight in combined score**: 0.35

#### Combining Outputs

The two signal scores are combined using a weighted average that favors the LLM signal, which is assumed to be the more accurate classifier, given it operates on meaning and not statistics, and has seen enourmous amounts of both human and AI-generated writing:

```
confidence = (0.65 * s1 + 0.35 * s2) / (0.65 + 0.35)
```

If the submitted text is under 150 words, the combined score is dampened toward 0.5 to reflect reduced statistical reliability at short lengths:

```
confidence = 0.5 + (raw_confidence - 0.5) * (word_count / 150)
```

This ensures the system expresses genuine uncertainty on short texts rather than producing a confidently wrong label.

---

## Uncertainty Representation

As described in the previous section, single signal ouputs will be combined using a weighted average which favors the LLM signal. Additional dampening of the weighted average towards uncertainty will be applied to short (< 150 words) text to account for the limitations of both signals on short text. Labels are assigned to the following ranges for this single weighted average:

| Range | Label |
|---|---|
| 0.0 - 0.4 | high-confidence human |
| 0.4 - 0.7 | uncertain |
| 0.7 - 1.0 | high-confidence AI |

Example: a weighted average signal score of 0.6 would sit just to the right of the midpoint of the "uncertain" range. This means that the system is leaning towards "high-confidence AI", but ultimately does not have enough confidence to push it into that domain, so it stays in "uncertain." The threshold for "high-confidence AI" is set conservatively at 0.7 to reduce false positives.

---

## Transparency Label Design

Labels are displayed to the reader alongside submitted content. Each label consists of an icon, a tier name, and a confidence qualifier. A legend appears below all labels.

**Legend (displayed beneath every label):**
*"Labels are generated automatically and may not be accurate. Creators may appeal any label using the button below."*

---

### Label Variants

| Score Range | Icon | Tier Name | Confidence Qualifier | Full Label Text |
|---|---|---|---|---|
| 0.00–0.20 | ✅ | Likely Human-Written | high confidence | ✅ Likely Human-Written · high confidence |
| 0.20–0.40 | ✅ | Likely Human-Written | moderate confidence | ✅ Likely Human-Written · moderate confidence |
| 0.40–0.70 | ⚠️ | Authorship Uncertain | — | ⚠️ Authorship Uncertain |
| 0.70–0.85 | 🤖 | Likely AI-Generated | moderate confidence | 🤖 Likely AI-Generated · moderate confidence |
| 0.85–1.00 | 🤖 | Likely AI-Generated | high confidence | 🤖 Likely AI-Generated · high confidence |

---

### Notes

- The uncertain tier does not display a confidence qualifier. Qualifying uncertainty further adds no information for the reader.
- Scores in the moderate confidence ranges intentionally produce softer labels to reflect the false positive asymmetry in the system design: a score must clear 0.70 before any AI label is shown, and must clear 0.80 before that label carries high confidence.
- The raw confidence score is not displayed, as it is meaningless to the user, but is stored in the audit log and returned in the API response for playing back in the API response for transparency.

---

## Appeals Workflow

### Who Can Appeal
Any creator may appeal a label on their submitted content regardless of tier. While appeals are most likely from creators labeled "Authorship Uncertain" or "Likely AI-Generated," restricting appeals by tier would undermine the fairness of the system.

### Information Captured on Submission
The appeal form auto-populates the following fields from the original submission:

| Field | Source |
|---|---|
| `post_id` | Auto-populated from original submission |
| `user_id` | Auto-populated from session |

The creator provides:

| Field | Source |
|---|---|
| `appeal_text` | Creator-written reasoning |

The system adds on receipt:

| Field | Source |
|---|---|
| `appeal_id` | Generated by system |
| `appeal_timestamp` | Generated by system |
|

---

### Status Tracking

Two separate status fields are maintained:

**Submission status** (on the original content record):

| Status | Meaning |
|---|---|
| `active` | No appeal filed; label is current |
| `under_review` | Appeal filed; label is under review |

**Appeal status** (on the appeal record):

| Status | Meaning |
|---|---|
| `pending` | Appeal received, not yet reviewed |
| `reviewed` | Reviewer has opened the appeal |
| `approved` | Appeal upheld |
| `rejected` | Appeal denied |

When an appeal is filed, the submission status updates from `"active"` to `"under_review"` and the appeal record is created with status `"pending"`. Both events are written to the audit log.

---

### What Gets Logged
Every appeal event appends to the audit log with:
- `appeal_id`
- `post_id` (links back to original submission)
- `user_id`
- `appeal_text`
- `appeal_timestamp`
- `appeal_status`

---

### Human Reviewer Queue
A reviewer opening the appeal queue would see the following for each appeal:

| Field | Purpose |
|---|---|
| Original content (full text) | What the creator submitted |
| Label shown to user | What the creator is objecting to |
| Combined confidence score | Overall system verdict |
| Signal 1 score (LLM) | Individual signal breakdown |
| Signal 2 score (stylometric) | Individual signal breakdown |
| Creator appeal text | Creator's reasoning |
| Appeal timestamp | For sorting queue oldest-first |
| Final decision + notes | Reviewer's conclusion (written on review) |

---

### Known Limitations

- Approving an appeal does not currently update the label displayed on the content. Overriding a label post-review is a known gap and flagged as a future TODO.
- The appeals workflow assumes human review. No automated re-classification is performed on appeal.

---

## Database Schema

### Submissions Table

| Field | Type | Description |
|---|---|---|
| `content_id` | TEXT PRIMARY KEY | UUID generated on submission |
| `user_id` | TEXT | Supplied by user on submission |
| `text` | TEXT | Full submitted content |
| `s1_score` | REAL | Signal 1 (LLM) float 0.0–1.0 |
| `s2_score` | REAL | Signal 2 (stylometric) float 0.0–1.0 |
| `confidence` | REAL | Weighted combined score float 0.0–1.0 |
| `label` | TEXT | Full label text shown to user |
| `status` | TEXT | `"active"` or `"under_review"` |
| `timestamp` | TEXT | ISO 8601 submission timestamp |

---

### Appeals Table

| Field | Type | Description |
|---|---|---|
| `appeal_id` | TEXT PRIMARY KEY | UUID generated on appeal receipt |
| `content_id` | TEXT FOREIGN KEY | Links to submissions.content_id |
| `user_id` | TEXT | Supplied by user on submission |
| `appeal_text` | TEXT | Creator-written reasoning |
| `appeal_status` | TEXT | `"pending"`, `"reviewed"`, `"approved"`, `"rejected"` |
| `appeal_timestamp` | TEXT | ISO 8601 appeal submission timestamp |
| `reviewer_notes` | TEXT | Reviewer's conclusion, nullable until reviewed |

---

### Relationships

Appeals link to submissions via `content_id`. One submission may have multiple appeals (a creator may appeal a revised decision), but in the current implementation a submission is expected to have at most one active appeal at a time.

---

## Anticipated Edge Cases

Known blind spots: Minimalist or deliberately plain human prose, unconventional stylistic choices (non-standard capitalization, missing punctuation), poetry and lyric forms, and short texts under ~150 words where the model has insufficient signal.

1. Highly structured text like technical writing might score as AI-generated from the LLM signal given its uniform sentence structure and length, generic vocabulary, and reliance on logical transitions. This sort of writing is often uniform and balanced, which can be viewed as a signal of AI-generated writing. Example:

```
"The project entered its secondary phase in early Q3, following the successful mitigation of initial supply chain constraints. Key deliverables for this period included the finalization of the user interface architecture and the migration of the legacy database to a cloud-based environment. While the engineering team met the primary deployment deadlines, integration testing revealed minor latency issues in data retrieval protocols. Current remediation efforts are focused on indexing optimization to ensure system stability prior to the wider regional rollout scheduled for next month."
```

2. Minimalist prose that features short, declarative sentences and limited punctuation variation would likely generate a high-confidence AI score from the heuristics signal. Earnest Hemingway's writing is a great example of this (below).

```
It was dark and we heard the rain. I could hear the horses on the bridge. The road was muddy. We turned off and went up the hill.

# Citation: A Farewell to Arms, Hemingway
```

---

## AI Tool Plan

#### Milestone 3

**Which spec sections I'll provide:** Architecture, API surface, and detection signals sections. 

**What I'll ask it the AI tool to generate:** The Flask app skeleton, control layer for the API endpoints, and the first signal (LLM inference) function.

**How I'll verify the output:** Use `curl` to send a POST request to the /submit endpoint to generate a log object. I'll use GET /log to validate the implementation has logged the submission with an actual value for the `s1_score` attribute, and placeholders for `s2_score`, `confidence`, and `label`.

#### Milestone 4

**Which spec sections I'll provide:** Detection signal, uncertainty representation, and architecture sections.

**What I'll ask it the AI tool to generate:** The second signal (stylometric heuristics) function and scoring logic.

**How I'll verify the output:** Test scoring with 4 different outputs and validate scores against intuition. Use two clear cases and two boarderline cases to evaluate performance.

#### Milestone 5

**Which spec sections I'll provide:** Transparency label design, appeals workflow, and architecture sections.

**What I'll ask it the AI tool to generate:** The label generation logic and /appeal endpoint.

**How I'll verify the output:** Use `curl` to send a POST request to the /appeal endpoint to generate a log object. I'll use GET /log to validate the implementation has logged the appeal, updated the status for the submission to `under_review`, and the `appeal_text` is populated with the text from the request body.

**Which spec sections I'll provide:** Architecture section and implemented appeals workflow.

**What I'll ask it the AI tool to generate:** A simple front end with a text window, submit functionality, label display, and appeals submission functionality.

**How I'll verify the output:** Use the test examples from milestone 4 to verify the correct labels are returned and displayed on the frontend. Additionally, appeal the final example and confirm the appeal log updates per the spec. 

---