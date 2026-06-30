# Provenance Guard
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

**Response body:**
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

## Example API Request & Response

### Request body:
```json
{
    "user_id": "test_user_1",
    "text": "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment."
}
```

### Response
```json
{
    "content_id": "e8148768-d566-40b7-8a0a-3314a07b7d9a",
    "confidence": 0.75,
    "label": "\ud83e\udd16 Likely AI-Generated \u00b7 moderate confidence"
}
```

---

## Detection Signals

#### Signal 1: LLM Inference:

An LLM will be used to pattern match the input text against its own training corpus. AI writing tends to stylistic and structural regularities that it should be able to detect with a system prompt.  No explanation is required or requested; the signal will just return the float. An example of a system prompt is below.

**What it measures:** The semantic and stylistic properties of the text as assessed by a large language model. This signal leverages the LLM's internalized knowledge of how AI-generated and human-authored text differ in ways that are difficult to enumerate explicitly. It measures these stylistic and structural regularities in the input text and returns a single float value between 0 and 1 representing the text's similarity to AI writing patterns. The response is parsed and validated; if the output is not a clean float in range, the pipeline falls back to a neutral 0.5.

**Output:** Float 0.0–1.0. 0.0 = confidently human-authored, 1.0 = confidently AI-generated.

**Weight in combined score:** 0.7

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

**Weight in combined score**: 0.3

---

## Confidence Scoring

Confidence scores are calculated as a weighted average of the two signals. LLM semantic analysis is weighted at 0.7 and stylometric heuristics is weighted at 0.3, and weights are mapped to a 0.0–1.0 scale where 0.0 indicates confidently human-authored and 1.0 indicates confidently AI-generated. For submissions under 150 words, the stylometric score is dampened toward 0.5 to reflect reduced statistical reliability at short lengths; the LLM score is not dampened.

### Variation in Example Submissions

The following two samples from validation testing demonstrate that the system produces meaningfully different scores rather than clustering around a constant:

**High-confidence AI detection**
> "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment."

| s1 (LLM) | s2 (Stylometric) | Confidence |
|---|---|---|
| 0.87 | 0.41 | 0.75 |

The LLM signal fired strongly (0.87) on the hedging language and uniform structure characteristic of AI output. The stylometric signal was dampened due to short length, limiting its influence on the final score.

---

**Lower-confidence human detection**
> "The relationship between monetary policy and asset price inflation has been extensively studied in the literature. Central banks face a fundamental tension between their mandate for price stability and the unintended consequences of prolonged low interest rates on equity and real estate valuations."

| s1 (LLM) | s2 (Stylometric) | Confidence |
|---|---|---|
| 0.43 | 0.42 | 0.44 |

Both signals returned mid-range scores, correctly reflecting genuine ambiguity. This is formal academic prose written by a human, but its structural conventions (uniform sentence length, conservative punctuation, domain vocabulary) produce surface patterns that overlap with AI writing. The system correctly declined to make a confident determination either way.

---

## Uncertainty Representation

As described in the previous section, single signal ouputs will be combined using a weighted average which favors the LLM signal. Additional dampening of the weighted average towards uncertainty will be applied to short (< 150 words) text to account for the limitations of the stylometric signal on short text. Labels are assigned to the following ranges for this single weighted average:

| Range | Label |
|---|---|
| 0.0 - 0.4 | high-confidence human |
| 0.4 - 0.7 | uncertain |
| 0.7 - 1.0 | high-confidence AI |

Example: a weighted average signal score of 0.6 would sit just to the right of the midpoint of the "uncertain" range. This means that the system is leaning towards "high-confidence AI", but ultimately does not have enough confidence to push it into that domain, so it stays in "uncertain." The threshold for "high-confidence AI" is set conservatively at 0.7 to reduce false positives.

---

## Transparency Label Design

Labels are displayed to the reader alongside submitted content on the frontend. Each label consists of an icon, a tier name, and a confidence qualifier. 

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

### Examples

POST /appeal request
```json
{
    "content_id": "5d02fecb-3e3e-4f0c-99ae-8430fb56744f", 
    "user_id": "test-user-19", 
    "appeal_text": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical."
}
```

POST /appeal response
```json
{
    "appeal_id": "cc237dcb-86df-4cc9-9c9b-a295e2ea208b",
    "message": "Your appeal has been received and will be reviewed."
}
```

Audit log post appeal
```json
{
    "appeal_id": "cc237dcb-86df-4cc9-9c9b-a295e2ea208b",
    "appeal_status": "pending",
    "appeal_text": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.",
    "appeal_timestamp": "2026-06-30T18:24:25.764228+00:00",
    "confidence": 0.44,
    "content_id": "5d02fecb-3e3e-4f0c-99ae-8430fb56744f",
    "label": "\u26a0\ufe0f Authorship Uncertain",
    "s1_score": 0.43,
    "s2_score": 0.42,
    "status": "under_review",
    "text": "The relationship between monetary policy and asset price inflation has been extensively studied in the literature. Central banks face a fundamental tension between their mandate for price stability and the unintended consequences of prolonged low interest rates on equity and real estate valuations.",
    "timestamp": "2026-06-30T17:55:54.294591+00:00",
    "user_id": "test-user-19"
}
```

---

## Validation Testing

To sanity-check whether the confidence scores are meaningful rather than arbitrary, the system was tested against a small set of hand-picked samples spanning different registers, including known edge cases identified during development.

| Sample | Expected | s1 (LLM) | s2 (Stylometric) | Confidence | Label | Notes |
|---|---|---|---|---|---|---|
| "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment." | High-confidence AI | 0.87 | 0.41 | 0.75 | Likely AI-Generated · moderate confidence | Correct directionally; s2 dampened, s1 drove the result |
| "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. my friend got the spicy version and said it was better. probably won't go back unless someone drags me there" | High-confidence human | 0.21 | 0.42 | 0.29 | Likely Human-Written · moderate confidence | s2 pulls the score towards uncertain, but confident s1 score and weighting yields the correct label |
| "The relationship between monetary policy and asset price inflation has been extensively studied in the literature. Central banks face a fundamental tension between their mandate for price stability and the unintended consequences of prolonged low interest rates on equity and real estate valuations." | Uncertain | 0.43 | 0.42 | 0.44 | Authorship Uncertain | Correctly landed in uncertain. This was an intended hard case |
| "I've been thinking a lot about remote work lately. There are genuine tradeoffs — flexibility and no commute on one side, isolation and blurred work-life boundaries on the other. Studies show productivity varies widely by individual and role type." | Uncertain | 0.21 | 0.40 | 0.29 | Likely Human-Written · moderate confidence | s1 read this as human despite AI-adjacent phrasing |
| "took this course along with 271 and struggled a bit with time management until the middle of the term. I was often spending half the week on one subject and half the week on the other, and then if either subject was particularly challenging that week, would find myself behind on the other. I switched up my study habits to dedicate equal time to each subject midway through the course and found the experience much less stressful. I think the hardest part of the term was around the midway point with both 162 and 271 having larger and more conceptually challenging projects that pushed me in new ways. I took advantage of the office hours this semester and enjoyed getting to ask more conceptual questions directly to the instructors." | High-confidence human | 0.21 | 0.5 | 0.30 | Likely Human-Written · moderate confidence | s2 diluted the confident s1 signal score |
| "In the morning I walked down the Boulevard to the rue Soufflot for coffee and brioche. It was a fine morning. The horse-chestnut trees in the Luxembourg gardens were in bloom. There was the pleasant early-morning feeling of a hot day. I read the papers with the coffee and then smoked a cigarette. The flower-women were coming up from the market and arranging their daily stock. Students went by going up to the law school, or down to the Sorbonne." | High-confidence human | 0.21 | 0.53 | 0.30 | Likely Human-Written · moderate confidence | s2 still misfires on uniform short sentences, correctly overridden by s1. Intended hard case. Citation: Farewell to Arms, Earnest Hemingway |

---

## Know Limitations

This system may struggle with AI-generated text written in a casual or conversational register.

The remote-work sample from the validation section scored 0.21 on s1, which is the same score as the ramen review and the Hemingway excerpt. The LLM signal couldn't distinguish it from clearly human writing, likely because the conversational framing ("I've been thinking a lot about...") masked the AI content underneath ("Studies show productivity varies widely by individual and role type").

This is a meaningful gap because it represents a realistic attack vector on a creative platform by someone who prompts an AI to write in a personal, casual voice rather than the formal hedging style of AI generated text. A slightly more sophisticated user who prompts for a conversational tone could slip through with a confident human label.

---

## Complete Audit Log

```json
[
    {
        "confidence": 0.75,
        "content_id": "e8148768-d566-40b7-8a0a-3314a07b7d9a",
        "label": "\ud83e\udd16 Likely AI-Generated \u00b7 moderate confidence",
        "s1_score": 0.87,
        "s2_score": 0.41,
        "status": "active",
        "text": "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment.",
        "timestamp": "2026-06-30T17:55:23.617989+00:00",
        "user_id": "test-user-17"
    },
    {
        "confidence": 0.29,
        "content_id": "9cd419f9-22b1-4927-b06d-02677c404053",
        "label": "\u2705 Likely Human-Written \u00b7 moderate confidence",
        "s1_score": 0.21,
        "s2_score": 0.42,
        "status": "active",
        "text": "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. my friend got the spicy version and said it was better. probably wont go back unless someone drags me there",
        "timestamp": "2026-06-30T17:55:40.601402+00:00",
        "user_id": "test-user-18"
    },
    {
        "appeal_id": "b57e2d62-15f9-4040-b673-69021fc41311",
        "appeal_status": "pending",
        "appeal_text": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.",
        "appeal_timestamp": "2026-06-30T18:24:25.764228+00:00",
        "confidence": 0.44,
        "content_id": "5d02fecb-3e3e-4f0c-99ae-8430fb56744f",
        "label": "\u26a0\ufe0f Authorship Uncertain",
        "s1_score": 0.43,
        "s2_score": 0.42,
        "status": "under_review",
        "text": "The relationship between monetary policy and asset price inflation has been extensively studied in the literature. Central banks face a fundamental tension between their mandate for price stability and the unintended consequences of prolonged low interest rates on equity and real estate valuations.",
        "timestamp": "2026-06-30T17:55:54.294591+00:00",
        "user_id": "test-user-19"
    },
    {
        "confidence": 0.29,
        "content_id": "ec35778a-ac42-48f8-8160-ee66c99a1dea",
        "label": "\u2705 Likely Human-Written \u00b7 moderate confidence",
        "s1_score": 0.21,
        "s2_score": 0.4,
        "status": "active",
        "text": "I have been thinking a lot about remote work lately. There are genuine tradeoffs \u2014 flexibility and no commute on one side, isolation and blurred work-life boundaries on the other. Studies show productivity varies widely by individual and role type.",
        "timestamp": "2026-06-30T17:56:08.448431+00:00",
        "user_id": "test-user-20"
    },
    {
        "confidence": 0.3,
        "content_id": "2fe4f1f3-25a4-4ead-ab5c-c20815826fca",
        "label": "\u2705 Likely Human-Written \u00b7 moderate confidence",
        "s1_score": 0.21,
        "s2_score": 0.5,
        "status": "active",
        "text": "took this course along with 271 and struggled a bit with time management until the middle of the term. I was often spending half the week on one subject and half the week on the other, and then if either subject was particularly challenging that week, would find myself behind on the other. I switched up my study habits to dedicate equal time to each subject midway through the course and found the experience much less stressful. I think the hardest part of the term was around the midway point with both 162 and 271 having larger and more conceptually challenging projects that pushed me in new ways. I took advantage of the office hours this semester and enjoyed getting to ask more conceptual questions directly to the instructors.",
        "timestamp": "2026-06-30T17:56:39.655286+00:00",
        "user_id": "test-user-21"
    },
    {
        "confidence": 0.3,
        "content_id": "a2eb3925-5056-4787-b4f1-04565805b1bb",
        "label": "\u2705 Likely Human-Written \u00b7 moderate confidence",
        "s1_score": 0.21,
        "s2_score": 0.53,
        "status": "active",
        "text": "In the morning I walked down the Boulevard to the rue Soufflot for coffee and brioche. It was a fine morning. The horse-chestnut trees in the Luxembourg gardens were in bloom. There was the pleasant early-morning feeling of a hot day. I read the papers with the coffee and then smoked a cigarette. The flower-women were coming up from the market and arranging their daily stock. Students went by going up to the law school, or down to the Sorbonne.",
        "timestamp": "2026-06-30T17:56:52.478576+00:00",
        "user_id": "test-user-22"
    }
]
```

---

## Rate Limiting

Submissions are rate limited to 10 requests per minute and 100 requests per day per `user_id`. The per-minute limit targets burst abuse, like automated flooding or rapid probing, since no legitimate human creator submits 10 pieces of content in 60 seconds. The per-day limit targets sustained abuse while remaining generous enough for real usage patterns. Even a prolific writer submitting drafts and revisions throughout a full day is unlikely to approach 100 submissions. Limits are keyed on `user_id` rather than IP address, which means an adversary rotating `user_id` values could bypass them with this implementation.

### Verification

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5001/submit \
    -H "Content-Type: application/json" \
    -d '{"text": "This is a test submission for rate limit testing purposes only.", "user_id": "ratelimit-test"}'
done

200
200
200
200
200
200
200
200
200
200
429
429
```

---

## Spec Reflection

**One way the spec helped:** The architecture section and API surface were very helpful in implementing the signal functions, and my AI tool was able to implement them with minimal revisions. I spent a lot of time iterating on the API surface and submission/appeal schema in particular, and it paid off in fewer prompt iterations when using AI tools for implementation.

**One way implementation diverged and why:** As I was researching this topic, I developed a dampening methodology to account for poor signal performance on short text (< 150 words). However, after testing, I found that the LLM signal was reliably producing varied signal scores consistent with expected output for the example text, indicating that text length was not a big factor in the LLM's ability to generate a meaningful score. When dampening was applied to the LLM signal, the confidence scores were pulled closer to uncertain (0.5), and the majority of the example texts were labeled uncertain. The dampening on the LLM signal effectively reduced the impact of the weighting. I retained the dampening effect for short text on the stylometric signal. I also stated I would implement a lightweight frontend, but ultimately ran out of time, so will save that for a future iteration.

---

## AI Usage

#### Instance 1
**What I asked the AI to do:** Implement `signal_2_stylometrics` using the architecture and detection signal sections of `planning.md`.

**What I changed or overrode:** I expanded the function words from a list of 4 words to a list of 35, including pronouns, conjunctions, and prepositions. I also added error handling for use of `statistics.stdev` by providing a fallback to 0.5 in `function_word_frequency` if there is not enough data for `statistics.stdev` to use (function word rate < 2). I also rounded the output from the signal to 2 decimal places for consistency with signal 1.

#### Instance 2
**What I asked the AI to do:** Implement `signal_2_stylometrics` using the architecture and detection signal sections of `planning.md`. It implemented each heuristic with a fallback to baseline uncertainty (0.5) if the heuristic lacked enough data to run a meaninful statistical analysis. 

**What I changed or overrode:** In testing, I found this had could be pulling stylometric scores towards uncertain, as the fallback was included in the average. To make stylometric score more meaninfcul, I removed the fallback to a baseline uncertain score and instead had the fallback return None so that the heuristic could be excluded from the final average. If the sample text was so short so that all heuristics failed, the signal falls back to the same 0.5 baseline uncertain score.

---
