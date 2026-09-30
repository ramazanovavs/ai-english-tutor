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
