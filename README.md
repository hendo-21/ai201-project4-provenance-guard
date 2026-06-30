# Provenance Guard

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

## Validation Testing

To sanity-check whether the confidence scores are meaningful rather than arbitrary, the system was tested against a small set of hand-picked samples spanning different registers, including known edge cases identified during development.

| Sample | Expected | s1 (LLM) | s2 (Stylometric) | Confidence | Label | Notes |
|---|---|---|---|---|---|---|
| "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment." | High-confidence AI | 0.87 | 0.41 | 0.75 | Likely AI-Generated · moderate confidence | Correct directionally; s2 dampened, s1 drove the result |
| "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. my friend got the spicy version and said it was better. probably won't go back unless someone drags me there" | High-confidence human | 0.21 | 0.42 | 0.29 | Likely Human-Written · moderate confidence | Correctly resolved after fixing dampening scope |
| "The relationship between monetary policy and asset price inflation has been extensively studied in the literature. Central banks face a fundamental tension between their mandate for price stability and the unintended consequences of prolonged low interest rates on equity and real estate valuations." | Uncertain | 0.43 | 0.42 | 0.44 | Authorship Uncertain | Correctly landed in uncertain — intended hard case |
| "I've been thinking a lot about remote work lately. There are genuine tradeoffs — flexibility and no commute on one side, isolation and blurred work-life boundaries on the other. Studies show productivity varies widely by individual and role type." | Uncertain | 0.21 | 0.40 | 0.29 | Likely Human-Written · moderate confidence | s1 read this as human despite AI-adjacent phrasing |
| "took this course along with 271 and struggled a bit with time management until the middle of the term. I was often spending half the week on one subject and half the week on the other, and then if either subject was particularly challenging that week, would find myself behind on the other. I switched up my study habits to dedicate equal time to each subject midway through the course and found the experience much less stressful. I think the hardest part of the term was around the midway point with both 162 and 271 having larger and more conceptually challenging projects that pushed me in new ways. I took advantage of the office hours this semester and enjoyed getting to ask more conceptual questions directly to the instructors." | High-confidence human | 0.21 | 0.5 | 0.30 | Likely Human-Written · moderate confidence | s2 diluted the confident s1 signal score |
| "In the morning I walked down the Boulevard to the rue Soufflot for coffee and brioche. It was a fine morning. The horse-chestnut trees in the Luxembourg gardens were in bloom. There was the pleasant early-morning feeling of a hot day. I read the papers with the coffee and then smoked a cigarette. The flower-women were coming up from the market and arranging their daily stock. Students went by going up to the law school, or down to the Sorbonne." | High-confidence human | 0.21 | 0.53 | 0.30 | Likely Human-Written · moderate confidence | s2 still misfires on uniform short sentences, correctly overridden by s1. Intended hard case. |

*Confidence calculated via a 0.7/0.3 (LLM/stylometric) weighted average. For texts under 150 words, the stylometric score (s2) is dampened toward 0.5 to reflect reduced statistical reliability at short lengths; the LLM score (s1) is not dampened, since its confidence is not a function of sample size in the same way.*

---

## Spec Reflection

**One way the spec helped:**

**One way implementation diverged and why:** Dampening methodology.

---

## AI Usage

#### Instance 1
**What I asked the AI to do:** Implement `signal_2_stylometrics` using the architecture and detection signal sections of `planning.md`.

**What I changed or overrode:** I expanded the function words from a list of 4 words to a list of 35, including pronouns, conjunctions, and prepositions. I also added error handling for use of `statistics.stdev` by providing a fallback to 0.5 in `function_word_frequency` if there is not enough data for `statistics.stdev` to use (function word rate < 2). I also rounded the output from the signal to 2 decimal places for consistency with signal 1.

#### Instance 2
**What I asked the AI to do:** Implement `signal_2_stylometrics` using the architecture and detection signal sections of `planning.md`. It implemented each heuristic with a fallback to baseline uncertainty (0.5) if the heuristic lacked enough data to run a meaninful statistical analysis. 

**What I changed or overrode:** In testing, I found this had could be pulling stylometric scores towards uncertain, as the fallback was included in the average. To make stylometric score more meaninfcul, I removed the fallback to a baseline uncertain score and instead had the fallback return None so that the heuristic could be excluded from the final average. If the sample text was so short so that all heuristics failed, the signal falls back to the same 0.5 baseline uncertain score.

---