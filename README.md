# AI English Tutor

A deployable MVP of an adaptive AI English tutor.

## Included

- Supabase email/password authentication
- Student profile + CEFR-style skill dashboard
- AI Tutor chat
- Grammar Coach with corrections and mini-practice
- Vocabulary set generator
- Writing Coach
- Speaking role-play transcript feedback
- Adaptive mini-tests
- Learning event history
- GitHub/Render-ready configuration

> The displayed CEFR levels are learning-profile estimates, not official language certifications.

## Stack

- Python + FastAPI
- OpenAI Responses API
- Supabase Auth + PostgreSQL
- Bootstrap + vanilla JavaScript
- Render

## 1. Create Supabase project

1. Create a project at Supabase.
2. Open **SQL Editor**.
3. Run `database/schema.sql`.
4. Open **Project Settings -> API** and copy:
   - Project URL
   - anon/public key
   - service_role key

Important: `SUPABASE_SERVICE_ROLE_KEY` is a server secret. Never paste it into frontend JavaScript or commit it to GitHub.

### Email confirmation

By default Supabase can require email verification. For a classroom demo you can disable **Confirm email** in Auth settings, or keep it enabled and confirm accounts via email.

For production, configure your Auth Site URL / redirect URLs for the final Render domain.

## 2. Create OpenAI API key

Create an API key in the OpenAI developer platform and keep it private.

The project uses the Responses API:

```python
response = client.responses.create(
    model=settings.openai_model,
    instructions=instructions,
    input=user_input,
)
```

Set `OPENAI_MODEL` to a model available to your API project. The example default is `gpt-5-mini`.

## 3. Run locally

Python 3.13 is configured in `.python-version`.

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Install:

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the values.

Run:

```bash
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

API docs:

```text
http://127.0.0.1:8000/docs
```

## 4. Put the project on GitHub

Create a new GitHub repository, then from this project folder:

```bash
git init
git add .
git commit -m "Initial AI English Tutor MVP"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/ai-english-tutor.git
git push -u origin main
```

`.env` is excluded by `.gitignore`.

## 5. Deploy to Render

### Option A: render.yaml

1. Push the repository to GitHub.
2. In Render choose **New -> Blueprint**.
3. Connect the repository.
4. Render detects `render.yaml`.
5. Enter secret environment values when requested.

### Option B: Web Service

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Add environment variables:

```text
OPENAI_API_KEY
OPENAI_MODEL
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
APP_ENV=production
```

## Security design

- Browser receives only the Supabase anonymous key. That key is designed to be public when RLS is used.
- The service-role key remains on FastAPI/Render.
- The frontend sends the user's Supabase access token as a Bearer token.
- FastAPI validates that token via Supabase Auth before accessing student data.
- The server performs database operations after authentication.
- RLS is enabled as an additional boundary for future direct-from-browser reads.

## Main API routes

| Route | Purpose |
|---|---|
| `GET /api/me` | Student profile and progress |
| `POST /api/chat` | Adaptive AI tutor |
| `POST /api/grammar` | Grammar analysis |
| `POST /api/vocabulary` | Vocabulary set |
| `POST /api/writing` | Writing review |
| `POST /api/speaking` | Speaking transcript feedback |
| `POST /api/test/generate` | Generate mini-test |
| `POST /api/test/submit` | Score mini-test |

## Current MVP limitations

1. Speaking uses browser speech recognition and evaluates the transcript. It intentionally does **not** claim to measure pronunciation acoustically.
2. Generated tests are scored deterministically from the answer key returned when the test is generated.
3. Reading and listening are represented in the student model but do not yet have dedicated screens.
4. The AI's CEFR estimate is formative, not an official CEFR assessment.
5. Production systems should add rate limiting, audit logging, stronger structured-output validation, and cost controls.

## Good next upgrades

- OpenAI Realtime API for live voice conversation
- Audio pronunciation evaluation
- Listening tasks with TTS/audio
- Reading from uploaded PDF/text
- Teacher dashboard
- Course/topic management
- Spaced repetition vocabulary
- IELTS practice modes
- Kazakh/Russian explanation preferences
- Admin analytics
- Automated weekly progress reports


## Cost-optimized OpenAI model setup

This version uses three OpenAI models, each for a separate task:

- `gpt-6-luna` — text tutor, grammar, vocabulary, writing, mini-tests and transcript feedback.
- `gpt-4o-mini-transcribe` — English speech-to-text for recorded speaking answers.
- `gpt-4o-mini-tts` — optional AI-generated tutor audio.

Environment variables:

```text
OPENAI_MODEL=gpt-6-luna
OPENAI_TRANSCRIBE_MODEL=gpt-4o-mini-transcribe
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=marin
```

### Speaking flow

```text
Browser microphone
    -> MediaRecorder
    -> POST /api/audio/transcribe
    -> gpt-4o-mini-transcribe
    -> transcript
    -> text tutor analysis
```

### Tutor voice flow

```text
Tutor text
    -> POST /api/audio/tts
    -> gpt-4o-mini-tts
    -> MP3
    -> browser playback
```

The UI explicitly tells learners that tutor speech is AI-generated. The application does not claim to score pronunciation from a transcript. Recordings are limited by the frontend to about two minutes per recording and by the backend to 12 MB to control cost and abuse.

## IELTS preparation extension

This version adds a second learning track alongside General English / CEFR:

- IELTS profile: Academic or General Training, target band, planned exam date
- IELTS dashboard with section band history and a next-section recommendation
- Writing Task 1 and Task 2 prompt generation and criterion-level formative feedback
- Speaking Parts 1-3 question generation, microphone transcription, and transcript-based feedback
- Reading practice sets with automatic checking
- Listening practice sets rendered with the existing OpenAI TTS pipeline
- IELTS band history and mock-test data model

### Important scoring note

All IELTS bands shown by this application are **AI-generated formative practice estimates**. They are not official IELTS results. Speaking pronunciation is deliberately left unscored because the current implementation evaluates the transcript, not acoustic pronunciation features. Short Reading/Listening sets also use an approximate practice-band conversion rather than an official 40-question test conversion.

### Supabase: existing project

If you have **already run the previous full `database/schema.sql`**, do not rebuild the database. Run only:

```text
database/ielts_schema.sql
```

in **Supabase -> SQL Editor -> New query -> Run**.

It adds six IELTS tables without deleting existing General English data:

```text
ielts_profiles
ielts_writing_attempts
ielts_speaking_attempts
ielts_objective_attempts
ielts_band_history
ielts_mock_tests
```

### Supabase: new project

For a completely new Supabase project, run the current:

```text
database/schema.sql
```

It contains both the General English schema and the IELTS extension, so you do not need to run `ielts_schema.sql` again.

### IELTS API routes

```text
GET  /api/ielts/dashboard
POST /api/ielts/profile
POST /api/ielts/writing/generate
POST /api/ielts/writing/assess
POST /api/ielts/speaking/generate
POST /api/ielts/speaking/assess
POST /api/ielts/reading/generate
POST /api/ielts/listening/generate
POST /api/ielts/objective/submit
```

The same cost-optimized OpenAI stack is used:

```text
Text tutoring / IELTS generation and feedback -> OPENAI_MODEL
Speech-to-text -> gpt-4o-mini-transcribe
Text-to-speech -> gpt-4o-mini-tts
```


## Full IELTS Mock Exam

The project now includes a complete **Full IELTS Mock Exam** mode.

Flow:

```text
Listening (4 parts / 40 questions / ~30 min)
→ Reading (3 sections / 40 questions / 60 min)
→ Writing (Task 1 + Task 2 / 60 min)
→ Speaking (Parts 1–3 / 11–14 min)
→ Practice result
```

### Exam-mode safeguards

- Listening answer keys and scripts stay server-side.
- Listening audio is generated from the stored script through TTS.
- The normal mock UI allows each Listening part to be started once.
- Reading answer keys stay server-side until submission.
- Writing Task 2 is weighted twice Task 1 when calculating the practice Writing band.
- Speaking is assessed from transcripts; pronunciation is deliberately left unscored.
- All scores are clearly marked as formative **practice estimates**, not official IELTS results.

### Existing Supabase project

If you already ran the previous IELTS schema, run only:

```text
database/ielts_full_mock_schema.sql
```

It adds `ielts_mock_sections` and does not delete existing learning data.

For a brand-new Supabase project, run the complete:

```text
database/schema.sql
```

### Full mock API endpoints

```text
POST /api/ielts/mock/start
GET  /api/ielts/mock/{mock_id}
POST /api/ielts/mock/{mock_id}/generate
GET  /api/ielts/mock/{mock_id}/listening/{part}/audio
POST /api/ielts/mock/{mock_id}/objective/submit
POST /api/ielts/mock/{mock_id}/writing/submit
POST /api/ielts/mock/{mock_id}/speaking/submit
POST /api/ielts/mock/{mock_id}/finish
```
