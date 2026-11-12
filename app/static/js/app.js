const sb = supabase.createClient(APP_CONFIG.supabaseUrl, APP_CONFIG.supabaseAnonKey);
let currentSession = null;
let currentProfile = null;
let currentTest = null;
let currentVocabSet = null;
let currentVocabPractice = null;
let mediaRecorder = null;
let mediaChunks = [];
let recordingStream = null;
let recordingStopTimer = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  }[c]));
}

async function api(path, options = {}) {
  if (!currentSession) throw new Error("Please sign in.");
  const headers = {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${currentSession.access_token}`,
    ...(options.headers || {})
  };
  const res = await fetch(path, {...options, headers});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}


async function apiForm(path, formData) {
  if (!currentSession) throw new Error("Please sign in.");
  const res = await fetch(path, {
    method: "POST",
    headers: {"Authorization": `Bearer ${currentSession.access_token}`},
    body: formData
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

async function speakText(text, button = null) {
  if (!text || !currentSession) return;
  const oldLabel = button ? button.textContent : null;
  if (button) {
    button.disabled = true;
    button.textContent = "Generating audio…";
  }
  try {
    const res = await fetch("/api/audio/tts", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${currentSession.access_token}`
      },
      body: JSON.stringify({text})
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Could not create speech.");
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.onended = () => URL.revokeObjectURL(url);
    await audio.play();
  } catch (e) {
    alert(e.message);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldLabel;
    }
  }
}

async function signUp() {
  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;
  const { data, error } = await sb.auth.signUp({email, password});
  const el = document.getElementById("authMsg");
  if (error) return el.innerHTML = `<span class="text-danger">${esc(error.message)}</span>`;
  el.innerHTML = data.session
    ? `<span class="text-success">Account created.</span>`
    : `<span class="text-success">Account created. Check your email to confirm it.</span>`;
}

async function signIn() {
  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;
  const { data, error } = await sb.auth.signInWithPassword({email, password});
  if (error) {
    document.getElementById("authMsg").innerHTML = `<span class="text-danger">${esc(error.message)}</span>`;
    return;
  }
  currentSession = data.session;
  await showApp();
}

async function signOut() {
  await sb.auth.signOut();
  currentSession = null;
  document.getElementById("appView").classList.add("d-none");
  document.getElementById("authView").classList.remove("d-none");
}

async function showApp() {
  document.getElementById("authView").classList.add("d-none");
  document.getElementById("appView").classList.remove("d-none");
  await loadProfile();
}

async function loadProfile() {
  try {
    currentProfile = await api("/api/me");
    renderDashboard();
    renderRoadmapDashCard();
    loadToday();
  } catch (e) {
    alert(e.message);
  }
}

function renderDashboard() {
  const profile = currentProfile.profile || {};
  const skills = currentProfile.skills || [];

  const assessed = skills.filter(s =>
    Number(s.attempts || 0) > 0 &&
    s.score !== null &&
    s.score !== undefined
  );

  document.getElementById("navLevel").textContent =
    profile.cefr_level || "Not assessed";
  document.getElementById("navEmail").textContent =
    currentProfile.email || currentSession?.user?.email || "";
  document.getElementById("navAdmin").classList.toggle("d-none", !currentProfile.is_admin);

  const masteryLabel = (s) => {
    if (!s.mastery_status) return "";
    return s.mastery_status.charAt(0).toUpperCase() + s.mastery_status.slice(1);
  };

  document.getElementById("skillCards").innerHTML = skills.map(s => {
    const isAssessed =
      Number(s.attempts || 0) > 0 &&
      s.score !== null &&
      s.score !== undefined;

    const scoreText = isAssessed ? `${esc(s.score)}%` : "Not assessed";
    const levelText = isAssessed && s.level ? esc(s.level) : "—";
    const masteryText = isAssessed && s.mastery_status
      ? ` · ${esc(masteryLabel(s))}`
      : "";

    return `
      <div class="col-md-6 col-xl-4">
        <div class="card-soft h-100">
          <div class="text-secondary small">${esc(s.skill)}</div>
          <div class="skill-score">${scoreText}</div>
          <div class="d-flex justify-content-between align-items-center gap-2">
            <span class="badge text-bg-light">${levelText}${masteryText}</span>
            <span class="small text-secondary">${esc(s.attempts || 0)} attempts</span>
          </div>
          ${s.last_tested_level && s.last_tested_level !== s.level
            ? `<div class="small text-secondary mt-2">Latest evidence: ${esc(s.last_tested_level)} · ${esc(s.last_test_score)}%</div>`
            : ""}
        </div>
      </div>
    `;
  }).join("");

  const masteryRank = {developing: 0, proficient: 1, strong: 2};
  const weakest = assessed.length
    ? [...assessed].sort((a, b) => {
        const ma = masteryRank[a.mastery_status] ?? 9;
        const mb = masteryRank[b.mastery_status] ?? 9;
        if (ma !== mb) return ma - mb;
        return Number(a.score) - Number(b.score);
      })[0]
    : null;

  if (!weakest) {
    document.getElementById("recommendation").innerHTML =
      "No skills have been assessed yet. Start with a short diagnostic activity.";
  } else {
    const currentLevel = weakest.level || "current";
    const nextMap = {A1:"A2", A2:"B1", B1:"B2", B2:"C1", C1:"C2", C2:null};
    const next = nextMap[weakest.level] || null;

    if (weakest.mastery_status === "strong" && next) {
      document.getElementById("recommendation").innerHTML =
        `<strong>${esc(weakest.skill)}</strong> is strong at ${esc(currentLevel)}. Try a ${esc(next)} diagnostic before raising the level.`;
    } else {
      document.getElementById("recommendation").innerHTML =
        `<strong>${esc(weakest.skill)}</strong> is your weakest assessed area: ${esc(currentLevel)} · ${esc(masteryLabel(weakest) || "developing")} (${esc(weakest.score)}%). Practice this level before moving up.`;
    }
  }

  document.getElementById("progressTable").innerHTML = `
    <div class="table-responsive"><table class="table align-middle">
      <thead><tr><th>Skill</th><th>Score</th><th>Assessed level</th><th>Mastery</th><th>Attempts</th></tr></thead>
      <tbody>${skills.map(s => {
        const isAssessed =
          Number(s.attempts || 0) > 0 &&
          s.score !== null &&
          s.score !== undefined;
        return `<tr>
          <td>${esc(s.skill)}</td>
          <td>${isAssessed ? `${esc(s.score)}%` : "Not assessed"}</td>
          <td>${isAssessed && s.level ? esc(s.level) : "—"}</td>
          <td>${isAssessed && s.mastery_status ? esc(masteryLabel(s)) : "—"}</td>
          <td>${esc(s.attempts || 0)}</td>
        </tr>`;
      }).join("")}</tbody>
    </table></div>`;

  document.getElementById("recentActivity").innerHTML = (currentProfile.recent || []).length
    ? currentProfile.recent.map(r => {
        const type = r.metadata?.evidence_source ? "assessment" :
          (String(r.activity_type).includes("diagnostic") ? "diagnostic" :
          (String(r.topic).startsWith("Transcription:") ? "transcription" : "practice"));
        return `<div class="py-2 border-bottom">
          <strong>${esc(r.activity_type)}</strong> · ${esc(r.topic)}
          ${r.score != null ? `— ${esc(r.score)}%` : ""}
          <span class="badge text-bg-light ms-2">${esc(type)}</span>
        </div>`;
      }).join("")
    : `<div class="text-secondary">No activities yet.</div>`;
}
function openPage(name) {
  document.querySelectorAll(".page").forEach(x => x.classList.add("d-none"));
  document.getElementById(`page-${name}`).classList.remove("d-none");
  document.querySelectorAll(".menu-btn").forEach(x => x.classList.toggle("active", x.dataset.page === name));
  if (name === "progress" || name === "dashboard") loadProfile();
  if (name === "listening") suggestListeningLevel();
  if (name !== "listening" && typeof stopListeningAudio === "function") stopListeningAudio();
  if (name === "roadmap") loadRoadmap();
  if (name === "review") loadReview();
  if (typeof renderRoadmapTaskBanner === "function") renderRoadmapTaskBanner(name);
}

document.querySelectorAll(".menu-btn").forEach(btn => {
  btn.addEventListener("click", () => openPage(btn.dataset.page));
});

function loading(elId, text="Working…") {
  document.getElementById(elId).innerHTML = `<div class="loader">${esc(text)}</div>`;
}

async function sendChat() {
  const input = document.getElementById("chatInput");
  const sendBtn = document.getElementById("chatSendBtn");
  const box = document.getElementById("chatBox");
  const text = input.value.trim();

  if (!text || sendBtn.disabled) return;

  // Remove stale loading bubbles left by any earlier failed request.
  box.querySelectorAll(".chat-thinking, .msg.ai.loader").forEach(el => {
    if ((el.textContent || "").includes("Tutor is thinking")) el.remove();
  });

  box.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(text)}</div>`);
  input.value = "";

  const requestId = `chat_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  const thinkingId = `${requestId}_thinking`;
  const answerId = `${requestId}_answer`;

  box.insertAdjacentHTML(
    "beforeend",
    `<div id="${thinkingId}" class="msg ai loader chat-thinking">Tutor is thinking…</div>`
  );
  box.scrollTop = box.scrollHeight;

  sendBtn.disabled = true;
  sendBtn.textContent = "Sending…";
  input.disabled = true;

  let answerStarted = false;
  let answerText = "";

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${currentSession.access_token}`
      },
      body: JSON.stringify({text})
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `Request failed (${res.status})`);
    }

    if (!res.body) throw new Error("Streaming is not supported by this browser.");

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const {value, done} = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, {stream:true});
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";

      for (const rawEvent of events) {
        const dataLine = rawEvent
          .split("\n")
          .find(line => line.startsWith("data: "));
        if (!dataLine) continue;

        const event = JSON.parse(dataLine.slice(6));

        if (event.type === "delta") {
          if (!answerStarted) {
            document.getElementById(thinkingId)?.remove();
            box.insertAdjacentHTML(
              "beforeend",
              `<div id="${answerId}" class="msg ai"><div class="streamed-answer"></div></div>`
            );
            answerStarted = true;
          }

          answerText += event.text || "";
          const answerEl = document.querySelector(`#${answerId} .streamed-answer`);
          if (answerEl) answerEl.textContent = answerText;
          box.scrollTop = box.scrollHeight;
        }

        if (event.type === "error") {
          throw new Error(event.message || "Tutor request failed.");
        }

        if (event.type === "done") {
          document.getElementById(thinkingId)?.remove();

          if (answerStarted) {
            const wrapper = document.getElementById(answerId);
            if (wrapper && !wrapper.querySelector(".listen-btn")) {
              const button = document.createElement("button");
              button.className = "btn btn-sm btn-link px-0 mt-1 listen-btn";
              button.textContent = "🔊 Listen";
              button.onclick = () => {
                const content = wrapper.querySelector(".streamed-answer")?.innerText || "";
                speakText(content, button);
              };
              wrapper.appendChild(button);
            }
          }
        }
      }
    }

    // Defensive cleanup if the connection closed without a final event.
    document.getElementById(thinkingId)?.remove();

  } catch (e) {
    document.getElementById(thinkingId)?.remove();

    if (!answerStarted) {
      box.insertAdjacentHTML(
        "beforeend",
        `<div class="msg ai text-danger">${esc(e.message || "Request failed")}</div>`
      );
    } else {
      const wrapper = document.getElementById(answerId);
      if (wrapper) {
        const note = document.createElement("div");
        note.className = "small text-danger mt-1";
        note.textContent = "The response was interrupted.";
        wrapper.appendChild(note);
      }
    }
    box.scrollTop = box.scrollHeight;
  } finally {
    // Remove any loading indicator belonging to this request, on both success and error.
    document.getElementById(thinkingId)?.remove();
    sendBtn.disabled = false;
    sendBtn.textContent = "Send";
    input.disabled = false;
    input.focus();
  }
}

async function checkGrammar() {
  const text = document.getElementById("grammarText").value.trim();
  if (!text) return;
  loading("grammarResult", "Analyzing grammar…");
  try {
    const d = await api("/api/grammar", {method:"POST", body:JSON.stringify({text})});
    document.getElementById("grammarResult").innerHTML = `
      <h4>${esc(d.score)}% · ${esc(d.topic)}</h4>
      <p>${esc(d.summary)}</p>
      <div class="alert alert-light"><strong>Corrected:</strong> ${esc(d.corrected_text)}</div>
      <h6>Errors</h6>
      ${(d.errors || []).map(x => `<div class="mb-2"><code>${esc(x.original)}</code> → <strong>${esc(x.correction)}</strong><br><span class="small">${esc(x.rule)}: ${esc(x.explanation)}</span></div>`).join("") || "<p>No major errors.</p>"}
      ${(d.errors || []).length ? `<div class="small text-secondary">🔁 ${esc(T("saved_to_mistakes"))}</div>` : ""}
      <h6 class="mt-3">Mini-practice</h6>
      <ol>${(d.practice || []).map(x => `<li>${esc(x)}</li>`).join("")}</ol>`;
    await loadProfile();
  } catch(e) { document.getElementById("grammarResult").textContent = e.message; }
}

async function makeVocab() {
  const topic = document.getElementById("vocabTopic").value.trim();
  const level = document.getElementById("vocabLevel").value;
  if (!topic) return;

  currentVocabPractice = null;
  loading("vocabResult", "Building vocabulary set…");

  try {
    const d = await api("/api/vocabulary", {
      method:"POST",
      body:JSON.stringify({topic, level})
    });

    currentVocabSet = {topic, level, items: d.items || []};

    document.getElementById("vocabResult").innerHTML = `
      <h4>${esc(d.topic)}</h4>
      ${(d.items || []).map(x => `
        <div class="vocab-item">
          <strong>${esc(x.word)}</strong>
          <span class="text-secondary">${esc(x.part_of_speech)}</span><br>
          ${esc(x.definition)}<br>
          <em>${esc(x.example)}</em><br>
          <small>Collocation: ${esc(x.collocation)}</small>
        </div>`).join("")}
      <div class="alert alert-light mt-3">${esc(d.tip)}</div>
      <div class="d-flex align-items-center gap-2 mt-3 flex-wrap">
        <button id="vocabPracticeBtn" class="btn btn-primary" onclick="startVocabPractice()">
          Start practice · 10 questions
        </button>
        <span class="small text-secondary">
          Your Dashboard score changes only after you complete the practice.
        </span>
      </div>
      <div id="vocabPracticeArea" class="mt-4"></div>`;
  } catch(e) {
    document.getElementById("vocabResult").textContent = e.message;
  }
}


async function startVocabPractice() {
  if (!currentVocabSet?.items?.length) {
    alert("Generate a vocabulary set first.");
    return;
  }

  const btn = document.getElementById("vocabPracticeBtn");
  const area = document.getElementById("vocabPracticeArea");
  btn.disabled = true;
  btn.textContent = "Preparing practice…";
  area.innerHTML = `<div class="loader">Creating 10 vocabulary questions…</div>`;

  try {
    const d = await api("/api/vocabulary/practice", {
      method:"POST",
      body:JSON.stringify(currentVocabSet)
    });
    currentVocabPractice = d;
    renderVocabPractice();
  } catch(e) {
    area.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`;
    btn.disabled = false;
    btn.textContent = "Start practice · 10 questions";
  }
}


function renderVocabPractice() {
  const area = document.getElementById("vocabPracticeArea");
  if (!currentVocabPractice) return;

  area.innerHTML = `
    <hr class="my-4">
    <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
      <div>
        <h4 class="mb-1">${esc(currentVocabPractice.title)}</h4>
        <div class="text-secondary small">Meaning → Context → Recall</div>
      </div>
      <span class="badge text-bg-light">10 questions</span>
    </div>

    <form id="vocabPracticeForm">
      ${(currentVocabPractice.questions || []).map((q, idx) => {
        const name = `vocab_q_${q.id}`;
        const typeLabel =
          q.type === "meaning" ? "Meaning" :
          q.type === "context" ? "Context" : "Recall";

        const control = (q.options || []).length
          ? (q.options || []).map((option, i) => `
              <div class="form-check mt-2">
                <input class="form-check-input" type="radio"
                  name="${name}" id="${name}_${i}" value="${esc(option)}">
                <label class="form-check-label" for="${name}_${i}">
                  ${esc(option)}
                </label>
              </div>`).join("")
          : `<input class="form-control mt-2" name="${name}"
               autocomplete="off" placeholder="Type the word or phrase">`;

        return `
          <div class="card-soft my-3 vocab-practice-question">
            <div class="small text-secondary">${idx + 1}/10 · ${typeLabel}</div>
            <div class="fw-semibold mt-1">${esc(q.question)}</div>
            ${control}
            <div id="vocab_feedback_${q.id}" class="mt-2"></div>
          </div>`;
      }).join("")}

      <button id="vocabSubmitBtn" type="button"
        class="btn btn-primary" onclick="submitVocabPractice()">
        Check answers
      </button>
    </form>

    <div id="vocabPracticeResult" class="mt-4"></div>`;
}


function collectVocabPracticeAnswers() {
  const answers = {};
  for (const q of (currentVocabPractice?.questions || [])) {
    const name = `vocab_q_${q.id}`;
    const radio = document.querySelector(`input[name="${name}"]:checked`);
    const textInput = document.querySelector(
      `input[name="${name}"]:not([type="radio"])`
    );
    answers[String(q.id)] = radio
      ? radio.value
      : (textInput ? textInput.value.trim() : "");
  }
  return answers;
}


async function submitVocabPractice() {
  if (!currentVocabPractice) return;

  const answers = collectVocabPracticeAnswers();
  const unanswered = Object.values(answers).filter(v => !String(v).trim()).length;
  if (unanswered > 0) {
    alert(`Please answer all 10 questions. ${unanswered} question(s) are still empty.`);
    return;
  }

  const btn = document.getElementById("vocabSubmitBtn");
  btn.disabled = true;
  btn.textContent = "Checking…";

  try {
    const d = await api("/api/vocabulary/practice/submit", {
      method:"POST",
      body:JSON.stringify({
        test_id: currentVocabPractice.test_id,
        answers
      })
    });
    roadmapReport("vocabulary", d.score);

    for (const item of (d.details || [])) {
      const el = document.getElementById(`vocab_feedback_${item.id}`);
      if (!el) continue;

      el.innerHTML = item.correct
        ? `<div class="small text-success"><strong>Correct.</strong> ${esc(item.explanation || "")}</div>`
        : `<div class="small text-danger">
             <strong>Review:</strong> your answer: ${esc(item.answer || "—")}<br>
             Correct answer: <strong>${esc(item.correct_answer)}</strong><br>
             <span class="text-secondary">${esc(item.explanation || "")}</span>
           </div>`;
    }

    document.querySelectorAll("#vocabPracticeForm input")
      .forEach(el => el.disabled = true);

    document.getElementById("vocabPracticeResult").innerHTML = `
      <div class="alert ${d.score >= 70 ? "alert-success" : "alert-primary"}">
        <h4 class="mb-1">Vocabulary score: ${esc(d.score)}%</h4>
        <div>${esc(d.correct)} / ${esc(d.total)} correct</div>
      </div>
      <p class="small text-secondary">
        This completed practice has now been added to your Vocabulary progress.
      </p>`;

    btn.textContent = "Completed";
    await loadProfile();
  } catch(e) {
    btn.disabled = false;
    btn.textContent = "Check answers";
    document.getElementById("vocabPracticeResult").innerHTML =
      `<div class="alert alert-danger">${esc(e.message)}</div>`;
  }
}

async function checkWriting() {
  const text = document.getElementById("writingText").value.trim();
  const purpose = document.getElementById("writingPurpose").value;
  if (!text) return;
  loading("writingResult", "Reviewing writing…");
  try {
    const d = await api("/api/writing", {method:"POST", body:JSON.stringify({text, purpose})});
    roadmapReport("writing", d.score, d.estimated_level);
    document.getElementById("writingResult").innerHTML = `
      <h4>${esc(d.score)}% · approx. ${esc(d.estimated_level)}</h4>
      <h6>Strengths</h6><ul>${(d.strengths || []).map(x=>`<li>${esc(x)}</li>`).join("")}</ul>
      <h6>Improve</h6>${(d.improvements || []).map(x=>`<div class="mb-2"><strong>${esc(x.category)}:</strong> ${esc(x.issue)}<br><span class="text-secondary">${esc(x.suggestion)}</span></div>`).join("")}
      ${(d.errors || []).length ? `<h6 class="mt-3">Errors</h6>${d.errors.map(x => `<div class="mb-1"><code>${esc(x.original)}</code> → <strong>${esc(x.correction)}</strong> <span class="small text-secondary">${esc(x.rule || "")}${x.explanation ? " — " + esc(x.explanation) : ""}</span></div>`).join("")}<div class="small text-secondary">🔁 ${esc(T("saved_to_mistakes"))}</div>` : ""}
      <h6 class="mt-3">Revised sample</h6><div class="alert alert-light">${esc(d.revised_sample)}</div>
      <strong>Next task:</strong> ${esc(d.next_task)}`;
    await loadProfile();
  } catch(e) { document.getElementById("writingResult").textContent = e.message; }
}

async function startSpeech() {
  const btn = document.getElementById("micBtn");

  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    return;
  }

  if (!navigator.mediaDevices || !window.MediaRecorder) {
    alert("Audio recording is not supported in this browser. You can type a transcript instead.");
    return;
  }

  try {
    recordingStream = await navigator.mediaDevices.getUserMedia({audio: true});
    const preferred = "audio/webm;codecs=opus";
    const options = MediaRecorder.isTypeSupported(preferred) ? {mimeType: preferred} : {};
    mediaRecorder = new MediaRecorder(recordingStream, options);
    mediaChunks = [];

    mediaRecorder.ondataavailable = event => {
      if (event.data && event.data.size > 0) mediaChunks.push(event.data);
    };

    mediaRecorder.onstop = async () => {
      clearTimeout(recordingStopTimer);
      btn.disabled = true;
      btn.textContent = "Transcribing…";
      if (recordingStream) recordingStream.getTracks().forEach(track => track.stop());

      try {
        const mimeType = mediaRecorder.mimeType || "audio/webm";
        const blob = new Blob(mediaChunks, {type: mimeType});
        const ext = mimeType.includes("ogg") ? "ogg" : mimeType.includes("mp4") ? "m4a" : "webm";
        const form = new FormData();
        form.append("audio", blob, `recording.${ext}`);
        form.append("scenario", document.getElementById("scenario").value);
        const data = await apiForm("/api/audio/transcribe", form);
        document.getElementById("speakingText").value = data.transcript;
      } catch (e) {
        alert(e.message);
      } finally {
        btn.disabled = false;
        btn.textContent = "🎙 Record answer";
        mediaRecorder = null;
        recordingStream = null;
        mediaChunks = [];
      }
    };

    mediaRecorder.start();
    btn.textContent = "⏹ Stop recording";
    recordingStopTimer = setTimeout(() => {
      if (mediaRecorder && mediaRecorder.state === "recording") mediaRecorder.stop();
    }, 120000);
  } catch (e) {
    alert("Microphone access failed. Allow microphone permission or type the transcript.");
    btn.textContent = "🎙 Record answer";
  }
}

async function checkSpeaking() {
  const text = document.getElementById("speakingText").value.trim();
  const scenario = document.getElementById("scenario").value;
  if (!text) return;
  loading("speakingResult", "Preparing speaking feedback…");
  try {
    const d = await api("/api/speaking", {
      method:"POST",
      body:JSON.stringify({text, scenario})
    });
    roadmapReport("speaking", d.practice_score, d.estimated_level);

    document.getElementById("speakingResult").innerHTML = `
      <div class="d-flex justify-content-between align-items-start flex-wrap gap-2">
        <h4>${esc(d.practice_score)}% · approx. ${esc(d.estimated_level || "—")}</h4>
        <span class="badge text-bg-warning">Pronunciation not assessed</span>
      </div>
      <div class="row g-2 my-3">
        <div class="col-md-4"><div class="card-soft text-center"><div class="small text-secondary">Fluency & coherence</div><div class="skill-score">${esc(d.fluency_coherence)}%</div></div></div>
        <div class="col-md-4"><div class="card-soft text-center"><div class="small text-secondary">Vocabulary</div><div class="skill-score">${esc(d.lexical_resource)}%</div></div></div>
        <div class="col-md-4"><div class="card-soft text-center"><div class="small text-secondary">Grammar</div><div class="skill-score">${esc(d.grammar_accuracy)}%</div></div></div>
      </div>
      <p><strong>Positive point:</strong> ${esc(d.positive_point || "")}</p>
      <p><strong>Correction:</strong> ${esc(d.correction || "")}</p>
      <p><strong>Suggestion:</strong> ${esc(d.suggestion || "")}</p>
      <p><strong>Follow-up:</strong> ${esc(d.follow_up_question || "")}</p>
      <p class="small text-secondary">${esc(d.disclaimer || "Transcript-only practice score; pronunciation is not assessed.")}</p>
      <button class="btn btn-sm btn-outline-primary mt-2"
        onclick="speakText(this.parentElement.innerText, this)">🔊 Listen to feedback</button>`;

    await loadProfile();
  } catch(e) {
    document.getElementById("speakingResult").textContent = e.message;
  }
}
async function generateTest() {
  const level = document.getElementById("testLevel").value;
  const focus = document.getElementById("testFocus").value;
  document.getElementById("testResult").innerHTML = `<div class="loader">Generating test…</div>`;
  try {
    currentTest = await api("/api/test/generate", {method:"POST", body:JSON.stringify({level, focus})});
    renderTest();
  } catch(e) { document.getElementById("testResult").textContent = e.message; }
}

function renderTest() {
  document.getElementById("testResult").innerHTML = `
    <h4>${esc(currentTest.title)}</h4>
    <form id="testForm">
      ${(currentTest.questions || []).map(q => `
        <div class="card-soft my-3">
          <div class="fw-semibold mb-2">${esc(q.id)}. ${esc(q.question)}</div>
          ${(q.options || []).map((o,i)=>`
            <div class="form-check">
              <input class="form-check-input" type="radio" name="q${q.id}" value="${i}" id="q${q.id}_${i}">
              <label class="form-check-label" for="q${q.id}_${i}">${esc(o)}</label>
            </div>`).join("")}
        </div>`).join("")}
      <button type="button" class="btn btn-primary" onclick="submitTest()">Submit test</button>
    </form>`;
}

async function submitTest() {
  if (!currentTest) return;
  const answers = {};
  currentTest.questions.forEach(q => {
    const checked = document.querySelector(`input[name="q${q.id}"]:checked`);
    if (checked) answers[String(q.id)] = Number(checked.value);
  });
  try {
    const d = await api("/api/test/submit", {
      method:"POST",
      body:JSON.stringify({
        title: currentTest.title,
        level: currentTest.level,
        questions: currentTest.questions,
        answers
      })
    });
    roadmapReport("test", d.score);
    const mastery = d.mastery_status
      ? d.mastery_status.charAt(0).toUpperCase() + d.mastery_status.slice(1)
      : "";
    const skillSummary = Object.entries(d.skill_results || {})
      .map(([skill, r]) => `<div class="small">${esc(skill)}: ${esc(r.score)}% (${esc(r.correct)}/${esc(r.total)})</div>`)
      .join("");
    document.getElementById("testResult").innerHTML += `
      <div class="alert alert-primary mt-3">
        <strong>${esc(d.tested_level)} diagnostic: ${esc(d.score)}%</strong>
        (${esc(d.correct)}/${esc(d.total)}) · ${esc(mastery)}
        <div class="small mt-1">A high score confirms performance at the tested level; it does not automatically raise CEFR.</div>
      </div>
      ${skillSummary}
      ${(d.details || []).map(x => `<div class="small py-1">${x.correct ? "✅" : "❌"} Q${esc(x.id)} ${x.skill ? `· ${esc(x.skill)}` : ""} — ${esc(x.explanation)}</div>`).join("")}`;
    await loadProfile();
  } catch(e) { alert(e.message); }
}

(async () => {
  const { data } = await sb.auth.getSession();
  currentSession = data.session;
  if (currentSession) await showApp();
})();

// ========================= IELTS PREPARATION =========================
let currentIELTSWriting = null;
let currentIELTSSpeaking = null;
let currentIELTSReading = null;
let currentIELTSListening = null;
let ieltsRecorder = null;
let ieltsChunks = [];
let ieltsStream = null;

function bandCard(label, value) {
  const shown = value == null ? "—" : Number(value).toFixed(1);
  return `<div class="col-6 col-lg"><div class="card-soft text-center h-100"><div class="small text-secondary">${esc(label)}</div><div class="skill-score">${esc(shown)}</div><div class="small">Band</div></div></div>`;
}

async function loadIELTSDashboard() {
  try {
    const d = await api("/api/ielts/dashboard");
    const p = d.profile || {};
    document.getElementById("ieltsExamType").value = p.exam_type || "Academic";
    document.getElementById("ieltsExamDate").value = p.planned_exam_date || "";
    document.getElementById("ieltsFeedbackLang").value = d.feedback_language || "en";
    document.getElementById("ieltsBandCards").innerHTML = [
      bandCard("Overall", p.current_overall_band), bandCard("Listening", p.listening_band),
      bandCard("Reading", p.reading_band), bandCard("Writing", p.writing_band), bandCard("Speaking", p.speaking_band)
    ].join("");
    document.getElementById("ieltsRecommendation").textContent = d.recommendation || "Complete a practice activity to get a recommendation.";
    loadIELTSAnalytics();
    const recent = [
      ...(d.objective_recent || []).map(x => ({name:`${x.section} · ${x.title}`, band:x.estimated_band, pct:x.score_percent, date:x.created_at})),
      ...(d.writing_recent || []).map(x => ({name:`Writing Task ${x.task_number}`, band:x.estimated_band, date:x.created_at})),
      ...(d.speaking_recent || []).map(x => ({name:`Speaking Part ${x.part}`, band:x.provisional_language_band, date:x.created_at}))
    ].sort((a,b)=>String(b.date).localeCompare(String(a.date))).slice(0,10);
    document.getElementById("ieltsRecent").innerHTML = recent.length
      ? recent.map(x=>`<div class="py-2 border-bottom d-flex justify-content-between"><span>${esc(x.name)}</span><strong>${x.band != null ? esc(Number(x.band).toFixed(1)) : (x.pct != null ? `${esc(x.pct)}%` : "—")}</strong></div>`).join("")
      : `<div class="text-secondary">No IELTS practice yet.</div>`;
  } catch(e) {
    document.getElementById("ieltsBandCards").innerHTML = `<div class="text-danger">${esc(e.message)}</div>`;
  }
}

async function saveIELTSProfile() {
  const payload = {
    exam_type: document.getElementById("ieltsExamType").value,
    planned_exam_date: document.getElementById("ieltsExamDate").value || null,
    feedback_language: document.getElementById("ieltsFeedbackLang").value
  };
  try {
    await api("/api/ielts/profile", {method:"POST", body:JSON.stringify(payload)});
    await loadIELTSDashboard();
  } catch(e) { alert(e.message); }
}

async function generateIELTSWriting() {
  loading("ieltsWritingResult", "Generating IELTS writing task…");
  document.getElementById("ieltsWritingResult").innerHTML = "";
  const payload = {
    test_type: document.getElementById("ieltsWritingType").value,
    task_number: Number(document.getElementById("ieltsWritingTask").value)
  };
  try {
    currentIELTSWriting = await api("/api/ielts/writing/generate", {method:"POST", body:JSON.stringify(payload)});
    document.getElementById("ieltsWritingPromptBox").classList.remove("d-none");
    document.getElementById("ieltsWritingTitle").textContent = currentIELTSWriting.title || "IELTS Writing";
    document.getElementById("ieltsWritingPrompt").textContent = currentIELTSWriting.prompt || "";
    document.getElementById("ieltsWritingChart").innerHTML = "";
    if (currentIELTSWriting.chart) renderTask1Chart("ieltsWritingChart", currentIELTSWriting.chart);
    document.getElementById("ieltsWritingMeta").textContent = `Minimum ${currentIELTSWriting.minimum_words || (payload.task_number===1?150:250)} words · Suggested ${currentIELTSWriting.suggested_minutes || (payload.task_number===1?20:40)} minutes`;
    document.getElementById("ieltsWritingResult").innerHTML = currentIELTSWriting.notice
      ? `<div class="alert alert-info">${esc(currentIELTSWriting.notice)}</div>` : "";
  } catch(e) { document.getElementById("ieltsWritingResult").textContent = e.message; }
}

async function assessIELTSWriting() {
  const response = document.getElementById("ieltsWritingResponse").value.trim();
  const prompt = currentIELTSWriting?.prompt || document.getElementById("ieltsWritingPrompt").innerText.trim();
  if (!prompt) return alert("Generate a task first.");
  if (!response) return alert("Write your response first.");
  loading("ieltsWritingResult", "Assessing IELTS writing…");
  const task = Number(document.getElementById("ieltsWritingTask").value);
  try {
    const d = await api("/api/ielts/writing/assess", {method:"POST", body:JSON.stringify({
      test_type: document.getElementById("ieltsWritingType").value,
      task_number: task, prompt, response, chart: currentIELTSWriting?.chart || null
    })});
    const taskKey = task === 1 ? "task_achievement" : "task_response";
    document.getElementById("ieltsWritingResult").innerHTML = `
      <div class="d-flex justify-content-between align-items-center"><h4>Estimated band ${esc(Number(d.overall_band).toFixed(1))}</h4><span class="badge text-bg-warning">Practice estimate</span></div>
      <div class="row g-2 my-2">
        ${bandCard(task===1?"Task Achievement":"Task Response", d[taskKey])}
        ${bandCard("Coherence & Cohesion", d.coherence_cohesion)}
        ${bandCard("Lexical Resource", d.lexical_resource)}
        ${bandCard("Grammar", d.grammatical_range_accuracy)}
      </div>
      ${d.underlength ? `<div class="alert alert-warning small">Your response is under the minimum length (${task === 1 ? 150 : 250} words). In the exam this lowers ${task === 1 ? "Task Achievement" : "Task Response"}.</div>` : ""}
      <p class="small text-secondary">${esc(d.word_count)} words · ${esc(d.disclaimer)}</p>
      <h6>Strengths</h6><ul>${(d.strengths||[]).map(x=>`<li>${esc(x)}</li>`).join("")}</ul>
      <h6>How to improve</h6>${(d.improvements||[]).map(x=>`<div class="mb-2"><strong>${esc(x.criterion)}:</strong> ${esc(x.issue)}<br><span class="text-secondary">Action: ${esc(x.action)}</span></div>`).join("")}
      <h6 class="mt-3">Language corrections</h6>${(d.error_examples||[]).map(x=>`<div class="mb-2"><code>${esc(x.original)}</code> → <strong>${esc(x.correction)}</strong><br><small>${esc(x.explanation)}</small></div>`).join("") || '<p class="text-secondary">No major examples returned.</p>'}
      <div class="alert alert-light mt-3"><strong>Next task:</strong> ${esc(d.next_task)}</div>
      ${learnPanelHTML("writing")}`;
    ieltsLearnSource.writing = {kind:"writing", prompt, text: response, band: d.overall_band,
      test_type: document.getElementById("ieltsWritingType").value, task_number: task, chart: currentIELTSWriting?.chart || null,
      grammar: (d.error_examples || []).map(x => x.original).filter(Boolean)};
    await loadIELTSDashboard();
  } catch(e) { document.getElementById("ieltsWritingResult").textContent = e.message; }
}

let ieltsSpeakingSession = null;

async function assessIELTSSpeakingSession(sessionId) {
  const result = document.getElementById("ieltsSpeakingResult");
  result.innerHTML = `<div class="loader">Assessing your answers…</div>`;
  let d;
  try {
    d = await api(`/api/ielts/speaking/session/${sessionId}/assess`, {method: "POST", body: "{}"});
  } catch (e) {
    result.innerHTML = `<div class="alert alert-warning">${esc(e.message)}
      <button class="btn btn-sm btn-outline-primary ms-2" onclick="assessIELTSSpeakingSession('${esc(sessionId)}')">Assess again</button></div>`;
    return;
  }
    result.innerHTML = `
      <div class="d-flex justify-content-between"><h4>${d.speaking_band != null
          ? `Speaking band ${esc(Number(d.speaking_band).toFixed(1))}`
          : `Language band ${esc(Number(d.provisional_language_band).toFixed(1))}`}</h4>
        <span class="badge ${d.pronunciation != null ? "text-bg-success" : "text-bg-warning"}">${d.pronunciation != null ? "All 4 criteria · AI estimate" : "Pronunciation could not be assessed"}</span></div>
      <div class="row g-2 my-2">${bandCard("Fluency & Coherence", d.fluency_coherence)}${bandCard("Lexical Resource", d.lexical_resource)}${bandCard("Grammar", d.grammatical_range_accuracy)}${bandCard("Pronunciation", d.pronunciation)}</div>
      <p class="small text-secondary">${esc(d.disclaimer || "")}</p>
      <h6>Strengths</h6><ul>${(d.strengths || []).map(x => `<li>${esc(x)}</li>`).join("")}</ul>
      <h6>Improve</h6>${(d.improvements || []).map(x => `<div class="mb-2"><strong>${esc(x.criterion)}:</strong> ${esc(x.issue)}<br><span class="text-secondary">${esc(x.action)}</span></div>`).join("")}
      ${pronunciationFeedbackHTML(d.pronunciation_feedback)}
      ${(d.better_phrasing || []).length ? `<h6 class="mt-3">Better phrasing</h6>${d.better_phrasing.map(x => `<div class="mb-1"><code>${esc(x.original)}</code> → <strong>${esc(x.improved)}</strong></div>`).join("")}` : ""}
      <details class="mt-3"><summary>Transcript of the conversation</summary><div class="small mt-2" style="white-space:pre-line">${esc(d.transcript || "")}</div></details>
      ${d.prompt ? learnPanelHTML("speaking") : ""}`;
    ieltsLearnSource.speaking = {kind:"speaking", prompt: d.prompt || "", text: d.transcript || "",
      band: d.speaking_band ?? d.provisional_language_band,
      grammar: (d.better_phrasing || []).map(x => x.original).filter(Boolean)};
    await loadIELTSDashboard();
}

function pronunciationFeedbackHTML(p) {
  if (!p) return "";
  return `<div class="card-soft my-3"><h6>Pronunciation · band ${esc(p.pronunciation)}</h6>
    <p class="small mb-2">${esc(p.summary || "")}</p>
    ${(p.strengths || []).length ? `<div class="small mb-1"><strong>Strengths:</strong> ${p.strengths.map(esc).join("; ")}</div>` : ""}
    ${(p.issues || []).map(x => `<div class="small mb-1"><strong>${esc(x.feature)}:</strong> “${esc(x.example)}” — <span class="text-secondary">${esc(x.tip)}</span></div>`).join("")}
    <div class="small text-secondary mt-1">Based on ${esc(p.sample_seconds)} seconds of your recorded answers.</div></div>`;
}

async function startIELTSSpeaking() {
  const part = Number(document.getElementById("ieltsSpeakingPart").value);
  const btn = document.getElementById("ieltsSpeakStartBtn");
  const box = document.getElementById("ieltsSpeakingExaminer");
  const result = document.getElementById("ieltsSpeakingResult");
  result.innerHTML = "";
  try { await examinerPrepare(); }
  catch (e) { return alert(`Microphone access is needed. ${e.message || ""}`); }
  btn.disabled = true;
  box.innerHTML = `<div class="loader">The examiner is getting ready…</div>`;
  try {
    const first = await api("/api/ielts/speaking/session", {method: "POST", body: JSON.stringify({part})});
    ieltsSpeakingSession = first.session_id;
    await runExaminer(box, first, `/api/ielts/speaking/session/${first.session_id}/answer`);
    examinerRelease();
    await assessIELTSSpeakingSession(first.session_id);
  } catch (e) {
    box.insertAdjacentHTML("beforeend", `<div class="alert alert-danger mt-2">${esc(e.message)}</div>`);
  } finally {
    btn.disabled = false;
  }
}

function renderIELTSQuestions(containerId, data, section) {
  const passage = section === "reading" ? `<div class="card-soft mb-3"><h5>${esc(data.title)}</h5><div class="reading-passage">${esc(data.passage).replace(/\n/g,"<br>")}</div></div>` : `<div class="card-soft mb-3"><h5>${esc(data.title)}</h5><p class="text-secondary mb-0">Read the questions, then press Play. The recording is played once — answer while you listen.</p><div id="ieltsListenState" class="small text-secondary mt-1"></div></div>`;
  const standard = q => {
    const opts = (q.options || []).filter(Boolean);
    const instr = q.instruction ? `<div class="small text-secondary mb-1">${esc(q.instruction)}</div>` : "";
    const input = opts.length ? opts.map((o,i)=>`<div class="form-check"><input class="form-check-input" type="radio" name="${section}q${q.id}" value="${esc(o)}" id="${section}q${q.id}_${i}"><label class="form-check-label" for="${section}q${q.id}_${i}">${esc(o)}</label></div>`).join("") : `<input class="form-control" name="${section}q${q.id}" placeholder="Your answer">`;
    return `<div class="card-soft my-3">${instr}<div class="fw-semibold mb-2">${esc(q.id)}. ${esc(q.question)}</div>${input}<div id="${section}fb${q.id}" class="mt-2"></div></div>`;
  };
  const questions = readingGroupsHTML(data, {
    name: q => `${section}q${q.id}`, number: id => id, saved: () => "", standard,
    fbId: q => `${section}fb${q.id}`,
  });
  document.getElementById(containerId).innerHTML = passage + questions + `<button class="btn btn-primary" onclick="submitIELTSObjective('${section}')">Submit ${section}</button><div id="${section}SubmitResult" class="mt-3"></div>`;
}

async function generateIELTSReading() {
  document.getElementById("ieltsReadingContent").innerHTML = `<div class="loader">Generating reading practice…</div>`;
  try {
    currentIELTSReading = await api("/api/ielts/reading/generate", {method:"POST", body:JSON.stringify({
      test_type:document.getElementById("ieltsReadingType").value, focus_type: ieltsFocus.reading
    })});
    renderIELTSQuestions("ieltsReadingContent", currentIELTSReading, "reading");
    if (currentIELTSReading.notice) document.getElementById("ieltsReadingContent").insertAdjacentHTML("afterbegin", `<div class="alert alert-info">${esc(currentIELTSReading.notice)}</div>`);
  } catch(e) { document.getElementById("ieltsReadingContent").textContent=e.message; }
}

async function generateIELTSListening() {
  document.getElementById("ieltsListeningContent").innerHTML = `<div class="loader">Generating listening practice…</div>`;
  try {
    currentIELTSListening = await api("/api/ielts/listening/generate", {method:"POST", body:JSON.stringify({test_type:"Academic", focus_type: ieltsFocus.listening})});
    const btn = document.getElementById("ieltsListenAudioBtn");
    btn.disabled = false;
    btn.textContent = "▶ Play recording (once)";
    renderIELTSQuestions("ieltsListeningContent", currentIELTSListening, "listening");
    if (currentIELTSListening.notice) document.getElementById("ieltsListeningContent").insertAdjacentHTML("afterbegin", `<div class="alert alert-info">${esc(currentIELTSListening.notice)}</div>`);
  } catch(e) { document.getElementById("ieltsListeningContent").textContent=e.message; }
}

async function playIELTSListening() {
  if (!currentIELTSListening?.test_id) return alert("Generate a listening set first.");
  const btn = document.getElementById("ieltsListenAudioBtn");
  const state = document.getElementById("ieltsListenState");
  btn.disabled = true;
  btn.textContent = "Loading recording…";
  try {
    const res = await fetch(`/api/ielts/practice/${currentIELTSListening.test_id}/audio`, {
      headers: {"Authorization": `Bearer ${currentSession.access_token}`}
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      if (res.status !== 403) btn.disabled = false;
      btn.textContent = res.status === 403 ? "Recording already played" : "▶ Play recording (once)";
      throw new Error(d.detail || `Could not load the recording (${res.status}).`);
    }
    const url = URL.createObjectURL(await res.blob());
    const audio = new Audio(url);
    btn.textContent = "Playing…";
    if (state) state.textContent = "The recording is playing. It will not be repeated.";
    audio.onended = () => {
      btn.textContent = "Recording finished";
      if (state) state.textContent = "Recording finished. Complete your answers and submit.";
      URL.revokeObjectURL(url);
    };
    await audio.play();
  } catch (e) {
    if (state) state.textContent = e.message; else alert(e.message);
  }
}

async function submitIELTSObjective(section) {
  const data = section === "reading" ? currentIELTSReading : currentIELTSListening;
  if (!data) return;
  const answers = {};
  (data.questions || []).forEach(q => {
    const radio = document.querySelector(`input[name="${section}q${q.id}"]:checked`);
    const text = document.querySelector(`input.form-control[name="${section}q${q.id}"]`);
    const sel = document.querySelector(`select[name="${section}q${q.id}"]`);
    answers[String(q.id)] = radio ? radio.value : (sel ? sel.value : (text ? text.value : ""));
  });
  const resultId = `${section}SubmitResult`;
  document.getElementById(resultId).innerHTML = `<div class="loader">Scoring…</div>`;
  try {
    const d = await api("/api/ielts/objective/submit", {method:"POST", body:JSON.stringify({
      test_id: data.test_id, answers
    })});
    for (const x of (d.details || [])) {
      const fb = document.getElementById(`${section}fb${x.id}`);
      if (fb) fb.innerHTML = x.correct
        ? `<div class="small text-success"><strong>Correct.</strong> ${esc(x.explanation || "")}</div>`
        : `<div class="small text-danger"><strong>Your answer:</strong> ${esc(x.answer || "—")} · <strong>Correct:</strong> ${esc(x.expected)}<br><span class="text-secondary">${esc(x.explanation || "")}</span></div>`;
    }
    document.querySelectorAll(`#${section === "reading" ? "ieltsReadingContent" : "ieltsListeningContent"} input`).forEach(el => el.disabled = true);
    document.getElementById(resultId).innerHTML = `<div class="alert alert-primary"><strong>${esc(d.correct)}/${esc(d.total)} correct · ${esc(d.score_percent)}%</strong><br><small>${esc(d.note)}</small></div>`
      + (d.transcript ? `<div class="card-soft"><h6>Transcript</h6><div class="small">${esc(d.transcript).replace(/\n/g,"<br>")}</div></div>` : "");
    await loadIELTSDashboard();
  } catch(e) { document.getElementById(resultId).textContent=e.message; }
}

const ieltsWritingResponse = document.getElementById("ieltsWritingResponse");
if (ieltsWritingResponse) ieltsWritingResponse.addEventListener("input", () => {
  const words = ieltsWritingResponse.value.trim() ? ieltsWritingResponse.value.trim().split(/\s+/).length : 0;
  document.getElementById("ieltsWordCount").textContent = `${words} words`;
});

// Extend page navigation with IELTS dashboard refresh.
const originalOpenPage = openPage;
openPage = function(name) {
  originalOpenPage(name);
  if (name === "ielts-dashboard") { loadIELTSDashboard(); loadIELTSBankAdmin(); }
};



// ========================= FULL IELTS MOCK EXAM (computer-delivered format) =========================
// Timing is kept on the server; this page only displays it. Answers are autosaved, and when a
// section's time is up the saved answers are submitted automatically.

let fullMock = null;            // last status from the server
let mockSection = null;         // data of the running section
let mockTimerHandle = null;
let mockClockOffset = 0;        // server time - local time (ms)
let mockSaveTimer = null;
let mockPollTimer = null;
let mockAudioEl = null;         // one audio element, unlocked by the learner's click
let mockListeningRun = 0;       // invalidates old Listening runners
let mockSubmitting = false;

const MOCK_SECTION_LABEL = {listening: "Listening", reading: "Reading", writing: "Writing", speaking: "Speaking"};

function mockNow() { return Date.now() + mockClockOffset; }

let mockHeartbeat = null;

function isSmallDevice() {
  return window.innerWidth < 992 || window.matchMedia("(pointer: coarse)").matches;
}

function showMockDeviceWarning() {
  document.getElementById("mockDeviceWarning")?.classList.toggle("d-none", !isSmallDevice());
}
function mockSyncClock(serverTime) {
  if (serverTime) mockClockOffset = new Date(serverTime).getTime() - Date.now();
}
function mockFmt(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function setMockProgress(active) {
  const order = ["listening", "reading", "writing", "speaking", "results"];
  const ai = order.indexOf(active);
  document.querySelectorAll(".mock-step").forEach(el => {
    const i = order.indexOf(el.dataset.mockStep);
    el.classList.toggle("active", i === ai);
    el.classList.toggle("done", i >= 0 && i < ai);
  });
}

// Countdown to a server deadline; calls onExpire once.
function startMockDeadline(deadlineIso, onExpire, label = "Section time") {
  clearInterval(mockTimerHandle);
  const el = document.getElementById("mockTimer");
  document.getElementById("mockTimerLabel").textContent = label;
  const deadline = new Date(deadlineIso).getTime();
  let fired = false;
  const draw = () => {
    const left = (deadline - mockNow()) / 1000;
    el.textContent = left > 0 ? mockFmt(left) : "TIME";
    el.classList.toggle("time-low", left <= 300);
    if (left <= 0 && !fired) {
      fired = true;
      clearInterval(mockTimerHandle);
      onExpire?.();
    }
  };
  draw();
  mockTimerHandle = setInterval(draw, 1000);
  // Keep the free Render instance awake while a section runs (it sleeps after 15 min without requests).
  clearInterval(mockHeartbeat);
  mockHeartbeat = setInterval(() => fetch("/health").catch(() => {}), 4 * 60 * 1000);
}

function stopMockTimers() {
  clearInterval(mockTimerHandle);
  clearInterval(mockHeartbeat);
  clearTimeout(mockSaveTimer);
  clearTimeout(mockPollTimer);
  mockListeningRun++;
  if (mockAudioEl) { try { mockAudioEl.pause(); } catch {} }
}

function showMockExam() {
  document.getElementById("mockSetup").classList.add("d-none");
  document.getElementById("mockExam").classList.remove("d-none");
  const src = {verified: " · verified exam", backup: " · backup paper from the bank"}[fullMock?.source] || "";
  document.getElementById("mockExamLabel").textContent = `${fullMock?.test_type || "IELTS"} · computer-delivered format${src}`;
}

async function startFullIELTSMock() {
  const test_type = document.getElementById("mockTestType").value;
  const source = document.getElementById("mockSource")?.value || "new";
  const btn = document.getElementById("mockStartBtn");
  if (isSmallDevice() && !confirm("The IELTS exam is taken on a computer. On this device the result will not be reliable. Start anyway?")) return;
  if (btn) { btn.disabled = true; btn.textContent = "Starting…"; }
  try {
    fullMock = await api("/api/ielts/mock/start", {method: "POST", body: JSON.stringify({test_type, source})});
    mockSyncClock(fullMock.server_time);
    localStorage.setItem("activeIELTSMockId", fullMock.mock_id);
    showMockExam();
    routeMock();
  } catch (e) {
    alert(e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Start full mock"; }
  }
}

async function resumeFullIELTSMock() {
  const id = localStorage.getItem("activeIELTSMockId");
  if (!id || mockSection) return;
  try {
    const d = await api(`/api/ielts/mock/${id}`);
    if (d.status !== "in_progress" && d.next_section !== "results") {
      localStorage.removeItem("activeIELTSMockId");
      return;
    }
    if (d.version !== "cd_mock_v2") {
      // A mock started before the exam-format update cannot continue in the new format.
      await api(`/api/ielts/mock/${id}/abandon`, {method: "POST", body: "{}"}).catch(() => {});
      localStorage.removeItem("activeIELTSMockId");
      document.getElementById("mockSetup").insertAdjacentHTML("afterbegin",
        `<div class="alert alert-info">Your previous mock used the old format and was closed. Please start a new one.</div>`);
      return;
    }
    fullMock = d;
    mockSyncClock(d.server_time);
    showMockExam();
    routeMock();
  } catch (e) {
    console.warn("Could not resume IELTS mock:", e);
  }
}

async function refreshMockStatus() {
  fullMock = await api(`/api/ielts/mock/${fullMock.mock_id}`);
  mockSyncClock(fullMock.server_time);
  return fullMock;
}

// Decide what to show next from the server status.
function routeMock() {
  stopMockTimers();
  mockSection = null;
  const next = fullMock.next_section;
  setMockProgress(next);
  if (next === "results") return finishFullIELTSMock();
  if (fullMock.timing?.[next]) return showMockIntro(next, true);  // resume a running section
  showMockIntro(next, false);
}

const MOCK_RULES = {
  listening: [
    "There are 4 parts and 40 questions. Each recording is played <strong>once only</strong>.",
    "Before each part you have time to look at the questions; after Parts 1–3 you have 30 seconds to check.",
    "At the end you have <strong>2 minutes</strong> to review your answers. Then they are submitted automatically.",
    "Type answers directly into the boxes. Spelling counts; keep to the word limit in each instruction.",
    "Use headphones and do not reload the page — a recording that has started cannot be replayed.",
  ],
  reading: [
    "There are 3 passages and 40 questions. You have <strong>60 minutes</strong>; there is no extra transfer time.",
    "Use the tabs to move between passages. Your answers are saved automatically.",
    "When the time is up, your answers are submitted automatically.",
  ],
  writing: [
    "There are 2 tasks. You have <strong>60 minutes</strong> in total. Spend about 20 minutes on Task 1 and 40 minutes on Task 2.",
    "Task 1: at least 150 words. Task 2: at least 250 words. Task 2 counts twice as much as Task 1.",
    "Your text is saved automatically and submitted when the time is up.",
  ],
  speaking: [
    "The Speaking test has 3 parts and takes 11–14 minutes.",
    "The examiner asks the questions aloud — they are not shown on screen. Answer by voice; the microphone opens automatically.",
    "Part 2: you get a topic card and 1 minute to prepare (you can make notes), then speak for up to 2 minutes.",
    "Part 3: the examiner's questions follow what you say. Use headphones and a quiet room.",
    "All four criteria are assessed, including Pronunciation from your recorded voice.",
  ],
};

function showMockIntro(section, resuming) {
  const ws = document.getElementById("mockWorkspace");
  document.getElementById("mockTimer").textContent = "--:--";
  document.getElementById("mockTimerLabel").textContent = "Section time";
  const prep = fullMock.prep || {};
  const listeningReady = !!fullMock.listening_plan;
  const notReady = section === "listening" && !listeningReady;
  const prepLine = prep.status === "error"
    ? `<div class="alert alert-danger">Preparation failed: ${esc(prep.error || "")}
         <button class="btn btn-sm btn-outline-danger ms-2" onclick="retryMockPrep()">Try again</button></div>`
    : (prep.status !== "ready"
        ? `<div class="small text-secondary mb-3"><span class="spinner-border spinner-border-sm me-2"></span>
             Preparing your exam paper in the background${prep.step ? ` · ${esc(prep.step)}` : ""}…</div>` : "");
  ws.innerHTML = `
    <div class="card-soft">
      <h4>${esc(MOCK_SECTION_LABEL[section])}${resuming ? " · continue" : ""}</h4>
      ${resuming
        ? `<p>This section is already running on the server and its clock has not stopped. Press continue to return to it.</p>`
        : `<ul>${MOCK_RULES[section].map(r => `<li class="mb-1">${r}</li>`).join("")}</ul>`}
      ${prepLine}
      <button id="mockEnterBtn" class="btn btn-primary" ${notReady ? "disabled" : ""}
        onclick="enterMockSection('${section}')">${resuming ? "Continue" : `Start ${esc(MOCK_SECTION_LABEL[section])}`}</button>
      ${notReady ? `<span class="small text-secondary ms-2">The recordings are being prepared…</span>` : ""}
    </div>`;
  if (prep.status !== "ready" && prep.status !== "error") {
    mockPollTimer = setTimeout(async () => {
      if (mockSection) return;
      try { await refreshMockStatus(); } catch { return; }
      if (!mockSection) showMockIntro(fullMock.next_section, !!fullMock.timing?.[fullMock.next_section]);
    }, 4000);
  }
}

async function retryMockPrep() {
  fullMock = await api(`/api/ielts/mock/${fullMock.mock_id}/prepare`, {method: "POST", body: "{}"});
  routeMock();
}

async function enterMockSection(section) {
  const btn = document.getElementById("mockEnterBtn");
  if (btn) { btn.disabled = true; btn.textContent = "Opening…"; }
  if (section === "speaking") {
    try { await examinerPrepare(); }
    catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = "Try again"; }
      return alert(`Microphone access is needed for the Speaking test. ${e.message || ""}`);
    }
  }
  if (section === "listening") {
    // Unlock audio with this click so later recordings may start automatically.
    mockAudioEl = mockAudioEl || new Audio();
    mockAudioEl.muted = true;
    mockAudioEl.play().catch(() => {});
    mockAudioEl.pause();
    mockAudioEl.muted = false;
  }
  try {
    const d = await api(`/api/ielts/mock/${fullMock.mock_id}/section/start`, {
      method: "POST", body: JSON.stringify({section})
    });
    fullMock = d;
    mockSyncClock(d.server_time);
    mockSection = d;
    setMockProgress(section);
    if (section === "listening") renderMockListening(d);
    else if (section === "reading") renderMockReading(d);
    else if (section === "writing") renderMockWriting(d);
    else renderMockSpeaking(d);
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = "Try again"; }
    alert(e.message);
  }
}

// ---------- objective questions (Listening / Reading) ----------

function mockQuestionHTML(section, part, q, offset, saved) {
  const name = `mock_${section}_${part}_${q.id}`;
  const number = offset + Number(q.id);
  const value = saved?.[String(q.id)] ?? "";
  const opts = (q.options || []).filter(Boolean);
  const control = opts.length
    ? opts.map((o, i) => `
        <div class="form-check">
          <input class="form-check-input" type="radio" name="${name}" value="${esc(o)}" id="${name}_${i}" ${value === o ? "checked" : ""}>
          <label class="form-check-label" for="${name}_${i}">${esc(o)}</label>
        </div>`).join("")
    : `<input class="form-control mock-answer" name="${name}" value="${esc(value)}" autocomplete="off" spellcheck="false">`;
  return `<div class="mock-question" id="mockq_${section}_${number}">
    ${q.instruction ? `<div class="small text-secondary mb-1">${esc(q.instruction)}</div>` : ""}
    <div class="fw-semibold mb-2"><span class="mock-qnum">${number}</span> ${esc(q.question)}</div>
    ${control}
  </div>`;
}

function collectMockObjective(section) {
  const answers = {};
  for (const p of (mockSection?.parts || [])) {
    const partAnswers = {};
    for (const q of (p.content.questions || [])) {
      const name = `mock_${section}_${p.part}_${q.id}`;
      const radio = document.querySelector(`input[name="${name}"]:checked`);
      const text = document.querySelector(`input[name="${name}"]:not([type="radio"])`);
      const sel = document.querySelector(`select[name="${name}"]`);
      partAnswers[String(q.id)] = radio ? radio.value : (sel ? sel.value : (text ? text.value.trim() : ""));
    }
    answers[String(p.part)] = partAnswers;
  }
  return answers;
}

function collectMockAnswers(section) {
  if (section === "writing") {
    return {"1": document.getElementById("mockWriting1")?.value || "",
            "2": document.getElementById("mockWriting2")?.value || ""};
  }
  return collectMockObjective(section);
}

function scheduleMockSave() {
  clearTimeout(mockSaveTimer);
  mockSaveTimer = setTimeout(saveMockAnswers, 1500);
}

async function saveMockAnswers() {
  if (!mockSection || mockSubmitting) return;
  const section = mockSection.section;
  try {
    await api(`/api/ielts/mock/${fullMock.mock_id}/answers`, {
      method: "POST", body: JSON.stringify({section, answers: collectMockAnswers(section)})
    });
    const s = document.getElementById("mockSaveState");
    if (s) s.textContent = `Saved ${new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}`;
  } catch {}
}

function bindMockAutosave() {
  document.getElementById("mockWorkspace").addEventListener("input", scheduleMockSave);
  document.getElementById("mockWorkspace").addEventListener("change", scheduleMockSave);
}

function mockPartTabs(section, parts, label) {
  return `<ul class="nav nav-tabs mb-3" role="tablist">
    ${parts.map((p, i) => `<li class="nav-item"><button class="nav-link ${i === 0 ? "active" : ""}" data-mock-tab="${p.part}"
       onclick="showMockPart(${p.part})">${label} ${p.part}</button></li>`).join("")}
  </ul>`;
}

function showMockPart(part) {
  document.querySelectorAll("[data-mock-tab]").forEach(b => b.classList.toggle("active", Number(b.dataset.mockTab) === part));
  document.querySelectorAll("[data-mock-pane]").forEach(el => el.classList.toggle("d-none", Number(el.dataset.mockPane) !== part));
}

async function submitMockSection(auto = false) {
  if (!mockSection || mockSubmitting) return;
  const section = mockSection.section;
  if (!auto && !confirm(`Finish ${MOCK_SECTION_LABEL[section]}? You cannot return to it.`)) return;
  mockSubmitting = true;
  stopMockTimers();
  const ws = document.getElementById("mockWorkspace");
  const answers = collectMockAnswers(section);
  ws.innerHTML = `<div class="loader">${auto ? "Time is up. " : ""}Submitting ${esc(MOCK_SECTION_LABEL[section])}…</div>`;
  try {
    fullMock = await api(`/api/ielts/mock/${fullMock.mock_id}/section/submit`, {
      method: "POST", body: JSON.stringify({section, answers})
    });
    mockSyncClock(fullMock.server_time);
    mockSection = null;
    ws.innerHTML = `<div class="card-soft"><h5>${esc(MOCK_SECTION_LABEL[section])} submitted</h5>
      <p class="text-secondary mb-0">As in the real exam, results are shown only at the end.</p></div>`;
    setTimeout(routeMock, 1500);
  } catch (e) {
    ws.innerHTML = `<div class="alert alert-danger">${esc(e.message)}
      <button class="btn btn-sm btn-outline-danger ms-2" onclick="location.reload()">Reload</button></div>`;
  } finally {
    mockSubmitting = false;
  }
}

// ---------- Listening ----------

function renderMockListening(d) {
  const ws = document.getElementById("mockWorkspace");
  ws.innerHTML = `
    <div class="mock-audio-box mb-3">
      <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
        <div><strong id="mockListenPhase">Listening</strong><div id="mockListenHint" class="small text-secondary"></div></div>
        <div class="fw-semibold" id="mockPhaseClock"></div>
      </div>
      <div class="progress mt-2" style="height:6px"><div id="mockAudioProgress" class="progress-bar" style="width:0%"></div></div>
    </div>
    ${mockPartTabs("listening", d.parts, "Part")}
    ${d.parts.map((p, i) => `<div data-mock-pane="${p.part}" class="${i === 0 ? "" : "d-none"}">
        <h5 class="mb-2">Part ${p.part} · Questions ${(p.part - 1) * 10 + 1}–${p.part * 10}</h5>
        ${(p.content.questions || []).map(q => mockQuestionHTML("listening", p.part, q, (p.part - 1) * 10, p.answers)).join("")}
      </div>`).join("")}
    <div class="d-flex justify-content-between align-items-center mt-3">
      <span id="mockSaveState" class="small text-secondary"></span>
      <button class="btn btn-outline-primary" onclick="submitMockSection(false)">Finish Listening</button>
    </div>`;
  bindMockAutosave();
  startMockDeadline(d.timing.deadline, () => submitMockSection(true), "Listening time");
  runMockListening(d);
}

function mockCountdown(seconds, runId, label, hint) {
  return new Promise(resolve => {
    const end = Date.now() + seconds * 1000;
    document.getElementById("mockListenPhase").textContent = label;
    document.getElementById("mockListenHint").textContent = hint || "";
    const tick = () => {
      if (runId !== mockListeningRun) return resolve(false);
      const left = (end - Date.now()) / 1000;
      const clock = document.getElementById("mockPhaseClock");
      if (clock) clock.textContent = mockFmt(left);
      if (left <= 0) return resolve(true);
      setTimeout(tick, 250);
    };
    tick();
  });
}

async function playMockPart(part, runId) {
  document.getElementById("mockListenPhase").textContent = `Part ${part} · recording`;
  document.getElementById("mockListenHint").textContent = "Answer the questions while you listen. The recording is played once.";
  document.getElementById("mockPhaseClock").textContent = "▶";
  const bar = document.getElementById("mockAudioProgress");
  bar.style.width = "0%";
  let blob = null;
  for (let attempt = 0; attempt < 2 && !blob; attempt++) {
    try {
      const res = await fetch(`/api/ielts/mock/${fullMock.mock_id}/listening/${part}/audio`, {
        headers: {"Authorization": `Bearer ${currentSession.access_token}`}
      });
      if (res.status === 403) {
        const d = await res.json().catch(() => ({}));
        document.getElementById("mockListenHint").textContent = d.detail || "This recording has already been played.";
        return;
      }
      if (res.ok) blob = await res.blob();
    } catch {}
  }
  if (!blob || runId !== mockListeningRun) return;
  const url = URL.createObjectURL(blob);
  const audio = mockAudioEl || new Audio();
  await new Promise(resolve => {
    audio.onended = () => resolve();
    audio.onerror = () => resolve();
    audio.ontimeupdate = () => {
      if (audio.duration && isFinite(audio.duration)) bar.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
      if (runId !== mockListeningRun) { audio.pause(); resolve(); }
    };
    audio.src = url;
    audio.play().catch(() => {
      document.getElementById("mockListenHint").innerHTML =
        `The browser blocked playback. <button class="btn btn-sm btn-primary ms-1" id="mockUnblock">▶ Play</button>`;
      document.getElementById("mockUnblock").onclick = () => audio.play();
    });
  });
  URL.revokeObjectURL(url);
  bar.style.width = "100%";
}

async function runMockListening(d) {
  const runId = ++mockListeningRun;
  const served = d.listening_served || {};
  const firstUnplayed = [1, 2, 3, 4].find(p => !served[String(p)]);
  if (firstUnplayed) {
    for (let part = firstUnplayed; part <= 4; part++) {
      showMockPart(part);
      const q1 = (part - 1) * 10 + 1;
      if (!await mockCountdown(30, runId, `Part ${part} · look at the questions`, `Questions ${q1}–${q1 + 9}. The recording starts automatically.`)) return;
      await playMockPart(part, runId);
      if (runId !== mockListeningRun) return;
      if (part < 4 && !await mockCountdown(30, runId, `Part ${part} · check your answers`, "")) return;
    }
  }
  // Review time: 2 minutes, but never beyond the server deadline.
  const left = Math.min(120, (new Date(d.timing.deadline).getTime() - mockNow()) / 1000 - 5);
  await saveMockAnswers();
  if (!await mockCountdown(Math.max(0, left), runId, "Review your answers",
      "You have 2 minutes to check all 40 answers. They will then be submitted automatically.")) return;
  submitMockSection(true);
}

// ---------- Reading ----------

function renderMockReading(d) {
  const ws = document.getElementById("mockWorkspace");
  let offset = 0;
  const panes = d.parts.map((p, i) => {
    const html = `<div data-mock-pane="${p.part}" class="${i === 0 ? "" : "d-none"}">
      <div class="row g-3">
        <div class="col-lg-6"><div class="card-soft mock-reading-passage">
          <h5>${esc(p.content.title || `Passage ${p.part}`)}</h5>
          <div class="mock-passage">${esc(p.content.passage || "").replace(/\n/g, "<br>")}</div></div></div>
        <div class="col-lg-6 mock-reading-questions">
          ${(o => readingGroupsHTML(p.content, {
              name: q => `mock_reading_${p.part}_${q.id}`, number: id => o + Number(id),
              saved: id => p.answers?.[String(id)], fbId: () => "",
              standard: q => mockQuestionHTML("reading", p.part, q, o, p.answers),
            }, o))(offset)}
        </div>
      </div></div>`;
    offset += (p.content.questions || []).length;
    return html;
  }).join("");
  ws.innerHTML = `${mockPartTabs("reading", d.parts, "Passage")}${panes}
    <div class="d-flex justify-content-between align-items-center mt-3">
      <span id="mockSaveState" class="small text-secondary"></span>
      <button class="btn btn-primary" onclick="submitMockSection(false)">Finish Reading</button>
    </div>`;
  bindMockAutosave();
  startMockDeadline(d.timing.deadline, () => submitMockSection(true), "Reading time");
}

// ---------- Writing ----------

function renderMockWriting(d) {
  const ws = document.getElementById("mockWorkspace");
  const task = n => d.parts.find(p => p.part === n) || {content: {}, answers: {}};
  const pane = n => {
    const t = task(n);
    return `<div data-mock-pane="${n}" class="${n === 1 ? "" : "d-none"}">
      <div class="row g-3">
        <div class="col-lg-5"><div class="card-soft">
          <h5>${esc(t.content.title || `Task ${n}`)}</h5>
          ${n === 1 && t.content.chart ? `<div id="mockChart1" class="mb-2"></div>` : ""}
          <p style="white-space:pre-line">${esc(t.content.prompt || "")}</p>
          <div class="small text-secondary">Write at least ${esc(t.content.minimum_words || (n === 1 ? 150 : 250))} words. Suggested time: ${esc(t.content.suggested_minutes || (n === 1 ? 20 : 40))} minutes.</div>
        </div></div>
        <div class="col-lg-7">
          <textarea id="mockWriting${n}" class="form-control mock-writing" rows="18" spellcheck="false"
            placeholder="Write Task ${n} here…">${esc(t.answers?.response || "")}</textarea>
          <div id="mockWords${n}" class="small text-secondary text-end mt-1">0 words</div>
        </div>
      </div></div>`;
  };
  ws.innerHTML = `${mockPartTabs("writing", d.parts, "Task")}${pane(1)}${pane(2)}
    <div class="d-flex justify-content-between align-items-center mt-3">
      <span id="mockSaveState" class="small text-secondary"></span>
      <button class="btn btn-primary" onclick="submitMockSection(false)">Finish Writing</button>
    </div>`;
  if (typeof renderTask1Chart === "function") {
    const c = task(1).content.chart;
    if (c) renderTask1Chart("mockChart1", c);
  }
  const wc = n => {
    const el = document.getElementById(`mockWriting${n}`);
    const draw = () => {
      const words = el.value.trim() ? el.value.trim().split(/\s+/).length : 0;
      document.getElementById(`mockWords${n}`).textContent = `${words} words`;
    };
    el.addEventListener("input", draw);
    el.addEventListener("paste", e => e.preventDefault());  // no pasting, as on the exam computer
    draw();
  };
  wc(1); wc(2);
  bindMockAutosave();
  startMockDeadline(d.timing.deadline, () => submitMockSection(true), "Writing time");
}

// ---------- Speaking (with the examiner) ----------

async function renderMockSpeaking(d) {
  const ws = document.getElementById("mockWorkspace");
  ws.innerHTML = `<h4>IELTS Speaking · Parts 1–3</h4>
    <p class="text-secondary small">Listen to the examiner and answer aloud. The microphone opens automatically when the examiner finishes.
      You may ask the examiner to repeat a question.</p>
    <div id="mockExaminer"></div>`;
  startMockDeadline(d.timing.deadline, null, "Speaking time");
  try {
    const first = await api(`/api/ielts/mock/${fullMock.mock_id}/speaking/turn`, {method: "POST", body: "{}"});
    await runExaminer(document.getElementById("mockExaminer"), first, `/api/ielts/mock/${fullMock.mock_id}/speaking/answer`);
    examinerRelease();
    ws.insertAdjacentHTML("beforeend", `<div class="loader mt-3">Submitting the Speaking test…</div>`);
    fullMock = await api(`/api/ielts/mock/${fullMock.mock_id}/speaking/submit`, {method: "POST", body: "{}"});
    stopMockTimers();
    mockSection = null;
    routeMock();
  } catch (e) {
    ws.insertAdjacentHTML("beforeend", `<div class="alert alert-danger mt-3">${esc(e.message)}
      <button class="btn btn-sm btn-outline-danger ms-2" onclick="location.reload()">Reload</button></div>`);
  }
}

// ---------- Results ----------

function mockObjectiveReview(section, review) {
  let offset = 0;
  return Object.entries(review || {}).map(([part, r]) => {
    const details = r.result?.details || [];
    const rows = details.map(x => {
      const n = offset + Number(x.id);
      return `<tr class="${x.correct ? "" : "table-warning"}"><td>${n}</td><td>${esc(x.answer || "—")}</td>
        <td>${esc((x.accepted || []).join(" / "))}</td><td>${x.correct ? "✅" : "❌"}</td>
        <td class="small text-secondary">${esc(x.explanation || "")}</td></tr>`;
    }).join("");
    offset += details.length;
    return `<details class="mb-2"><summary>${section === "listening" ? "Part" : "Passage"} ${esc(part)}: ${esc(r.raw_score ?? "—")}/${esc(r.total ?? "—")} ${r.title ? `· ${esc(r.title)}` : ""}</summary>
      <div class="table-responsive mt-2"><table class="table table-sm align-middle"><thead><tr><th>#</th><th>Your answer</th><th>Correct</th><th></th><th>Why</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${r.transcript ? `<div class="small"><strong>Transcript</strong><div class="text-secondary" style="white-space:pre-line">${esc(r.transcript)}</div></div>` : ""}
    </details>`;
  }).join("");
}

async function finishFullIELTSMock() {
  if (!fullMock) return;
  stopMockTimers();
  setMockProgress("results");
  const ws = document.getElementById("mockWorkspace");
  ws.innerHTML = `<div class="loader">Calculating your result…</div>`;
  document.getElementById("mockTimer").textContent = "--:--";
  try {
    const d = await api(`/api/ielts/mock/${fullMock.mock_id}/finish`, {method: "POST", body: "{}"});
    if (d.overall_band == null) {  // Writing/Speaking are waiting for the AI examiner
      const b = d.bands || {};
      const card = (label, v) => v == null
        ? `<div class="col-6 col-lg"><div class="card-soft text-center h-100"><div class="small text-secondary">${esc(label)}</div><div class="skill-score">⏳</div><div class="small">pending</div></div></div>`
        : bandCard(label, v);
      ws.innerHTML = `
        <div class="alert alert-info">${esc(d.detail || "Assessing…")}</div>
        <div class="row g-3 mb-4">${card("Listening", b.listening)}${card("Reading", b.reading)}${card("Writing", b.writing)}${card("Speaking", b.speaking)}</div>
        <div class="card-soft mb-3"><h5>Listening review</h5>${mockObjectiveReview("listening", d.review?.listening)}</div>
        <div class="card-soft mb-3"><h5>Reading review</h5>${mockObjectiveReview("reading", d.review?.reading)}</div>
        <p class="small text-secondary">This page checks again every 30 seconds. You can also close it and come back later — the result is kept.</p>`;
      mockPollTimer = setTimeout(finishFullIELTSMock, 30000);
      return;
    }
    const b = d.bands || {};
    const w = d.review?.writing || {};
    const sp = d.review?.speaking?.["1"]?.result || {};
    const spText = [1, 2, 3].map(p => d.review?.speaking?.[String(p)]?.transcript).filter(Boolean).join("\n\n");
    ws.innerHTML = `
      <div class="text-center mb-4">
        <div class="small text-secondary">PRACTICE OVERALL BAND</div>
        <div class="mock-band">${Number(d.overall_band).toFixed(1)}</div>
        <div class="text-secondary">${esc(fullMock.test_type || "IELTS")} Full Mock</div>
      </div>
      <div class="row g-3 mb-4">
        ${bandCard("Listening", b.listening)}${bandCard("Reading", b.reading)}
        ${bandCard("Writing", b.writing)}${bandCard(sp.pronunciation != null ? "Speaking" : "Speaking*", b.speaking)}
      </div>
      <div class="alert alert-warning small">${esc(d.disclaimer)}</div>
      <div class="card-soft mb-3"><h5>Listening review</h5>${mockObjectiveReview("listening", d.review?.listening)}</div>
      <div class="card-soft mb-3"><h5>Reading review</h5>${mockObjectiveReview("reading", d.review?.reading)}</div>
      <div class="card-soft mb-3"><h5>Writing</h5>
        ${[1, 2].map(n => {
          const r = w[String(n)]?.result || {};
          const wt = w[String(n)] || {};
          if (wt.prompt && String(wt.response || "").trim().split(/\s+/).length >= 20)
            ieltsLearnSource[`mock${n}`] = {kind: "writing", prompt: wt.prompt, text: wt.response, band: r.overall_band,
              test_type: fullMock.test_type || "Academic", task_number: n, chart: wt.chart || null, grammar: (r.error_examples || []).map(x => x.original).filter(Boolean)};
          return `<details class="mb-2"><summary>Task ${n}: band ${esc(r.overall_band ?? "—")}</summary>
            <div class="small mt-2">${(r.improvements || []).map(x => `<div class="mb-1"><strong>${esc(x.criterion)}:</strong> ${esc(x.issue)} — <span class="text-secondary">${esc(x.action)}</span></div>`).join("")}</div>
            ${ieltsLearnSource[`mock${n}`] ? learnPanelHTML(`mock${n}`) : ""}
          </details>`;
        }).join("")}
      </div>
      <div class="card-soft mb-3"><h5>Speaking</h5>
        ${(sp.improvements || []).map(x => `<div class="small mb-1"><strong>${esc(x.criterion)}:</strong> ${esc(x.issue)} — <span class="text-secondary">${esc(x.action)}</span></div>`).join("") || '<div class="small text-secondary">No feedback.</div>'}
        ${pronunciationFeedbackHTML(sp.pronunciation_feedback)}
        ${spText ? `<details class="mt-2"><summary>Transcript of the interview</summary><div class="small mt-2" style="white-space:pre-line">${esc(spText)}</div></details>` : ""}
      </div>
      <button class="btn btn-primary" onclick="resetFullIELTSMock()">Start another full mock</button>
      <button class="btn btn-outline-primary ms-2" onclick="openPage('ielts-dashboard')">IELTS Dashboard</button>`;
    localStorage.removeItem("activeIELTSMockId");
    await loadIELTSDashboard();
  } catch (e) { ws.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; }
}

function resetFullIELTSMock() {
  stopMockTimers();
  fullMock = null;
  mockSection = null;
  localStorage.removeItem("activeIELTSMockId");
  location.reload();
}

// Extend navigation so an in-progress full mock is resumed when the page is opened.
const openPageBeforeFullMock = openPage;
openPage = function(name) {
  openPageBeforeFullMock(name);
  if (name === "ielts-mock") { showMockDeviceWarning(); resumeFullIELTSMock(); }
};

window.addEventListener("beforeunload", e => {
  if (mockSection) { saveMockAnswers(); e.preventDefault(); e.returnValue = ""; }
});


/* ===================== Listening Practice (General English) ===================== */

let currentListening = null;      // {test_id, title, level, questions, max_plays, ...}
let listeningAudio = null;        // HTMLAudioElement currently playing
let listeningPlaysLeft = 0;
let listeningLevelTouched = false;
let listeningChecked = false;

const LISTENING_TYPE_LABEL = {
  gist: "Main idea",
  detail: "Detail",
  tfng: "True / False / Not Given",
  gap: "Gap fill"
};

document.getElementById("listeningLevel")?.addEventListener("change", () => { listeningLevelTouched = true; });

function suggestListeningLevel() {
  const hint = document.getElementById("listeningLevelHint");
  const select = document.getElementById("listeningLevel");
  if (!select || !hint) return;
  const skills = currentProfile?.skills || [];
  const listening = skills.find(s => s.category === "listening");
  let level = null, source = "";
  if (listening && Number(listening.attempts || 0) > 0 && listening.level) {
    level = listening.level; source = "your current Listening level";
  } else if (currentProfile?.profile?.cefr_level) {
    level = currentProfile.profile.cefr_level; source = "your overall level";
  } else {
    level = "B1"; source = "default level, Listening is not assessed yet";
  }
  if (!listeningLevelTouched) select.value = level;
  hint.textContent = `Suggested: ${level} (${source}). You can choose another level.`;
  loadListeningBankPanel();
}

/* Admin: backup bank of ready-made tests (shown only to ADMIN_EMAILS) */
let listeningBankTimer = null;

async function loadListeningBankPanel() {
  const box = document.getElementById("listeningAdmin");
  if (!box || !currentSession) return;
  if (!currentProfile?.is_admin) { box.innerHTML = ""; return; }  // students never see the panel
  let d;
  try {
    const res = await fetch("/api/listening/bank/status", {
      headers: {"Authorization": `Bearer ${currentSession.access_token}`}
    });
    d = await res.json().catch(() => ({}));
    if (!res.ok) d = {error: d.detail || `Backup bank status failed (${res.status}).`};
  } catch (e) { d = {error: e.message}; }
  if (d.error) {
    box.innerHTML = `<div class="card-soft"><h5 class="mb-1">Backup bank <span class="badge text-bg-secondary">admin</span></h5>
      <div class="alert alert-warning small mb-0 mt-2">${esc(d.error)}</div></div>`;
    return;
  }
  const per = d.per_level || 5;
  box.innerHTML = `
    <div class="card-soft">
      <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
        <div>
          <h5 class="mb-1">Backup bank <span class="badge text-bg-secondary">admin</span></h5>
          <div class="small text-secondary">Ready-made tests used only when OpenAI is unavailable. Up to ${per} per level.</div>
        </div>
        <button class="btn btn-outline-primary btn-sm" ${d.running ? "disabled" : ""} onclick="fillListeningBank()">
          ${d.running ? "Filling…" : "Fill missing tests"}
        </button>
      </div>
      <div class="d-flex flex-wrap gap-2 mt-3">
        ${Object.entries(d.counts || {}).map(([lvl, n]) =>
          `<span class="badge ${n >= per ? "text-bg-success" : "text-bg-light"}">${esc(lvl)}: ${esc(n)}/${per}</span>`).join("")}
      </div>
      ${d.message ? `<div class="small text-secondary mt-2">${esc(d.message)} · created ${esc(d.done)}, errors ${esc(d.failed)}</div>` : ""}
    </div>`;
  clearTimeout(listeningBankTimer);
  if (d.running) listeningBankTimer = setTimeout(loadListeningBankPanel, 5000);
}

async function fillListeningBank() {
  try {
    await api("/api/listening/bank/fill", {method: "POST"});
  } catch (e) { alert(e.message); }
  loadListeningBankPanel();
}

function stopListeningAudio() {
  if (listeningAudio) {
    listeningAudio.pause();
    if (listeningAudio.src?.startsWith("blob:")) URL.revokeObjectURL(listeningAudio.src);
    listeningAudio = null;
  }
  document.getElementById("listeningPauseBtn")?.classList.add("d-none");
  updateListeningPlayInfo();
}

async function generateListening(useBackup = false) {
  const btn = document.getElementById("listeningGenerateBtn");
  const area = document.getElementById("listeningArea");
  stopListeningAudio();
  btn.disabled = true;
  btn.textContent = "Generating…";
  area.innerHTML = `<div class="loader">${useBackup === true ? "Loading a backup test…" : "Writing the recording and 10 questions…"}</div>`;
  try {
    currentListening = await api("/api/listening/generate", {
      method: "POST",
      body: JSON.stringify({
        level: document.getElementById("listeningLevel").value,
        format: document.getElementById("listeningFormat").value,
        topic: document.getElementById("listeningTopic").value.trim(),
        use_backup: useBackup === true
      })
    });
    listeningPlaysLeft = currentListening.max_plays || 3;
    listeningChecked = false;
    renderListening();
  } catch (e) {
    area.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate";
  }
}

function renderListening() {
  const t = currentListening;
  const formatLabel = {dialogue: "Dialogue", monologue: "Monologue", announcement: "Announcement"}[t.format] || t.format;
  document.getElementById("listeningArea").innerHTML = `
    ${t.backup ? `<div class="alert alert-info"><strong>Backup test.</strong> ${esc(t.notice || "")}</div>` : ""}
    <div class="card-soft listening-player mb-3">
      <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
        <div>
          <h4 class="mb-1">${esc(t.title)}</h4>
          <div class="small text-secondary">${esc(t.level)} · ${esc(formatLabel)} · 10 questions</div>
        </div>
        <div class="d-flex gap-2 align-items-center">
          <button id="listeningPlayBtn" class="btn btn-primary" onclick="playListening()">▶ Play</button>
          <button id="listeningPauseBtn" class="btn btn-outline-secondary d-none" onclick="toggleListeningPause()">⏸ Pause</button>
        </div>
      </div>
      <div class="progress mt-3" style="height:6px"><div id="listeningProgress" class="progress-bar" style="width:0%"></div></div>
      <div id="listeningPlayInfo" class="small text-secondary mt-2"></div>
    </div>

    <form id="listeningForm" onsubmit="return false">
      ${(t.questions || []).map((q, idx) => {
        const name = `listen_q_${q.id}`;
        const control = q.type === "gap"
          ? `<input class="form-control mt-2" name="${name}" autocomplete="off" placeholder="Write the missing word(s)">`
          : (q.options || []).map((opt, i) => `
              <div class="form-check mt-2">
                <input class="form-check-input" type="radio" name="${name}" id="${name}_${i}" value="${esc(opt)}">
                <label class="form-check-label" for="${name}_${i}">${esc(opt)}</label>
              </div>`).join("");
        return `
          <div class="card-soft my-3">
            <div class="small text-secondary">${idx + 1}/10 · ${esc(LISTENING_TYPE_LABEL[q.type] || q.type)}</div>
            <div class="fw-semibold mt-1">${esc(q.question)}</div>
            ${control}
            <div id="listen_feedback_${q.id}" class="mt-2"></div>
          </div>`;
      }).join("")}
      <button id="listeningSubmitBtn" type="button" class="btn btn-primary" onclick="submitListening()">Check answers</button>
    </form>
    <div id="listeningResult" class="mt-4"></div>`;
  updateListeningPlayInfo();
}

function updateListeningPlayInfo() {
  const info = document.getElementById("listeningPlayInfo");
  const btn = document.getElementById("listeningPlayBtn");
  if (!info || !btn) return;
  if (listeningChecked) {
    info.textContent = "Review mode: you can listen as many times as you like.";
    if (listeningAudio) return;
    btn.disabled = false;
    btn.textContent = "▶ Play again";
    return;
  }
  info.textContent = `Plays left: ${listeningPlaysLeft} of ${currentListening.max_plays || 3}. The first play may take a few seconds while the audio is prepared.`;
  if (listeningAudio) return;  // a play is in progress: keep the "Playing…" state
  btn.disabled = listeningPlaysLeft <= 0;
  btn.textContent = listeningPlaysLeft <= 0 ? "No plays left" : "▶ Play";
}

async function playListening() {
  if (!currentListening) return;
  const btn = document.getElementById("listeningPlayBtn");
  const pauseBtn = document.getElementById("listeningPauseBtn");
  const bar = document.getElementById("listeningProgress");
  stopListeningAudio();
  btn.disabled = true;
  btn.textContent = "Loading audio…";
  try {
    const res = await fetch(`/api/listening/${currentListening.test_id}/audio`, {
      headers: {"Authorization": `Bearer ${currentSession.access_token}`}
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      if (res.status === 403) listeningPlaysLeft = 0;
      const err = new Error(d.detail || `Could not load the audio (${res.status}).`);
      err.audioFailed = res.status === 502;
      throw err;
    }
    const left = res.headers.get("X-Plays-Left");
    if (left !== null) listeningPlaysLeft = Number(left);
    const url = URL.createObjectURL(await res.blob());
    const audio = new Audio(url);
    listeningAudio = audio;
    audio.ontimeupdate = () => {
      if (audio.duration && isFinite(audio.duration)) bar.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
    };
    audio.onended = () => {
      bar.style.width = "100%";
      stopListeningAudio();
    };
    btn.textContent = "Playing…";
    btn.disabled = true;
    pauseBtn.textContent = "⏸ Pause";
    pauseBtn.classList.remove("d-none");
    updateListeningPlayInfo();
    await audio.play();
  } catch (e) {
    stopListeningAudio();
    document.getElementById("listeningPlayInfo").innerHTML =
      `<span class="text-danger">${esc(e.message)}</span>` +
      (e.audioFailed && !listeningChecked && !currentListening.backup
        ? ` <button class="btn btn-sm btn-outline-primary ms-2" onclick="generateListening(true)">Use a backup test</button>`
        : "");
  }
}

function toggleListeningPause() {
  const pauseBtn = document.getElementById("listeningPauseBtn");
  if (!listeningAudio) return;
  if (listeningAudio.paused) { listeningAudio.play(); pauseBtn.textContent = "⏸ Pause"; }
  else { listeningAudio.pause(); pauseBtn.textContent = "▶ Resume"; }
}

function collectListeningAnswers() {
  const answers = {};
  for (const q of (currentListening?.questions || [])) {
    const name = `listen_q_${q.id}`;
    const radio = document.querySelector(`input[name="${name}"]:checked`);
    const text = document.querySelector(`input[name="${name}"]:not([type="radio"])`);
    answers[String(q.id)] = radio ? radio.value : (text ? text.value.trim() : "");
  }
  return answers;
}

async function submitListening() {
  if (!currentListening || listeningChecked) return;
  const answers = collectListeningAnswers();
  const empty = Object.values(answers).filter(v => !String(v).trim()).length;
  if (empty && !confirm(`${empty} question(s) are not answered. Check anyway?`)) return;

  const btn = document.getElementById("listeningSubmitBtn");
  btn.disabled = true;
  btn.textContent = "Checking…";
  try {
    const d = await api("/api/listening/submit", {
      method: "POST",
      body: JSON.stringify({test_id: currentListening.test_id, answers})
    });
    listeningChecked = true;
    roadmapReport("listening", d.score);

    for (const item of (d.details || [])) {
      const el = document.getElementById(`listen_feedback_${item.id}`);
      if (!el) continue;
      el.innerHTML = item.correct
        ? `<div class="small text-success"><strong>Correct.</strong> ${esc(item.explanation)}</div>`
        : `<div class="small text-danger"><strong>Review:</strong> your answer: ${esc(item.answer || "—")}<br>
             Correct answer: <strong>${esc(item.correct_answer)}</strong><br>
             <span class="text-secondary">${esc(item.explanation)}</span></div>`;
    }
    document.querySelectorAll("#listeningForm input").forEach(el => el.disabled = true);
    btn.textContent = "Completed";

    document.getElementById("listeningResult").innerHTML = `
      <div class="alert ${d.score >= 70 ? "alert-success" : "alert-primary"}">
        <h4 class="mb-1">Listening score: ${esc(d.score)}%</h4>
        <div>${esc(d.correct)} / ${esc(d.total)} correct · level ${esc(d.level)}</div>
      </div>
      <p class="small text-secondary">This practice has been added to your Listening progress.
        Highlighted parts of the transcript show where each answer is heard; listen again with the text.</p>
      <div class="card-soft">
        <h5>Transcript</h5>
        ${renderListeningTranscript(d.script || [], d.details || [])}
      </div>`;
    updateListeningPlayInfo();
    await loadProfile();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Check answers";
    document.getElementById("listeningResult").innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`;
  }
}

function renderListeningTranscript(script, details) {
  const evidences = details
    .filter(d => d.evidence)
    .map(d => {
      const words = String(d.evidence).match(/[\w']+/g) || [];
      if (!words.length) return null;
      const pattern = words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[^\\w']+");
      return {id: d.id, correct: d.correct, re: new RegExp(pattern, "i"), used: false};
    })
    .filter(Boolean);

  return script.map(line => {
    const text = String(line.text || "");
    const ranges = [];
    for (const ev of evidences) {
      if (ev.used) continue;
      const m = ev.re.exec(text);
      if (!m) continue;
      const start = m.index, end = m.index + m[0].length;
      if (ranges.some(r => start < r.end && end > r.start)) continue;
      ranges.push({start, end, id: ev.id, correct: ev.correct});
      ev.used = true;
    }
    ranges.sort((a, b) => a.start - b.start);
    let html = "", pos = 0;
    for (const r of ranges) {
      html += esc(text.slice(pos, r.start));
      html += `<mark class="listen-evidence ${r.correct ? "ok" : "bad"}" title="Question ${r.id}"><sup>${r.id}</sup>${esc(text.slice(r.start, r.end))}</mark>`;
      pos = r.end;
    }
    html += esc(text.slice(pos));
    return `<p class="mb-2"><strong>${esc(line.speaker)}:</strong> ${html}</p>`;
  }).join("");
}


/* ===================== IELTS Academic Writing Task 1 visuals (SVG) ===================== */

const T1_COLORS = ["#3b5bdb", "#e8590c", "#2b8a3e", "#ae3ec9", "#1098ad", "#f08c00"];

function t1Nice(max) {
  if (max <= 0) return {top: 1, step: 0.2};
  const raw = max / 5, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
  return {top: Math.ceil(max / step) * step, step};
}

function t1Num(v) { return Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100); }

function t1Legend(series, x, y) {
  return series.map((s, i) => `<rect x="${x + i * 140}" y="${y - 9}" width="12" height="12" fill="${T1_COLORS[i % 6]}"/>
    <text x="${x + i * 140 + 17}" y="${y + 1}" font-size="12">${esc(s.name)}</text>`).join("");
}

function t1Axes(c, W, H, L, B, T) {
  const max = Math.max(0, ...c.series.flatMap(s => s.values));
  const {top, step} = t1Nice(max);
  let g = "";
  for (let v = 0; v <= top + 1e-9; v += step) {
    const y = H - B - (v / top) * (H - B - T);
    g += `<line x1="${L}" x2="${W - 15}" y1="${y}" y2="${y}" stroke="#e5e5e5"/>
          <text x="${L - 6}" y="${y + 4}" font-size="11" text-anchor="end">${t1Num(Math.round(v * 100) / 100)}</text>`;
  }
  g += `<line x1="${L}" x2="${L}" y1="${T}" y2="${H - B}" stroke="#333"/><line x1="${L}" x2="${W - 15}" y1="${H - B}" y2="${H - B}" stroke="#333"/>`;
  if (c.y_label) g += `<text transform="translate(14 ${(H - B + T) / 2}) rotate(-90)" font-size="12" text-anchor="middle">${esc(c.y_label)}</text>`;
  if (c.x_label) g += `<text x="${(L + W) / 2}" y="${H - 6}" font-size="12" text-anchor="middle">${esc(c.x_label)}</text>`;
  return {g, top};
}

function t1Bar(c) {
  const W = 680, H = 400, L = 62, B = 58, T = 40;
  const {g, top} = t1Axes(c, W, H, L, B, T);
  const n = c.categories.length, k = c.series.length, slot = (W - 15 - L) / n, bw = Math.min(34, (slot * 0.75) / k);
  let bars = "";
  c.categories.forEach((cat, i) => {
    const x0 = L + i * slot + (slot - bw * k) / 2;
    c.series.forEach((s, j) => {
      const h = (s.values[i] / top) * (H - B - T);
      bars += `<rect x="${x0 + j * bw}" y="${H - B - h}" width="${bw - 2}" height="${h}" fill="${T1_COLORS[j % 6]}"><title>${esc(s.name)}: ${t1Num(s.values[i])}</title></rect>`;
    });
    bars += `<text x="${L + i * slot + slot / 2}" y="${H - B + 16}" font-size="11" text-anchor="middle">${esc(cat)}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="t1-svg">${g}${bars}${t1Legend(c.series, L, 22)}</svg>`;
}

function t1Line(c) {
  const W = 680, H = 400, L = 62, B = 58, T = 40;
  const {g, top} = t1Axes(c, W, H, L, B, T);
  const n = c.categories.length, xs = i => L + 20 + i * ((W - 50 - L) / Math.max(1, n - 1));
  let lines = c.categories.map((cat, i) => `<text x="${xs(i)}" y="${H - B + 16}" font-size="11" text-anchor="middle">${esc(cat)}</text>`).join("");
  c.series.forEach((s, j) => {
    const pts = s.values.map((v, i) => `${xs(i)},${H - B - (v / top) * (H - B - T)}`);
    lines += `<polyline points="${pts.join(" ")}" fill="none" stroke="${T1_COLORS[j % 6]}" stroke-width="2.5"/>`;
    lines += pts.map((p, i) => { const [x, y] = p.split(","); return `<circle cx="${x}" cy="${y}" r="3.5" fill="${T1_COLORS[j % 6]}"><title>${esc(s.name)} ${esc(c.categories[i])}: ${t1Num(s.values[i])}</title></circle>`; }).join("");
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="t1-svg">${g}${lines}${t1Legend(c.series, L, 22)}</svg>`;
}

function t1Pie(c) {
  const k = c.series.length, W = 230 * k + 20, H = 300, r = 88;
  let out = "";
  c.series.forEach((s, j) => {
    const cx = 125 + j * 230, cy = 150, total = s.values.reduce((a, b) => a + b, 0) || 1;
    let a0 = -Math.PI / 2;
    s.values.forEach((v, i) => {
      const a1 = a0 + (v / total) * 2 * Math.PI, large = a1 - a0 > Math.PI ? 1 : 0;
      const p = (a) => `${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`;
      out += `<path d="M${cx},${cy} L${p(a0)} A${r},${r} 0 ${large} 1 ${p(a1)} Z" fill="${T1_COLORS[i % 6]}" stroke="#fff"><title>${esc(c.categories[i])}: ${t1Num(v)}%</title></path>`;
      const mid = (a0 + a1) / 2;
      if (v / total > 0.04) out += `<text x="${cx + r * 0.65 * Math.cos(mid)}" y="${cy + r * 0.65 * Math.sin(mid) + 4}" font-size="12" fill="#fff" font-weight="700" text-anchor="middle">${t1Num(v)}%</text>`;
      a0 = a1;
    });
    out += `<text x="${cx}" y="${cy + r + 24}" font-size="13" font-weight="700" text-anchor="middle">${esc(s.name)}</text>`;
  });
  const legend = c.categories.map((cat, i) => `<span class="me-3 small"><span style="display:inline-block;width:11px;height:11px;background:${T1_COLORS[i % 6]}"></span> ${esc(cat)}</span>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="t1-svg" style="max-width:${W}px">${out}</svg><div>${legend}</div>`;
}

function t1Table(c) {
  return `<div class="table-responsive"><table class="table table-bordered table-sm text-center mb-1">
    <thead class="table-light"><tr>${c.columns.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${c.rows.map(r => `<tr>${r.map((x, i) => i ? `<td>${esc(x)}</td>` : `<th class="text-start">${esc(x)}</th>`).join("")}</tr>`).join("")}</tbody>
  </table></div>${c.unit ? `<div class="small text-secondary">${esc(c.unit)}</div>` : ""}`;
}

function t1Process(c) {
  const perRow = 4, bw = 140, bh = 66, gx = 34, gy = 40, n = c.steps.length;
  const rows = Math.ceil(n / perRow), W = perRow * bw + (perRow - 1) * gx + 20, H = rows * bh + (rows - 1) * gy + (c.cycle ? 50 : 20);
  const pos = i => {
    const r = Math.floor(i / perRow), k = i % perRow, col = r % 2 ? perRow - 1 - k : k;  // snake layout
    return {x: 10 + col * (bw + gx), y: 10 + r * (bh + gy)};
  };
  const wrap = t => { const words = t.split(" "), lines = [""]; words.forEach(w => { if ((lines.at(-1) + " " + w).trim().length > 19) lines.push(w); else lines[lines.length - 1] = (lines.at(-1) + " " + w).trim(); }); return lines.slice(0, 3); };
  let out = `<defs><marker id="t1arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#555"/></marker></defs>`;
  c.steps.forEach((s, i) => {
    const {x, y} = pos(i);
    out += `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="8" fill="#eef1ff" stroke="#5b5bd6"/>
      <circle cx="${x + 12}" cy="${y + 12}" r="9" fill="#5b5bd6"/><text x="${x + 12}" y="${y + 16}" font-size="11" fill="#fff" text-anchor="middle">${i + 1}</text>
      ${wrap(s).map((l, j) => `<text x="${x + bw / 2 + 6}" y="${y + 26 + j * 15}" font-size="12" text-anchor="middle">${esc(l)}</text>`).join("")}`;
    if (i < n - 1) {
      const a = pos(i), b = pos(i + 1);
      const [x1, y1, x2, y2] = a.y === b.y
        ? (b.x > a.x ? [a.x + bw, a.y + bh / 2, b.x - 2, b.y + bh / 2] : [a.x, a.y + bh / 2, b.x + bw + 2, b.y + bh / 2])
        : [a.x + bw / 2, a.y + bh, b.x + bw / 2, b.y - 2];
      out += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#555" stroke-width="2" marker-end="url(#t1arrow)"/>`;
    }
  });
  if (c.cycle) out += `<text x="${W / 2}" y="${H - 12}" font-size="12" text-anchor="middle" fill="#555">↻ After step ${n} the process returns to step 1</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" class="t1-svg">${out}</svg>`;
}

function t1Map(c) {
  const cell = 30, size = cell * 10, W = size * 2 + 70, H = size + 50;
  const fills = {};
  let k = 0;
  c.panels.forEach(p => p.features.forEach(f => { if (!(f.label in fills)) fills[f.label] = ["#d0ebff", "#d3f9d8", "#fff3bf", "#ffe3e3", "#e5dbff", "#c5f6fa", "#ffe8cc", "#f1f3f5"][k++ % 8]; }));
  let out = "";
  c.panels.forEach((p, j) => {
    const ox = 10 + j * (size + 50), oy = 30;
    out += `<text x="${ox + size / 2}" y="20" font-size="14" font-weight="700" text-anchor="middle">${esc(p.title)}</text>
      <rect x="${ox}" y="${oy}" width="${size}" height="${size}" fill="#fafafa" stroke="#333"/>`;
    p.features.forEach(f => {
      const x = ox + f.x * cell, y = oy + f.y * cell, w = f.w * cell, h = f.h * cell;
      out += `<rect x="${x + 1}" y="${y + 1}" width="${w - 2}" height="${h - 2}" fill="${fills[f.label]}" stroke="#666"/>
        <text x="${x + w / 2}" y="${y + h / 2 + 4}" font-size="${w < 60 ? 9 : 11}" text-anchor="middle">${esc(f.label)}</text>`;
    });
    out += `<text x="${ox + size - 12}" y="${oy + 18}" font-size="12" font-weight="700">N</text><line x1="${ox + size - 8}" y1="${oy + 34}" x2="${ox + size - 8}" y2="${oy + 22}" stroke="#333" stroke-width="2"/>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="t1-svg">${out}</svg>`;
}

function renderTask1Chart(containerId, chart) {
  const el = document.getElementById(containerId);
  if (!el || !chart) return;
  const draw = {bar: t1Bar, line: t1Line, pie: t1Pie, table: t1Table, process: t1Process, map: t1Map}[chart.type];
  el.innerHTML = `<div class="t1-visual">${chart.title ? `<div class="fw-semibold text-center mb-2">${esc(chart.title)}</div>` : ""}
    ${draw ? draw(chart) : ""}</div>`;
}


/* ===================== IELTS Speaking examiner (shared by practice and full mock) ===================== */
// The examiner's questions are heard, not shown (as in the exam). The microphone opens when the
// examiner finishes; the answer stops at the time limit or when the candidate presses "Finished".

let examinerAudio = null;
let examinerStream = null;
let examinerStop = null;      // stops the current recording early

async function examinerPrepare() {
  // Call from a click: unlocks audio playback and asks for the microphone once.
  examinerAudio = examinerAudio || new Audio();
  examinerAudio.muted = true;
  examinerAudio.play().catch(() => {});
  examinerAudio.pause();
  examinerAudio.muted = false;
  if (!navigator.mediaDevices || !window.MediaRecorder) throw new Error("Audio recording is not supported in this browser.");
  if (!examinerStream || examinerStream.getTracks().every(t => t.readyState === "ended")) {
    examinerStream = await navigator.mediaDevices.getUserMedia({audio: true});
  }
}

function examinerRelease() {
  examinerStream?.getTracks().forEach(t => t.stop());
  examinerStream = null;
}

function examinerPlay(b64) {
  return new Promise(resolve => {
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], {type: "audio/mpeg"}));
    const a = examinerAudio || new Audio();
    a.onended = () => { URL.revokeObjectURL(url); resolve(); };
    a.onerror = () => { URL.revokeObjectURL(url); resolve(); };
    a.src = url;
    a.play().catch(() => resolve());
  });
}

function examinerUI(container) {
  container.innerHTML = `
    <div class="examiner-box">
      <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
        <div><span class="badge text-bg-primary" id="exPart">Part 1</span>
             <strong class="ms-2" id="exStatus">Starting…</strong></div>
        <div class="fw-semibold" id="exClock"></div>
      </div>
      <div class="progress mt-2" style="height:6px"><div id="exBar" class="progress-bar" style="width:0%"></div></div>
      <div id="exCue" class="mt-3"></div>
      <div class="d-flex gap-2 mt-3">
        <button id="exFinish" class="btn btn-primary d-none">I've finished my answer</button>
        <button id="exRepeat" class="btn btn-outline-secondary d-none">↻ Could you repeat the question?</button>
      </div>
      <div id="exError" class="mt-2"></div>
    </div>`;
  return {
    status: (t) => { container.querySelector("#exStatus").textContent = t; },
    part: (p) => { container.querySelector("#exPart").textContent = `Part ${p}`; },
    clock: (t) => { container.querySelector("#exClock").textContent = t; },
    bar: (pct) => { container.querySelector("#exBar").style.width = `${pct}%`; },
    cue: (html) => { container.querySelector("#exCue").innerHTML = html; },
    btn: (id) => container.querySelector(id),
    error: (html) => { container.querySelector("#exError").innerHTML = html; },
  };
}

function examinerCountdown(ui, seconds, label) {
  return new Promise(resolve => {
    const end = Date.now() + seconds * 1000;
    ui.status(label);
    const tick = () => {
      const left = (end - Date.now()) / 1000;
      ui.clock(mockFmt(left));
      ui.bar(100 - Math.max(0, left) / seconds * 100);
      if (left <= 0) return resolve();
      setTimeout(tick, 200);
    };
    tick();
  });
}

// Records one answer; resolves with {blob, seconds, repeat}.
function examinerRecord(ui, maxSeconds, allowRepeat) {
  return new Promise(resolve => {
    const preferred = "audio/webm;codecs=opus";
    const rec = new MediaRecorder(examinerStream, MediaRecorder.isTypeSupported(preferred) ? {mimeType: preferred} : {});
    const chunks = [];
    const started = Date.now();
    let repeat = false, timer = null;
    rec.ondataavailable = e => { if (e.data?.size) chunks.push(e.data); };
    rec.onstop = () => {
      clearTimeout(timer);
      ui.btn("#exFinish").classList.add("d-none");
      ui.btn("#exRepeat").classList.add("d-none");
      resolve({blob: new Blob(chunks, {type: rec.mimeType || "audio/webm"}), seconds: (Date.now() - started) / 1000, repeat});
    };
    const fin = ui.btn("#exFinish"), rep = ui.btn("#exRepeat");
    fin.classList.remove("d-none");
    fin.onclick = () => rec.state === "recording" && rec.stop();
    rep.classList.toggle("d-none", !allowRepeat);
    rep.onclick = () => { repeat = true; if (rec.state === "recording") rec.stop(); };
    examinerStop = () => rec.state === "recording" && rec.stop();
    rec.start(1000);
    ui.status("Your answer — speak now 🎙");
    const tick = () => {
      if (rec.state !== "recording") return;
      const used = (Date.now() - started) / 1000;
      ui.clock(mockFmt(maxSeconds - used));
      ui.bar(Math.min(100, used / maxSeconds * 100));
      if (used >= maxSeconds) return rec.stop();
      timer = setTimeout(tick, 200);
    };
    tick();
  });
}

async function examinerUpload(url, turnId, rec) {
  const form = new FormData();
  form.append("turn_id", turnId);
  form.append("seconds", String(Math.round(rec.seconds)));
  form.append("audio", rec.blob, `${turnId}.webm`);
  return apiForm(url, form);
}

// Runs the whole examiner conversation. reply = {turn, done}; answerUrl = endpoint for answers.
async function runExaminer(container, firstReply, answerUrl) {
  const ui = examinerUI(container);
  let reply = firstReply;
  while (true) {
    const t = reply.turn;
    ui.part(t.part);
    ui.error("");
    ui.cue("");
    ui.clock("");
    ui.bar(0);
    ui.status("Examiner is speaking…");
    await examinerPlay(t.audio);
    if (t.kind === "end") { ui.status("The Speaking test is over."); return; }

    if (t.kind === "cue_card") {
      ui.cue(`<div class="cue-card"><strong>${esc(t.cue_card || "")}</strong>
          <div class="small mt-2">You should say:</div>
          <ul class="mb-2">${(t.bullet_points || []).map(b => `<li>${esc(b)}</li>`).join("")}</ul></div>
        <textarea class="form-control mt-2" rows="4" placeholder="Notes (only for you — they are not sent)"></textarea>`);
      await examinerCountdown(ui, t.prep_seconds || 60, "Preparation time — make notes");
      ui.status("Examiner is speaking…");
      ui.clock("");
      await examinerPlay(t.go_audio);
    }

    let rec;
    while (true) {
      rec = await examinerRecord(ui, t.answer_seconds || 45, t.kind === "question");
      if (!rec.repeat) break;
      ui.status("Examiner is repeating the question…");
      await examinerPlay(t.audio);
    }
    ui.status("Please wait…");
    ui.clock("");
    for (let attempt = 0; ; attempt++) {
      try {
        reply = await examinerUpload(answerUrl, t.id, rec);
        break;
      } catch (e) {
        if (attempt >= 1) {
          ui.error(`<div class="alert alert-danger small mb-0">${esc(e.message)}
            <button class="btn btn-sm btn-outline-danger ms-2" id="exRetry">Try again</button></div>`);
          await new Promise(r => { container.querySelector("#exRetry").onclick = r; });
          ui.error("");
        }
      }
    }
  }
}


/* ===================== Admin: IELTS bank (view / verify / delete) ===================== */

const BANK_LABELS = {
  "mock:Academic": "Full mock · Academic", "mock:General Training": "Full mock · General Training",
  "reading:Academic": "Reading · Academic", "reading:General Training": "Reading · General Training",
  "listening": "Listening", "writing:Academic:1": "Writing Task 1 · Academic",
  "writing:General Training:1": "Writing Task 1 · General Training", "writing:2": "Writing Task 2",
  "speaking:1": "Speaking Part 1", "speaking:2": "Speaking Part 2", "speaking:3": "Speaking Part 3",
};

async function loadIELTSBankAdmin() {
  const box = document.getElementById("ieltsBankAdmin");
  if (!box) return;
  if (!currentProfile?.is_admin) { box.innerHTML = ""; return; }
  let d;
  try { d = await api("/api/admin/ielts-bank"); } catch (e) { d = {error: e.message, items: []}; }
  if (d.error) {
    box.innerHTML = `<div class="card-soft"><h5>IELTS bank <span class="badge text-bg-secondary">admin</span></h5>
      <div class="alert alert-warning small mb-0">${esc(d.error)}</div></div>`;
    return;
  }
  const groups = {};
  (d.items || []).forEach(it => (groups[it.bank_key] = groups[it.bank_key] || []).push(it));
  const keys = Object.keys(BANK_LABELS);
  box.innerHTML = `<div class="card-soft mb-3"><h5>Storage <span class="badge text-bg-secondary">admin</span></h5><div id="storageCleanupBox"></div></div>
  <div class="card-soft">
    <h5>IELTS bank <span class="badge text-bg-secondary">admin</span></h5>
    <p class="small text-secondary">Filled automatically from successful generations (up to ${d.mock_limit} full mocks per test type and ${d.practice_limit} practice sets per section).
      Used when OpenAI is unavailable; <strong>verified</strong> full mocks can also be chosen by learners. ⚠ marks questions that almost nobody answers “correctly” — check the key.</p>
    ${keys.map(k => {
      const items = groups[k] || [];
      const limit = k.startsWith("mock:") ? d.mock_limit : d.practice_limit;
      return `<details class="mb-2" ${k.startsWith("mock:") ? "open" : ""}><summary><strong>${esc(BANK_LABELS[k])}</strong>
          <span class="badge ${items.length >= limit ? "text-bg-success" : "text-bg-light"} ms-1">${items.length}/${limit}</span></summary>
        ${items.length ? `<table class="table table-sm align-middle mt-2 mb-1"><tbody>${items.map(it => `
          <tr><td>${esc(it.title || "")}<div class="small text-secondary">${esc(String(it.created_at || "").slice(0, 10))} · used ${esc(it.times_used || 0)}×
              ${it.suspicious?.length ? ` · <span class="text-danger">⚠ ${it.suspicious.length} suspicious</span>` : ""}</div></td>
            <td class="text-end text-nowrap">
              <div class="form-check form-switch d-inline-block me-2"><input class="form-check-input" type="checkbox" ${it.verified ? "checked" : ""}
                onchange="verifyBankItem('${it.id}', this.checked)" title="Verified"><label class="small">verified</label></div>
              <button class="btn btn-sm btn-outline-primary" onclick="viewBankItem('${it.id}')">View</button>
              <button class="btn btn-sm btn-outline-danger" onclick="deleteBankItem('${it.id}')">Delete</button>
            </td></tr>
          <tr class="d-none" id="bankView_${it.id}"><td colspan="2"></td></tr>`).join("")}</tbody></table>`
          : `<div class="small text-secondary mt-1 mb-2">Empty — it fills itself as learners use this section.</div>`}
      </details>`;
    }).join("")}
  </div>`;
  loadStorageCleanup();
}

async function loadStorageCleanup() {
  const el = document.getElementById("storageCleanupBox");
  if (!el) return;
  try {
    const s = await api("/api/admin/storage-cleanup");
    const r = s.last_result;
    el.innerHTML = `<div class="small">Audio of exams and Speaking practice older than ${esc(s.retention_days)} days is deleted automatically
      (checked every ${esc(s.check_hours)} h; bank items and recordings still waiting for assessment are kept; texts and scores stay).
      ${r ? `Last run ${esc(String(r.ran_at).slice(0, 16).replace("T", " "))}: ${esc(r.files)} files from ${esc(r.mocks)} mocks and ${esc(r.practice_sessions)} practice sessions${r.skipped_pending ? `, ${esc(r.skipped_pending)} waiting for assessment kept` : ""}.` : "No run since the server started."}
      <button class="btn btn-sm btn-outline-secondary ms-2" onclick="runStorageCleanup(this)">Run now</button></div>`;
  } catch (e) { el.textContent = e.message; }
}

async function runStorageCleanup(btn) {
  btn.disabled = true; btn.textContent = "Cleaning…";
  try { await api("/api/admin/storage-cleanup", {method: "POST", body: "{}"}); } catch (e) { alert(e.message); }
  loadStorageCleanup();
}

async function verifyBankItem(id, verified) {
  try { await api(`/api/admin/ielts-bank/${id}/verify`, {method: "POST", body: JSON.stringify({verified})}); }
  catch (e) { alert(e.message); }
}

async function deleteBankItem(id) {
  if (!confirm("Delete this item from the bank? Its recordings are deleted too. This cannot be undone.")) return;
  try { await api(`/api/admin/ielts-bank/${id}`, {method: "DELETE"}); await loadIELTSBankAdmin(); }
  catch (e) { alert(e.message); }
}

async function loadBankAudio(id, name, elId) {
  const el = document.getElementById(elId);
  el.innerHTML = `<span class="small text-secondary">Loading…</span>`;
  try {
    const res = await fetch(`/api/admin/ielts-bank/${id}/audio/${name}`, {headers: {"Authorization": `Bearer ${currentSession.access_token}`}});
    if (!res.ok) throw new Error("Recording not found.");
    el.innerHTML = `<audio controls src="${URL.createObjectURL(await res.blob())}" style="width:100%"></audio>`;
  } catch (e) { el.textContent = e.message; }
}

function bankQuestionsHTML(c, suspiciousPrefix, suspicious) {
  const key = c.answer_key || {};
  const v = c.verification;
  const note = v ? `<div class="small text-secondary">Key check: ${v.status === "ok" ? `${esc(v.checked)} questions checked by a second model` : esc(v.status)}${(v.fixed || []).length ? ` · fixed: ${v.fixed.map(esc).join(", ")}` : ""}${(v.unverified || []).length ? ` · <span class="text-danger">still disputed: ${v.unverified.map(esc).join(", ")}</span>` : ""}</div>` : "";
  return note + `<table class="table table-sm small"><thead><tr><th>#</th><th>Question</th><th>Answer key</th><th>Explanation</th></tr></thead><tbody>
    ${(c.questions || []).map(q => {
      const ans = key[String(q.id)];
      const bad = suspicious.includes(`${suspiciousPrefix}:${q.id}`)
        || (c.verification?.unverified || []).includes(String(q.id));
      return `<tr class="${bad ? "table-danger" : ""}"><td>${esc(q.id)}${bad ? " ⚠" : ""}</td>
        <td>${esc(q.question)}${(q.options || []).length ? `<div class="text-secondary">${q.options.map(esc).join(" · ")}</div>` : ""}${q.instruction ? `<div class="text-secondary fst-italic">${esc(q.instruction)}</div>` : ""}</td>
        <td><strong>${esc(Array.isArray(ans) ? ans.join(" / ") : (ans ?? ""))}</strong></td>
        <td class="text-secondary">${esc((c.explanations || {})[String(q.id)] || "")}</td></tr>`;
    }).join("")}</tbody></table>`;
}

function bankTextHTML(title, text) {
  return text ? `<details class="mb-2"><summary>${esc(title)}</summary><div class="small mt-1" style="white-space:pre-line">${esc(text)}</div></details>` : "";
}

async function viewBankItem(id) {
  const row = document.getElementById(`bankView_${id}`);
  if (!row.classList.contains("d-none")) { row.classList.add("d-none"); return; }
  row.classList.remove("d-none");
  const cell = row.querySelector("td");
  cell.innerHTML = `<div class="loader">Loading…</div>`;
  let it;
  try { it = await api(`/api/admin/ielts-bank/${id}`); } catch (e) { cell.textContent = e.message; return; }
  const c = it.content || {}, sus = it.suspicious || [];
  let html = "";
  if (it.kind === "mock") {
    html += [1, 2, 3, 4].map(p => {
      const part = c.listening?.[String(p)] || {};
      return `<h6 class="mt-3">Listening Part ${p}: ${esc(part.title || "")}</h6>
        <div id="bankAudio_${id}_${p}"><button class="btn btn-sm btn-outline-secondary" onclick="loadBankAudio('${id}','${p}','bankAudio_${id}_${p}')">▶ Load recording</button></div>
        ${bankTextHTML("Transcript", part.audio_script)}${bankQuestionsHTML(part, `listening${p}`, sus)}`;
    }).join("");
    html += [1, 2, 3].map(p => {
      const part = c.reading?.[String(p)] || {};
      return `<h6 class="mt-3">Reading ${p}: ${esc(part.title || "")}</h6>${bankTextHTML("Passage", part.passage)}${bankQuestionsHTML(part, `reading${p}`, sus)}`;
    }).join("");
    html += [1, 2].map(p => {
      const t = c.writing?.[String(p)] || {};
      return `<h6 class="mt-3">Writing Task ${p}</h6><div class="small" style="white-space:pre-line">${esc(t.prompt || "")}</div><div id="bankChart_${id}_${p}"></div>`;
    }).join("");
    html += `<h6 class="mt-3">Speaking</h6><div class="small">
      <strong>Part 1:</strong> ${(c.speaking?.["1"]?.questions || []).map(esc).join(" · ")}<br>
      <strong>Part 2:</strong> ${esc(c.speaking?.["2"]?.cue_card || "")} (${(c.speaking?.["2"]?.bullet_points || []).map(esc).join("; ")})<br>
      <strong>Part 3:</strong> ${(c.speaking?.["3"]?.questions || []).map(esc).join(" · ")}</div>`;
  } else if (it.bank_key.startsWith("reading")) {
    html = bankTextHTML("Passage", c.passage) + bankQuestionsHTML(c, "1", sus);
  } else if (it.bank_key === "listening") {
    html = `<div id="bankAudio_${id}_1"><button class="btn btn-sm btn-outline-secondary" onclick="loadBankAudio('${id}','1','bankAudio_${id}_1')">▶ Load recording</button></div>`
      + bankTextHTML("Transcript", c.audio_script) + bankQuestionsHTML(c, "1", sus);
  } else if (it.bank_key.startsWith("writing")) {
    html = `<div class="small" style="white-space:pre-line">${esc(c.prompt || "")}</div><div id="bankChart_${id}_1"></div>`;
  } else {
    const m = c.material || {};
    html = `<div class="small">${esc(m.cue_card || "")} ${(m.bullet_points || []).length ? `(${m.bullet_points.map(esc).join("; ")})` : ""}
      ${(m.questions || []).length ? `<ol>${m.questions.map(q => `<li>${esc(q)}</li>`).join("")}</ol>` : ""}</div>`;
  }
  cell.innerHTML = `<div class="p-2 border rounded bg-light">${html}</div>`;
  if (it.kind === "mock" && c.writing?.["1"]?.chart) renderTask1Chart(`bankChart_${id}_1`, c.writing["1"].chart);
  if (it.bank_key.startsWith("writing") && c.chart) renderTask1Chart(`bankChart_${id}_1`, c.chart);
}


/* ===================== IELTS Reading: grouped question types ===================== */
// Matching Headings, Summary completion with a word bank and Diagram labelling share material
// (headings list, summary with gaps, diagram); it is shown once above the group's questions.

function readingAdjustNumbers(text, offset) {
  if (!offset) return text;
  return String(text || "").replace(/(Questions?\s+)(\d+)(\s*(?:-|–|to|and)\s*)(\d+)/gi,
    (m, a, x, sep, y) => `${a}${offset + Number(x)}${sep}${offset + Number(y)}`);
}

function readingSelectHTML(name, q, saved, number, fbId) {
  const value = saved ?? "";
  const opts = (q.options || []).map(o => `<option value="${esc(o)}" ${value === o ? "selected" : ""}>${esc(String(o).split(/[.)]\s*/)[0])}</option>`).join("");
  return `<div class="mock-question d-flex align-items-center gap-2 flex-wrap">
    <span class="mock-qnum">${number}</span><span>${esc(q.question)}</span>
    <select class="form-select form-select-sm reading-select" name="${name}"><option value="">—</option>${opts}</select>
    ${fbId ? `<div id="${fbId}" class="w-100"></div>` : ""}</div>`;
}

function readingDiagramSVG(diagram, numberOf) {
  const cw = 64, ch = 46, W = 10 * cw + 10, H = 8 * ch + 10;
  const parts = diagram.parts || [];
  const center = p => [5 + (p.x + p.w / 2) * cw, 5 + (p.y + p.h / 2) * ch];
  let out = (diagram.links || []).map(([a, b]) => {
    if (!parts[a] || !parts[b]) return "";
    const [x1, y1] = center(parts[a]), [x2, y2] = center(parts[b]);
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#888" stroke-width="1.5"/>`;
  }).join("");
  parts.forEach(p => {
    const x = 5 + p.x * cw, y = 5 + p.y * ch, w = p.w * cw - 6, h = p.h * ch - 6;
    const label = p.gap != null ? `(${numberOf(p.gap)}) ________` : p.text;
    out += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="6" fill="${p.gap != null ? "#fff8e1" : "#eef1ff"}" stroke="#5b5bd6"/>
      <text x="${x + w / 2}" y="${y + h / 2 + 4}" font-size="12" text-anchor="middle" font-weight="${p.gap != null ? 700 : 400}">${esc(label)}</text>`;
  });
  return `<div class="t1-visual mb-2">${diagram.title ? `<div class="fw-semibold text-center mb-1">${esc(diagram.title)}</div>` : ""}
    <svg viewBox="0 0 ${W} ${H}" class="t1-svg">${out}</svg></div>`;
}

// ctx: {name(q), number(qid), saved(qid), standard(q) -> html, fbId(q) -> id or ""}
function readingGroupsHTML(content, ctx, offset = 0) {
  const questions = content.questions || [];
  const byId = Object.fromEntries(questions.map(q => [String(q.id), q]));
  const used = new Set();
  let html = "";
  for (const g of (content.groups || [])) {
    const ids = (g.question_ids || []).map(String).filter(id => byId[id]);
    if (!ids.length) continue;
    ids.forEach(id => used.add(id));
    let shared = "";
    if (g.type === "matching_headings") {
      shared = `<div class="reading-box"><div class="fw-semibold mb-1">List of Headings</div>${(g.options || []).map(o => `<div>${esc(o)}</div>`).join("")}</div>`;
    } else if (g.type === "summary_completion") {
      const summary = esc(g.summary || "").replace(/\[Q(\d+)\]/g, (m, id) => `<strong>(${ctx.number(id)})</strong>&nbsp;________`);
      shared = `<div class="reading-box mb-2">${summary}</div>
        <div class="reading-box"><div class="fw-semibold mb-1">Word bank</div>${(g.options || []).map(o => `<span class="me-3 d-inline-block">${esc(o)}</span>`).join("")}</div>`;
    } else if (g.type === "diagram_labelling" && g.diagram) {
      shared = readingDiagramSVG(g.diagram, ctx.number);
    }
    const qs = ids.map(id => {
      const q = byId[id];
      if (g.type === "matching_headings" || g.type === "summary_completion") {
        return readingSelectHTML(ctx.name(q), q, ctx.saved(id), ctx.number(id), ctx.fbId(q));
      }
      return ctx.standard(q);
    }).join("");
    html += `<div class="reading-group mb-3">
      ${g.instruction ? `<div class="small fw-semibold text-secondary mb-2">${esc(readingAdjustNumbers(g.instruction, offset))}</div>` : ""}
      ${shared}${qs}</div>`;
  }
  html += questions.filter(q => !used.has(String(q.id))).map(q => ctx.standard(q)).join("");
  return html;
}

// ========================= IELTS analytics: progress chart, forecast, weak question types =========================
const ieltsFocus = {reading: null, listening: null};
const IELTS_SKILL_COLORS = {listening:"#2563eb", reading:"#16a34a", writing:"#d97706", speaking:"#9333ea", overall:"#111827"};

function ieltsProgressSVG(history) {
  const skills = ["listening", "reading", "writing", "speaking", "overall"];
  const rows = (history || []).filter(r => r.band != null);
  if (rows.length < 2) return `<div class="text-secondary small">The chart appears after two or more results (a full mock adds all four skills at once).</div>`;
  const times = rows.map(r => new Date(r.created_at).getTime());
  const t0 = Math.min(...times), t1 = Math.max(...times), span = Math.max(t1 - t0, 1);
  const W = 640, H = 240, L = 34, R = 12, T = 10, B = 26;
  const x = t => L + (W - L - R) * (rows.length > 1 ? (t - t0) / span : 0.5);
  const y = b => T + (H - T - B) * (1 - (Number(b) - 3) / 6);
  let grid = "";
  for (let b = 3; b <= 9; b++) grid += `<line x1="${L}" x2="${W-R}" y1="${y(b)}" y2="${y(b)}" stroke="#e5e7eb"/><text x="${L-6}" y="${y(b)+4}" font-size="11" text-anchor="end" fill="#6b7280">${b}</text>`;
  let lines = "", legend = "";
  skills.forEach((s, i) => {
    const pts = rows.filter(r => r.section === s).map(r => [x(new Date(r.created_at).getTime()), y(Math.max(3, Number(r.band)))]);
    if (!pts.length) return;
    const c = IELTS_SKILL_COLORS[s];
    lines += `<polyline fill="none" stroke="${c}" stroke-width="${s === "overall" ? 3 : 2}" ${s === "overall" ? 'stroke-dasharray="6 4"' : ""} points="${pts.map(p => p.join(",")).join(" ")}"/>`;
    lines += pts.map(p => `<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="${c}"/>`).join("");
    legend += `<span class="me-3 small"><span style="display:inline-block;width:12px;height:3px;background:${c};vertical-align:middle"></span> ${s[0].toUpperCase() + s.slice(1)}</span>`;
  });
  const fmt = t => new Date(t).toLocaleDateString(undefined, {day:"numeric", month:"short"});
  const axis = `<text x="${L}" y="${H-6}" font-size="11" fill="#6b7280">${fmt(t0)}</text><text x="${W-R}" y="${H-6}" font-size="11" text-anchor="end" fill="#6b7280">${fmt(t1)}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" class="w-100" role="img" aria-label="IELTS band progress">${grid}${lines}${axis}</svg><div>${legend}</div>`;
}

function ieltsForecastHTML(f) {
  if (!f || f.overall == null) {
    const miss = (f?.missing || []).map(s => s[0].toUpperCase() + s.slice(1)).join(", ");
    return `<div class="text-secondary">Forecast needs results in all four skills${miss ? ` (missing: ${esc(miss)})` : ""}. A full mock gives all four at once.</div>`;
  }
  const r = (v) => Number(v).toFixed(1);
  return `<div class="d-flex align-items-baseline gap-3 flex-wrap">
    <div><div class="small text-secondary">Likely exam result</div><div class="fs-3 fw-bold">${r(f.low)} – ${r(f.high)}</div></div>
    <div class="small text-secondary">centre ${r(f.overall)} · based on ${esc(f.results_used)} recent results; the range narrows as your results become more stable.</div></div>`;
}

function ieltsTypeStatsHTML(rows) {
  if (!rows?.length) return `<div class="text-secondary small">No Listening or Reading answers yet.</div>`;
  return `<table class="table table-sm align-middle mb-0"><thead><tr><th>Section</th><th>Question type</th><th>Correct</th><th style="width:30%"></th><th></th></tr></thead><tbody>${
    rows.map(r => {
      const cls = r.percent < 60 ? "bg-danger" : r.percent < 80 ? "bg-warning" : "bg-success";
      const btn = r.trainable ? `<button class="btn btn-sm btn-outline-primary" onclick="startIELTSDrill('${esc(r.section)}','${esc(r.type)}')">Practise</button>` : "";
      return `<tr><td class="text-capitalize">${esc(r.section)}</td><td>${esc(r.label)}</td><td>${esc(r.correct)}/${esc(r.total)} · <strong>${esc(r.percent)}%</strong></td>
        <td><div class="progress" style="height:8px"><div class="progress-bar ${cls}" style="width:${Number(r.percent)}%"></div></div></td><td class="text-end">${btn}</td></tr>`;
    }).join("")}</tbody></table>`;
}

async function loadIELTSAnalytics() {
  try {
    const a = await api("/api/ielts/analytics");
    document.getElementById("ieltsForecast").innerHTML = ieltsForecastHTML(a.forecast);
    document.getElementById("ieltsProgressChart").innerHTML = ieltsProgressSVG(a.history);
    document.getElementById("ieltsTypeStats").innerHTML = ieltsTypeStatsHTML(a.by_type);
    if (a.weakest?.length) {
      const w = a.weakest.map(x => `${x.label} (${x.section}, ${x.percent}%)`).join("; ");
      document.getElementById("ieltsRecommendation").insertAdjacentHTML("beforeend", `<br><span class="small">Weakest question types: ${esc(w)}.</span>`);
    }
  } catch (e) {
    document.getElementById("ieltsTypeStats").innerHTML = `<div class="text-danger small">${esc(e.message)}</div>`;
  }
}

function renderIELTSFocusBanner(section) {
  const el = document.getElementById(section === "reading" ? "ieltsReadingFocus" : "ieltsListeningFocus");
  if (!el) return;
  const t = ieltsFocus[section];
  el.innerHTML = t ? `<div class="alert alert-primary py-2 d-flex justify-content-between align-items-center">
      <span>Targeted drill: <strong>${esc(t.replace(/_/g, " "))}</strong> — most questions in the next set are of this type.</span>
      <button class="btn btn-sm btn-outline-secondary" onclick="clearIELTSDrill('${section}')">Normal mix</button></div>` : "";
}

function startIELTSDrill(section, type) {
  ieltsFocus[section] = type;
  openPage(section === "reading" ? "ielts-reading" : "ielts-listening");
  renderIELTSFocusBanner(section);
  section === "reading" ? generateIELTSReading() : generateIELTSListening();
}

function clearIELTSDrill(section) {
  ieltsFocus[section] = null;
  renderIELTSFocusBanner(section);
}

// ========================= Learn from results: Band 9 model, "one band higher", Vocabulary / Grammar links =========================
const ieltsLearnSource = {writing: null, speaking: null};
const ieltsLearnResult = {writing: null, speaking: null};

function learnPanelHTML(kind) {
  return `<div class="card-soft mt-3"><div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
      <div><h6 class="mb-0">Learn from this answer</h6><div class="small text-secondary">A Band 9 model answer and your own answer rewritten one band higher, with every change highlighted.</div></div>
      <button id="learnBtn-${kind}" class="btn btn-outline-primary" onclick="loadIELTSLearn('${kind}')">Show model answers</button></div>
    <div id="learnBox-${kind}" class="mt-3"></div></div>`;
}

// Word-level diff (LCS): deleted words struck out, inserted words highlighted.
function wordDiffHTML(before, after) {
  const tok = s => String(s || "").match(/\s+|[^\s]+/g) || [];
  const a = tok(before), b = tok(after);
  if (a.length * b.length > 4e6) return esc(after);
  const n = a.length, m = b.length;
  const dp = Array.from({length: n + 1}, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let i = 0, j = 0, out = "";
  const del = t => /^\s+$/.test(t) ? "" : `<del class="diff-del">${esc(t)}</del> `;
  const ins = t => /^\s+$/.test(t) ? esc(t) : `<ins class="diff-ins">${esc(t)}</ins>`;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out += esc(a[i]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out += del(a[i++]);
    else out += ins(b[j++]);
  }
  while (i < n) out += del(a[i++]);
  while (j < m) out += ins(b[j++]);
  return out.replace(/\n/g, "<br>");
}

async function loadIELTSLearn(kind) {
  const src = ieltsLearnSource[kind];
  const box = document.getElementById(`learnBox-${kind}`);
  const btn = document.getElementById(`learnBtn-${kind}`);
  if (!src || !box) return;
  btn.disabled = true;
  box.innerHTML = `<div class="loader">Writing the model answers…</div>`;
  try {
    const d = await api("/api/ielts/improve", {method: "POST", body: JSON.stringify({
      kind: src.kind, prompt: src.prompt, text: src.text, band: src.band,
      test_type: src.test_type || "Academic", task_number: src.task_number || 2, chart: src.chart || null})});
    ieltsLearnResult[kind] = d;
    const grammarText = [...(src.grammar || []), ...(d.grammar_focus || []).map(g => g.example_from_text)].filter(Boolean);
    box.innerHTML = `
      <ul class="nav nav-tabs small" role="tablist">
        <li class="nav-item"><button class="nav-link active" onclick="learnTab('${kind}','Up',this)" type="button">Your answer → Band ${esc(d.target_band)}</button></li>
        <li class="nav-item"><button class="nav-link" onclick="learnTab('${kind}','Model',this)" type="button">Band 9 model</button></li>
        <li class="nav-item"><button class="nav-link" onclick="learnTab('${kind}','Study',this)" type="button">Words &amp; grammar</button></li>
      </ul>
      <div class="border border-top-0 rounded-bottom p-3">
        <div class="learn-pane" id="learnUp-${kind}">
          <div class="small text-secondary mb-2"><del class="diff-del">removed</del> <ins class="diff-ins">added</ins> — your ideas are kept; only the language is raised to Band ${esc(d.target_band)}.</div>
          <div class="learn-text">${wordDiffHTML(src.text, d.upgraded)}</div>
          ${(d.changes || []).length ? `<h6 class="mt-3">Key changes</h6>${d.changes.map(c => `<div class="small mb-2"><code>${esc(c.original)}</code> → <strong>${esc(c.improved)}</strong><br><span class="text-secondary">${esc(c.why)}</span></div>`).join("")}` : ""}
        </div>
        <div class="learn-pane d-none" id="learnModel-${kind}">
          <div class="learn-text">${esc(d.model_answer || "").replace(/\n/g, "<br>")}</div>
          ${(d.model_notes || []).length ? `<h6 class="mt-3">Why this is Band 9</h6><ul class="small">${d.model_notes.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        </div>
        <div class="learn-pane d-none" id="learnStudy-${kind}">
          ${(d.vocabulary || []).length ? `<h6>Words to learn</h6>${d.vocabulary.map(v => `<div class="vocab-item"><strong>${esc(v.word)}</strong> <span class="text-secondary">${esc(v.part_of_speech || "")}</span><br>${esc(v.definition || "")}<br><em>${esc(v.example || "")}</em></div>`).join("")}
            <button class="btn btn-sm btn-primary mt-2" onclick="sendIELTSWordsToVocabulary('${kind}')">Add to Vocabulary and practise</button> <span id="learnVocabMsg-${kind}" class="small text-secondary"></span>` : ""}
          ${(d.grammar_focus || []).length ? `<h6 class="mt-3">Grammar to work on</h6>${d.grammar_focus.map(g => `<div class="small mb-2"><strong>${esc(g.topic)}:</strong> ${esc(g.rule)}<br><code>${esc(g.example_from_text || "")}</code></div>`).join("")}` : ""}
          ${grammarText.length ? `<button class="btn btn-sm btn-outline-primary" onclick="sendIELTSToGrammarCoach('${kind}')">Practise in Grammar Coach</button>` : ""}
        </div>
      </div>`;
    ieltsLearnResult[kind].grammarText = grammarText;
    btn.textContent = "Model answers ready";
  } catch (e) {
    btn.disabled = false;
    box.innerHTML = `<div class="alert alert-warning">${esc(e.message)}</div>`;
  }
}

async function sendIELTSWordsToVocabulary(kind) {
  const d = ieltsLearnResult[kind];
  const msg = document.getElementById(`learnVocabMsg-${kind}`);
  if (!d?.vocabulary?.length) return;
  const level = d.estimated_cefr || "B2";
  try {
    const r = await api("/api/ielts/vocabulary/add", {method: "POST", body: JSON.stringify({items: d.vocabulary, level})});
    msg.textContent = `Added ${r.added.length} words to your Vocabulary list.`;
  } catch (e) { msg.textContent = e.message; return; }
  currentVocabSet = {topic: "IELTS feedback", level, items: d.vocabulary};
  currentVocabPractice = null;
  openPage("vocabulary");
  document.getElementById("vocabTopic").value = "IELTS feedback";
  document.getElementById("vocabResult").innerHTML = `<h4>IELTS feedback</h4>
    ${d.vocabulary.map(x => `<div class="vocab-item"><strong>${esc(x.word)}</strong> <span class="text-secondary">${esc(x.part_of_speech || "")}</span><br>${esc(x.definition || "")}<br><em>${esc(x.example || "")}</em><br><small>Collocation: ${esc(x.collocation || "")}</small></div>`).join("")}
    <div class="d-flex align-items-center gap-2 mt-3 flex-wrap">
      ${d.vocabulary.length >= 4 ? `<button id="vocabPracticeBtn" class="btn btn-primary" onclick="startVocabPractice()">Start practice · 10 questions</button>` : `<span class="small text-secondary">Saved for review. Practice needs at least 4 words.</span>`}
    </div><div id="vocabPracticeArea" class="mt-4"></div>`;
}

function sendIELTSToGrammarCoach(kind) {
  const lines = ieltsLearnResult[kind]?.grammarText || [];
  if (!lines.length) return;
  openPage("grammar");
  document.getElementById("grammarText").value = [...new Set(lines)].slice(0, 8).join("\n");
  checkGrammar();
}

function learnTab(kind, name, btn) {
  ["Up", "Model", "Study"].forEach(n => document.getElementById(`learn${n}-${kind}`)?.classList.toggle("d-none", n !== name));
  btn.closest(".nav").querySelectorAll(".nav-link").forEach(b => b.classList.toggle("active", b === btn));
}

// ========================= Level roadmap ("Мой путь") and placement test =========================
const RM_T = {
  menu: {ru: "🧭 Мой путь", kk: "🧭 Менің жолым", en: "🧭 My path"},
  title: {ru: "Мой путь в английском", kk: "Ағылшын тіліндегі менің жолым", en: "My English path"},
  intro_title: {ru: "Определим ваш уровень", kk: "Деңгейіңізді анықтайық", en: "Let's find your level"},
  intro_text: {ru: "Входной тест покажет уровень по каждому навыку — с подуровнями от A1.1 до C2 — и построит ваш путь: что уже получается и что тренировать дальше.",
               kk: "Кіріс тесті әр дағды бойынша деңгейіңізді A1.1-ден C2-ге дейінгі ішкі деңгейлермен көрсетіп, жолыңызды құрады: не жақсы шығады және әрі қарай нені жаттықтыру керек.",
               en: "The placement test shows your level in every skill — with sublevels from A1.1 to C2 — and builds your path: what you can already do and what to practise next."},
  p1: {ru: "Грамматика и лексика — 18 вопросов, подстраиваются под ваши ответы (~8 мин)", kk: "Грамматика және лексика — жауаптарыңызға бейімделетін 18 сұрақ (~8 мин)", en: "Grammar and vocabulary — 18 questions that adapt to your answers (~8 min)"},
  p2: {ru: "Чтение — текст вашего уровня и 6 вопросов (~6 мин)", kk: "Оқылым — деңгейіңізге сай мәтін және 6 сұрақ (~6 мин)", en: "Reading — a text at your level and 6 questions (~6 min)"},
  p3: {ru: "Аудирование — запись, можно прослушать 2 раза (~6 мин)", kk: "Тыңдалым — жазба, 2 рет тыңдауға болады (~6 мин)", en: "Listening — a recording you can play twice (~6 min)"},
  p4: {ru: "Письмо — короткий текст (~8 мин, можно пропустить)", kk: "Жазылым — қысқа мәтін (~8 мин, өткізіп жіберуге болады)", en: "Writing — a short text (~8 min, can be skipped)"},
  p5: {ru: "Говорение — 2 ответа голосом до 1 минуты (нужен микрофон, можно пропустить)", kk: "Айтылым — 1 минутқа дейінгі 2 дауыстық жауап (микрофон қажет, өткізіп жіберуге болады)", en: "Speaking — 2 spoken answers up to 1 minute (microphone needed, can be skipped)"},
  intro_note: {ru: "Около 30 минут. Можно прерваться — прогресс сохраняется. Не пользуйтесь переводчиком: тест нужен, чтобы подобрать задания именно вам.",
               kk: "Шамамен 30 минут. Тоқтап, кейін жалғастыруға болады — нәтиже сақталады. Аудармашыны қолданбаңыз: тест тапсырмаларды дәл сізге лайықтау үшін керек.",
               en: "About 30 minutes. You can stop and continue later — progress is saved. Please don't use a translator: the test is there to choose tasks for you."},
  start: {ru: "Начать тест", kk: "Тестті бастау", en: "Start the test"},
  q_of: {ru: "Вопрос {n} из {t}", kk: "{t} сұрақтың {n}-сі", en: "Question {n} of {t}"},
  part: {ru: "Часть {n} из 5", kk: "5 бөлімнің {n}-бөлімі", en: "Part {n} of 5"},
  core_title: {ru: "Грамматика и лексика", kk: "Грамматика және лексика", en: "Grammar and vocabulary"},
  core_hint: {ru: "Выберите вариант для пропуска. Если не уверены — выберите наиболее вероятный.", kk: "Бос орынға сәйкес нұсқаны таңдаңыз. Сенімді болмасаңыз — ең ықтималын таңдаңыз.", en: "Choose the option that fits the gap. If you are not sure, choose the most likely one."},
  reading_title: {ru: "Чтение", kk: "Оқылым", en: "Reading"},
  listening_title: {ru: "Аудирование", kk: "Тыңдалым", en: "Listening"},
  writing_title: {ru: "Письмо", kk: "Жазылым", en: "Writing"},
  speaking_title: {ru: "Говорение", kk: "Айтылым", en: "Speaking"},
  preparing: {ru: "Готовим задание вашего уровня…", kk: "Деңгейіңізге сай тапсырма дайындалуда…", en: "Preparing a task at your level…"},
  not_available: {ru: "Эта часть сейчас недоступна (сервис ИИ занят). Её можно пропустить — уровень по ней определится позже, по первым заданиям.",
                  kk: "Бұл бөлім қазір қолжетімсіз (ЖИ сервисі бос емес). Оны өткізіп жіберуге болады — деңгей кейін, алғашқы тапсырмалар бойынша анықталады.",
                  en: "This part is unavailable right now (the AI service is busy). You can skip it — this level will be found later from your first tasks."},
  skip_part: {ru: "Пропустить часть", kk: "Бөлімді өткізу", en: "Skip this part"},
  next: {ru: "Дальше", kk: "Әрі қарай", en: "Next"},
  listening_hint: {ru: "Сначала прочитайте вопросы, затем нажмите «Слушать». Запись можно прослушать 2 раза.", kk: "Алдымен сұрақтарды оқып, содан кейін «Тыңдау» батырмасын басыңыз. Жазбаны 2 рет тыңдауға болады.", en: "Read the questions first, then press Listen. You can play the recording twice."},
  play: {ru: "▶ Слушать", kk: "▶ Тыңдау", en: "▶ Listen"},
  plays_left: {ru: "Осталось прослушиваний: {n}", kk: "Қалған тыңдау саны: {n}", en: "Plays left: {n}"},
  your_answer: {ru: "Ваш ответ", kk: "Жауабыңыз", en: "Your answer"},
  words: {ru: "слов", kk: "сөз", en: "words"},
  target_words: {ru: "Объём: {w} слов", kk: "Көлемі: {w} сөз", en: "Length: {w} words"},
  submit: {ru: "Отправить", kk: "Жіберу", en: "Submit"},
  skip_writing: {ru: "Пропустить письмо", kk: "Жазылымды өткізу", en: "Skip writing"},
  speaking_hint: {ru: "Ответьте на каждый вопрос вслух, до 1 минуты. Говорите естественно, как в разговоре.", kk: "Әр сұраққа дауыстап, 1 минутқа дейін жауап беріңіз. Әңгімедегідей табиғи сөйлеңіз.", en: "Answer each question aloud, up to 1 minute. Speak naturally, as in a conversation."},
  record: {ru: "🎙 Записать ответ", kk: "🎙 Жауапты жазу", en: "🎙 Record answer"},
  stop: {ru: "■ Остановить", kk: "■ Тоқтату", en: "■ Stop"},
  recorded: {ru: "✓ Ответ записан", kk: "✓ Жауап жазылды", en: "✓ Answer recorded"},
  rerecord: {ru: "Перезаписать", kk: "Қайта жазу", en: "Record again"},
  uploading: {ru: "Сохраняем…", kk: "Сақталуда…", en: "Saving…"},
  finish_test: {ru: "Завершить тест", kk: "Тестті аяқтау", en: "Finish the test"},
  skip_speaking: {ru: "Пропустить говорение", kk: "Айтылымды өткізу", en: "Skip speaking"},
  mic_error: {ru: "Нет доступа к микрофону.", kk: "Микрофонға рұқсат жоқ.", en: "No access to the microphone."},
  assessing: {ru: "Проверяем письмо и речь — это займёт до минуты…", kk: "Жазылым мен айтылымды тексеріп жатырмыз — бір минутқа дейін…", en: "Checking your writing and speaking — up to a minute…"},
  your_level: {ru: "Ваш уровень", kk: "Сіздің деңгейіңіз", en: "Your level"},
  not_measured: {ru: "не определён", kk: "анықталмаған", en: "not measured"},
  ielts_title: {ru: "Мостик к IELTS", kk: "IELTS-ке көпір", en: "Bridge to IELTS"},
  ielts_est: {ru: "Ориентировочно IELTS {b}", kk: "Шамамен IELTS {b}", en: "Indicative IELTS {b}"},
  ielts_early: {ru: "До IELTS пока рано: на этом уровне экзамен покажет меньше 3.0. Сначала дойдите до A2.2–B1.", kk: "IELTS-ке әлі ерте: бұл деңгейде емтихан 3.0-ден төмен көрсетеді. Алдымен A2.2–B1 деңгейіне жетіңіз.", en: "Too early for IELTS: at this level the exam would show below 3.0. Reach A2.2–B1 first."},
  ielts_hint: {ru: "Готовиться к IELTS имеет смысл с B1.2; для Band 6.5 нужен уверенный B2, для 7+ — C1.", kk: "IELTS-ке дайындықты B1.2-ден бастаған жөн; Band 6.5 үшін сенімді B2, 7+ үшін C1 қажет.", en: "IELTS preparation makes sense from B1.2; Band 6.5 needs a solid B2, 7+ needs C1."},
  ielts_current: {ru: "Ваш последний результат в IELTS-разделе: {b}", kk: "IELTS бөліміндегі соңғы нәтижеңіз: {b}", en: "Your latest result in the IELTS section: {b}"},
  ielts_open: {ru: "Открыть IELTS-раздел", kk: "IELTS бөлімін ашу", en: "Open the IELTS section"},
  ielts_note: {ru: "Соответствие ориентировочное (по данным IELTS о связи со шкалой CEFR), это не прогноз балла.", kk: "Сәйкестік шамамен алынған (IELTS-тің CEFR шкаласымен байланысы бойынша), бұл балл болжамы емес.", en: "Indicative only (based on IELTS guidance on CEFR alignment), not a score forecast."},
  map_title: {ru: "Карта уровней", kk: "Деңгейлер картасы", en: "Level map"},
  map_hint: {ru: "Нажмите на ступень, чтобы увидеть её умения.", kk: "Дағдыларын көру үшін сатыны басыңыз.", en: "Click a step to see its can-do statements."},
  st_confirmed: {ru: "Подтверждено", kk: "Расталды", en: "Confirmed"},
  st_practised: {ru: "Тренировали — нужно 70% и выше", kk: "Жаттықтыңыз — 70% және одан жоғары қажет", en: "Practised — needs 70% or more"},
  st_new: {ru: "Ещё не подтверждено", kk: "Әлі расталмаған", en: "Not confirmed yet"},
  by_test: {ru: "по входному тесту", kk: "кіріс тесті бойынша", en: "by the placement test"},
  train: {ru: "Тренировать", kk: "Жаттығу", en: "Practise"},
  current_tag: {ru: "вы здесь", kk: "сіз осындасыз", en: "you are here"},
  next_tag: {ru: "следующая цель", kk: "келесі мақсат", en: "next goal"},
  retake: {ru: "Пройти тест заново", kk: "Тестті қайта тапсыру", en: "Retake the test"},
  retake_confirm: {ru: "Начать входной тест заново? Подтверждённые умения сохранятся.", kk: "Кіріс тестін қайта бастайсыз ба? Расталған дағдылар сақталады.", en: "Start the placement test again? Confirmed skills are kept."},
  test_results: {ru: "Результаты входного теста", kk: "Кіріс тестінің нәтижелері", en: "Placement test results"},
  task_from_path: {ru: "Задание из «Моего пути»", kk: "«Менің жолымнан» тапсырма", en: "Task from My path"},
  task_label: {ru: "Тема", kk: "Тақырып", en: "Task"},
  vocab_task: {ru: "Сгенерируйте набор слов и пройдите практику — умение подтверждается результатом практики.", kk: "Сөздер жинағын жасап, жаттығудан өтіңіз — дағды жаттығу нәтижесімен расталады.", en: "Generate the word set and do the practice — the practice score confirms the skill."},
  speaking_task: {ru: "Запишите ответ на тему и нажмите «Get feedback».", kk: "Тақырып бойынша жауап жазып, «Get feedback» батырмасын басыңыз.", en: "Record an answer on the topic and press “Get feedback”."},
  back_to_path: {ru: "← к карте", kk: "← картаға", en: "← back to the map"},
  toast_confirmed: {ru: "✓ Умение подтверждено", kk: "✓ Дағды расталды", en: "✓ Skill confirmed"},
  toast_practised: {ru: "Засчитано как тренировка. Для подтверждения нужно 70% и выше (письмо и речь — уровень {l}).", kk: "Жаттығу ретінде есептелді. Растау үшін 70% және одан жоғары қажет (жазылым мен айтылым — {l} деңгейі).", en: "Counted as practice. To confirm you need 70% or more (writing and speaking: level {l})."},
  progress_line: {ru: "{p}% умений уровня {s} подтверждено", kk: "{s} деңгейі дағдыларының {p}%-ы расталды", en: "{p}% of {s} skills confirmed"},
  continue: {ru: "Продолжить тест", kk: "Тестті жалғастыру", en: "Continue the test"},
  open_path: {ru: "Открыть мой путь", kk: "Менің жолымды ашу", en: "Open my path"},
  dash_no_test: {ru: "Пройдите входной тест (≈30 мин) — и сайт построит ваш путь от текущего уровня.", kk: "Кіріс тестін тапсырыңыз (≈30 мин) — сайт ағымдағы деңгейіңізден бастап жолыңызды құрады.", en: "Take the placement test (≈30 min) and the site will build your path from your current level."},
  strengths: {ru: "Сильные стороны", kk: "Күшті жақтары", en: "Strengths"},
  improve: {ru: "Что улучшить", kk: "Нені жақсарту керек", en: "To improve"},
  skipped: {ru: "пропущено", kk: "өткізілді", en: "skipped"},
  error: {ru: "Ошибка", kk: "Қате", en: "Error"},
};
Object.assign(RM_T, {
  menu_review: {ru: "🔁 Повторение", kk: "🔁 Қайталау", en: "🔁 Review"},
  today: {ru: "Сегодня", kk: "Бүгін", en: "Today"},
  today_done: {ru: "Выполнено {d} из {t}", kk: "{t} тапсырманың {d}-і орындалды", en: "{d} of {t} done"},
  today_empty: {ru: "На сегодня всё сделано 🎉 Можно потренировать любое умение на карте.", kk: "Бүгінге бәрі орындалды 🎉 Картадағы кез келген дағдыны жаттықтыруға болады.", en: "All done for today 🎉 You can practise any skill on the map."},
  t_placement: {ru: "Пройти входной тест", kk: "Кіріс тестін тапсыру", en: "Take the placement test"},
  t_words: {ru: "Повторить слова: {n}", kk: "Сөздерді қайталау: {n}", en: "Review words: {n}"},
  t_words_done: {ru: "Слова повторены ({n} сегодня)", kk: "Сөздер қайталанды (бүгін {n})", en: "Words reviewed ({n} today)"},
  t_mistakes: {ru: "Исправить свои ошибки: {n}", kk: "Өз қателеріңді түзету: {n}", en: "Fix your mistakes: {n}"},
  t_mistakes_done: {ru: "Ошибки отработаны ({n} исправлено сегодня)", kk: "Қателер пысықталды (бүгін {n} түзетілді)", en: "Mistakes practised ({n} fixed today)"},
  t_skill: {ru: "Новое умение", kk: "Жаңа дағды", en: "New skill"},
  minutes: {ru: "{n} мин", kk: "{n} мин", en: "{n} min"},
  go: {ru: "Начать", kk: "Бастау", en: "Start"},
  saved_to_mistakes: {ru: "Ошибки сохранены в «Мои ошибки» — завтра они вернутся в новых предложениях.", kk: "Қателер «Менің қателерім» бөліміне сақталды — ертең жаңа сөйлемдерде қайта келеді.", en: "Saved to “My mistakes” — they come back tomorrow in new sentences."},
  tab_words: {ru: "Слова", kk: "Сөздер", en: "Words"},
  tab_mistakes: {ru: "Мои ошибки", kk: "Менің қателерім", en: "My mistakes"},
  words_none: {ru: "Сейчас слов на повторение нет. Новые слова приходят из Vocabulary и разборов IELTS; каждое возвращается тогда, когда его пора вспомнить.", kk: "Қазір қайталайтын сөз жоқ. Жаңа сөздер Vocabulary және IELTS талдауларынан келеді; әр сөз еске түсіретін уақыты келгенде қайтады.", en: "No words to review right now. New words come from Vocabulary and IELTS feedback; each one returns when it is time to recall it."},
  words_hint: {ru: "Вспомните значение, затем откройте ответ и честно оцените себя: от оценки зависит, когда слово вернётся.", kk: "Мағынасын еске түсіріп, жауапты ашыңыз да, өзіңізді шынайы бағалаңыз: сөздің қашан қайтатыны осыған байланысты.", en: "Recall the meaning, then show the answer and rate yourself honestly — it decides when the word comes back."},
  show_answer: {ru: "Показать ответ", kk: "Жауапты көрсету", en: "Show answer"},
  q_forgot: {ru: "Не помню", kk: "Есімде жоқ", en: "Forgot"},
  q_hard: {ru: "С трудом", kk: "Қиын болды", en: "Hard"},
  q_good: {ru: "Помню", kk: "Есімде", en: "Good"},
  q_easy: {ru: "Легко", kk: "Оңай", en: "Easy"},
  word_n: {ru: "Слово {n} из {t}", kk: "{t} сөздің {n}-сі", en: "Word {n} of {t}"},
  words_finished: {ru: "Готово: повторено {n} слов. Следующие вернутся по расписанию.", kk: "Дайын: {n} сөз қайталанды. Келесілері кесте бойынша қайтады.", en: "Done: {n} words reviewed. The next ones will come back on schedule."},
  mistakes_intro: {ru: "Сюда собираются ошибки из Grammar Coach, Writing Coach и IELTS Writing. Через день они возвращаются в новых предложениях; правильно исправленная ошибка отмечается как отработанная.", kk: "Мұнда Grammar Coach, Writing Coach және IELTS Writing қателері жиналады. Бір күннен кейін олар жаңа сөйлемдерде қайтады; дұрыс түзетілген қате пысықталған деп белгіленеді.", en: "Errors from Grammar Coach, Writing Coach and IELTS Writing are collected here. A day later they come back in new sentences; a correctly fixed error is marked as done."},
  mistakes_stats: {ru: "Открытых: {o} · готовы к тренировке: {d} · отработано: {r}", kk: "Ашық: {o} · жаттығуға дайын: {d} · пысықталған: {r}", en: "Open: {o} · ready to practise: {d} · fixed: {r}"},
  practise_mistakes: {ru: "Тренировать ошибки", kk: "Қателерді жаттықтыру", en: "Practise my mistakes"},
  mistakes_none: {ru: "Пока ошибок нет. Проверьте текст в Grammar Coach или Writing Coach — найденные ошибки появятся здесь.", kk: "Әзірге қате жоқ. Мәтінді Grammar Coach немесе Writing Coach арқылы тексеріңіз — табылған қателер осында шығады.", en: "No mistakes yet. Check a text in Grammar Coach or Writing Coach — the errors found will appear here."},
  check: {ru: "Проверить", kk: "Тексеру", en: "Check"},
  fill_gap: {ru: "Вставьте пропущенное (1–3 слова)", kk: "Бос орынды толтырыңыз (1–3 сөз)", en: "Fill the gap (1–3 words)"},
  mistakes_result: {ru: "Исправлено {c} из {t}", kk: "{t}-тің {c}-і түзетілді", en: "{c} of {t} fixed"},
  all_mistakes: {ru: "Все открытые ошибки", kk: "Барлық ашық қателер", en: "All open mistakes"},
  correct_answer: {ru: "Правильно", kk: "Дұрысы", en: "Correct"},
  mistakes_all_fixed: {ru: "Все ошибки отработаны 👍 Новые появятся после проверки текстов.", kk: "Барлық қателер пысықталды 👍 Жаңалары мәтіндерді тексергеннен кейін пайда болады.", en: "All mistakes fixed 👍 New ones appear after your texts are checked."},
});
const RM_SKILL_ICON = {grammar: "🧠", vocabulary: "🔤", reading: "📖", listening: "🎧", writing: "✍️", speaking: "🎙"};
let roadmapData = null, placementState = null, placementTimer = null, roadmapSelected = null, roadmapContext = null;
let rmRecorder = null, rmStream = null, rmChunks = [], rmRecTimer = null;

function uiLang() {
  const saved = (() => { try { return localStorage.getItem("uiLang"); } catch (e) { return null; } })();
  const fromProfile = currentProfile?.profile?.preferred_explanation_language;
  // the profile default is "en" for everyone, so an explicit choice on this device wins, and "en" from the
  // profile counts only after the learner picked it (it is then also saved on this device)
  const lang = saved || (roadmapData?.language !== "en" && roadmapData?.language) || (fromProfile !== "en" && fromProfile) || "ru";
  return ["ru", "kk", "en"].includes(lang) ? lang : "ru";
}

function T(key, vars = {}) {
  const entry = RM_T[key] || {};
  let s = entry[uiLang()] || entry.en || key;
  for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, v);
  return s;
}

function applyUiLang() {
  const sel = document.getElementById("uiLang");
  if (sel) sel.value = uiLang();
  const menu = document.getElementById("menuRoadmap");
  if (menu) menu.textContent = T("menu");
  const menuReview = document.getElementById("menuReview");
  if (menuReview) menuReview.textContent = T("menu_review");
  const ielts = document.getElementById("ieltsFeedbackLang");
  if (ielts) ielts.value = uiLang();
}

async function setUiLang(lang) {
  try { localStorage.setItem("uiLang", lang); } catch (e) {}
  if (roadmapData) roadmapData.language = lang;
  if (currentProfile?.profile) currentProfile.profile.preferred_explanation_language = lang;
  applyUiLang();
  try { await api("/api/profile/language", {method: "POST", body: JSON.stringify({language: lang})}); } catch (e) { rmToast(e.message); }
  renderRoadmapDashCard();
  renderToday();
  if (!document.getElementById("page-roadmap").classList.contains("d-none")) renderRoadmapPage();
  if (!document.getElementById("page-review").classList.contains("d-none")) renderReview();
  const page = document.querySelector(".page:not(.d-none)")?.id?.replace("page-", "");
  if (page) renderRoadmapTaskBanner(page);
}

function rmToast(text) {
  const el = document.createElement("div");
  el.className = "rm-toast";
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

function rmText(obj) { return obj?.[uiLang()] || obj?.en || ""; }

function renderRoadmapDashCard() {
  const box = document.getElementById("roadmapDashCard");
  if (!box || !currentProfile) return;
  applyUiLang();
  const r = currentProfile.roadmap;
  if (r === null || r === undefined) { box.innerHTML = ""; return; }
  if (r.current) {
    document.getElementById("navLevel").textContent = r.current;
    const p = r.progress || {percent: 0};
    box.innerHTML = `<div class="card-soft d-flex justify-content-between align-items-center flex-wrap gap-3">
      <div><div class="small text-secondary">${esc(T("your_level"))}</div><div class="fs-3 fw-bold">${esc(r.current)}</div>
        <div class="small">${esc(T("progress_line", {p: p.percent, s: r.current}))}</div>
        <div class="progress mt-1" style="height:6px;width:260px"><div class="progress-bar" style="width:${Number(p.percent)}%"></div></div></div>
      <button class="btn btn-primary" onclick="openPage('roadmap')">${esc(T("open_path"))}</button></div>`;
  } else {
    const inProgress = r.placement_status === "in_progress";
    box.innerHTML = `<div class="card-soft d-flex justify-content-between align-items-center flex-wrap gap-3">
      <div>${esc(T("dash_no_test"))}</div>
      <button class="btn btn-primary" onclick="openPage('roadmap')">${esc(inProgress ? T("continue") : T("start"))}</button></div>`;
  }
}

async function loadRoadmap() {
  clearTimeout(placementTimer);
  const root = document.getElementById("roadmapRoot");
  try {
    const [p, r] = await Promise.all([api("/api/placement"), api("/api/roadmap")]);
    placementState = p;
    if (roadmapData?.current !== r.current) roadmapSelected = null;   // after a level change show the new level
    roadmapData = r;
    [checkState, progressData] = await Promise.all([api("/api/level-check").catch(() => null), api("/api/progress/overview").catch(() => null)]);
    applyUiLang();
    renderRoadmapPage();
  } catch (e) {
    root.innerHTML = `<div class="alert alert-warning">${esc(e.message)}</div>`;
  }
}

function renderRoadmapPage() {
  const p = placementState || {};
  if (p.status === "in_progress") return renderPlacement();
  if (checkState?.active && ["preparing", "ready", "writing", "assessing"].includes(checkState.active.status)) return renderLevelCheck();
  if (roadmapData?.current) return renderRoadmapMap();
  return renderPlacementIntro();
}

function renderPlacementIntro() {
  document.getElementById("roadmapRoot").innerHTML = `
    <h2>${esc(T("title"))}</h2>
    <div class="card-soft">
      <h4>${esc(T("intro_title"))}</h4>
      <p>${esc(T("intro_text"))}</p>
      <ol>${["p1", "p2", "p3", "p4", "p5"].map(k => `<li>${esc(T(k))}</li>`).join("")}</ol>
      <p class="small text-secondary">${esc(T("intro_note"))}</p>
      <button class="btn btn-primary btn-lg" onclick="startPlacement()">${esc(T("start"))}</button>
    </div>`;
}

async function startPlacement() {
  const root = document.getElementById("roadmapRoot");
  root.innerHTML = `<div class="loader">…</div>`;
  try {
    placementState = await api("/api/placement/start", {method: "POST", body: "{}"});
    renderPlacement();
  } catch (e) { root.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; }
}

function placementHeader(n, title) {
  return `<div class="d-flex justify-content-between align-items-center mb-2">
      <h3 class="mb-0">${esc(title)}</h3><span class="badge text-bg-light">${esc(T("part", {n}))}</span></div>
    <div class="progress mb-3" style="height:6px"><div class="progress-bar" style="width:${(n - 1) * 20}%"></div></div>`;
}

function placementPoll(ms = 2000) {
  clearTimeout(placementTimer);
  placementTimer = setTimeout(async () => {
    if (document.getElementById("page-roadmap").classList.contains("d-none")) return;
    try {
      placementState = await api("/api/placement");
      if (placementState.stage === "done") { loadProfile(); return loadRoadmap(); }
      renderPlacement();
    } catch (e) { placementPoll(5000); }
  }, ms);
}

async function placementPost(path, body, isForm = false) {
  try {
    placementState = isForm ? await apiForm(path, body) : await api(path, {method: "POST", body: JSON.stringify(body)});
    if (placementState.stage === "done") { loadProfile(); return loadRoadmap(); }
    renderPlacement();
  } catch (e) { rmToast(e.message); }
}

function renderPlacement() {
  const s = placementState || {};
  const root = document.getElementById("roadmapRoot");
  const stage = s.stage;
  if (stage === "core" && s.item) {
    const it = s.item;
    root.innerHTML = placementHeader(1, T("core_title")) + `
      <div class="card-soft">
        <div class="small text-secondary mb-2">${esc(T("q_of", {n: it.number, t: it.total}))} · ${esc(T("core_hint"))}</div>
        <div class="fs-5 fw-semibold mb-3">${esc(it.question)}</div>
        <div class="d-grid gap-2">${it.options.map(o => `<button class="btn btn-outline-primary text-start"
            onclick="this.parentElement.querySelectorAll('button').forEach(b=>b.disabled=true);placementPost('/api/placement/answer', {item_id: '${esc(it.id)}', choice: ${esc(JSON.stringify(o))}})">${esc(o)}</button>`).join("")}</div>
      </div>`;
    return;
  }
  if (stage === "reading") {
    const r = s.reading || {};
    let body;
    if (r.status === "preparing") { body = `<div class="loader">${esc(T("preparing"))}</div>`; placementPoll(); }
    else if (r.status === "ready" && r.content) {
      const c = r.content;
      body = `<div class="card-soft mb-3"><h5>${esc(c.title || "")}</h5><div class="reading-passage">${esc(c.passage || "").replace(/\n/g, "<br>")}</div></div>
        ${c.questions.map(q => `<div class="card-soft my-2"><div class="fw-semibold mb-2">${esc(q.id)}. ${esc(q.question)}</div>
          ${(q.options || []).map((o, i) => `<div class="form-check"><input class="form-check-input" type="radio" name="plr${q.id}" id="plr${q.id}_${i}" value="${esc(o)}">
            <label class="form-check-label" for="plr${q.id}_${i}">${esc(o)}</label></div>`).join("")}</div>`).join("")}
        <button class="btn btn-primary" onclick="submitPlacementReading()">${esc(T("next"))}</button>`;
    } else {
      body = `<div class="alert alert-warning">${esc(T("not_available"))}</div>
        <button class="btn btn-outline-primary" onclick="placementPost('/api/placement/reading', {answers: {}})">${esc(T("skip_part"))}</button>`;
    }
    root.innerHTML = placementHeader(2, T("reading_title")) + body;
    return;
  }
  if (stage === "listening") {
    const l = s.listening || {};
    let body;
    if (l.status === "preparing") { body = `<div class="loader">${esc(T("preparing"))}</div>`; placementPoll(); }
    else if (l.status === "ready" && l.content) {
      const c = l.content;
      body = `<div class="card-soft mb-3"><p class="mb-2">${esc(T("listening_hint"))}</p>
          <button id="plPlayBtn" class="btn btn-primary" onclick="playPlacementListening('${esc(c.test_id)}')">${esc(T("play"))}</button>
          <span id="plPlayState" class="small text-secondary ms-2"></span></div>
        ${c.questions.map(q => `<div class="card-soft my-2"><div class="fw-semibold mb-2">${esc(q.id)}. ${esc(q.question)}</div>
          ${(q.options || []).length ? q.options.map((o, i) => `<div class="form-check"><input class="form-check-input" type="radio" name="pll${q.id}" id="pll${q.id}_${i}" value="${esc(o)}">
            <label class="form-check-label" for="pll${q.id}_${i}">${esc(o)}</label></div>`).join("")
            : `<input class="form-control" name="pll${q.id}" placeholder="${esc(T("your_answer"))}">`}</div>`).join("")}
        <button class="btn btn-primary" onclick="submitPlacementListening()">${esc(T("next"))}</button>`;
    } else {
      body = `<div class="alert alert-warning">${esc(T("not_available"))}</div>
        <button class="btn btn-outline-primary" onclick="placementPost('/api/placement/listening', {answers: {}})">${esc(T("skip_part"))}</button>`;
    }
    root.innerHTML = placementHeader(3, T("listening_title")) + body;
    return;
  }
  if (stage === "writing") {
    const w = s.writing || {};
    root.innerHTML = placementHeader(4, T("writing_title")) + `
      <div class="card-soft"><p class="fw-semibold mb-1">${esc(w.prompt || "")}</p>
        <p class="small text-secondary">${esc(T("target_words", {w: w.words || ""}))}</p>
        <textarea id="plWriting" class="form-control" rows="9" oninput="document.getElementById('plWords').textContent=(this.value.trim().split(/\\s+/).filter(Boolean).length)+' ${esc(T("words"))}'"></textarea>
        <div class="d-flex justify-content-between align-items-center mt-2 flex-wrap gap-2"><span id="plWords" class="small text-secondary">0 ${esc(T("words"))}</span>
          <span><button class="btn btn-outline-secondary me-2" onclick="placementPost('/api/placement/writing', {text: null})">${esc(T("skip_writing"))}</button>
          <button class="btn btn-primary" onclick="placementPost('/api/placement/writing', {text: document.getElementById('plWriting').value})">${esc(T("submit"))}</button></span></div></div>`;
    return;
  }
  if (stage === "speaking") {
    const sp = s.speaking || {};
    const answered = new Set(sp.answered || []);
    root.innerHTML = placementHeader(5, T("speaking_title")) + `
      <p>${esc(T("speaking_hint"))}</p>
      ${sp.questions.map((q, i) => `<div class="card-soft my-2"><div class="fw-semibold mb-2">${i + 1}. ${esc(q)}</div>
        <button id="plRec${i}" class="btn ${answered.has(String(i)) ? "btn-outline-secondary" : "btn-outline-primary"}" onclick="togglePlacementRecording(${i})">
          ${esc(answered.has(String(i)) ? T("rerecord") : T("record"))}</button>
        <span id="plRecState${i}" class="small ms-2 ${answered.has(String(i)) ? "text-success" : "text-secondary"}">${answered.has(String(i)) ? esc(T("recorded")) : ""}</span></div>`).join("")}
      <div class="mt-3"><button class="btn btn-outline-secondary me-2" onclick="placementPost('/api/placement/speaking/submit', {skip: true})">${esc(T("skip_speaking"))}</button>
        <button class="btn btn-primary" ${answered.size ? "" : "disabled"} onclick="placementPost('/api/placement/speaking/submit', {skip: false})">${esc(T("finish_test"))}</button></div>`;
    return;
  }
  if (stage === "assessing") {
    root.innerHTML = `<h2>${esc(T("title"))}</h2><div class="loader">${esc(T("assessing"))}</div>`;
    placementPoll(3000);
    return;
  }
  loadRoadmap();
}

function submitPlacementReading() {
  const answers = {};
  (placementState.reading?.content?.questions || []).forEach(q => {
    const el = document.querySelector(`input[name="plr${q.id}"]:checked`);
    if (el) answers[String(q.id)] = el.value;
  });
  placementPost("/api/placement/reading", {answers});
}

function submitPlacementListening() {
  const answers = {};
  (placementState.listening?.content?.questions || []).forEach(q => {
    const el = document.querySelector(`input[name="pll${q.id}"]:checked`) || document.querySelector(`input[name="pll${q.id}"]:not([type=radio])`);
    if (el && el.value.trim()) answers[String(q.id)] = el.value.trim();
  });
  if (window.rmAudio) { window.rmAudio.pause(); window.rmAudio = null; }
  placementPost("/api/placement/listening", {answers});
}

async function playPlacementListening(testId) {
  const btn = document.getElementById("plPlayBtn");
  const state = document.getElementById("plPlayState");
  btn.disabled = true;
  state.textContent = T("preparing");
  try {
    const res = await fetch(`/api/listening/${testId}/audio`, {headers: {"Authorization": `Bearer ${currentSession.access_token}`}});
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      if (res.status !== 403) btn.disabled = false;
      state.textContent = d.detail || `Error ${res.status}`;
      return;
    }
    const left = res.headers.get("X-Plays-Left");
    const audio = new Audio(URL.createObjectURL(await res.blob()));
    window.rmAudio = audio;
    state.textContent = left !== null ? T("plays_left", {n: left}) : "";
    audio.onended = () => { if (left !== "0") btn.disabled = false; };
    await audio.play();
  } catch (e) { btn.disabled = false; state.textContent = e.message; }
}

async function togglePlacementRecording(i) {
  const btn = document.getElementById(`plRec${i}`);
  const st = document.getElementById(`plRecState${i}`);
  if (rmRecorder && rmRecorder.state === "recording") { rmRecorder.stop(); return; }
  try {
    rmStream = await navigator.mediaDevices.getUserMedia({audio: true});
  } catch (e) { rmToast(T("mic_error")); return; }
  const preferred = "audio/webm;codecs=opus";
  rmRecorder = new MediaRecorder(rmStream, MediaRecorder.isTypeSupported(preferred) ? {mimeType: preferred} : {});
  rmChunks = [];
  rmRecorder.ondataavailable = e => { if (e.data.size) rmChunks.push(e.data); };
  let seconds = 60;
  rmRecorder.onstop = async () => {
    clearInterval(rmRecTimer);
    rmStream.getTracks().forEach(t => t.stop());
    btn.disabled = true;
    st.textContent = T("uploading");
    const fd = new FormData();
    fd.append("audio", new Blob(rmChunks, {type: "audio/webm"}), "answer.webm");
    await placementPost(`/api/placement/speaking/answer/${i}`, fd, true);
  };
  rmRecorder.start();
  btn.textContent = T("stop");
  btn.className = "btn btn-danger";
  st.textContent = "60 s";
  rmRecTimer = setInterval(() => {
    seconds -= 1;
    st.textContent = `${seconds} s`;
    if (seconds <= 0 && rmRecorder.state === "recording") rmRecorder.stop();
  }, 1000);
}

function renderRoadmapMap() {
  const r = roadmapData;
  const cur = r.current;
  if (!roadmapSelected || !r.sublevels.includes(roadmapSelected)) roadmapSelected = cur;
  const lv = r.levels || {};
  const main = cur.slice(0, 2);
  const skillsRow = r.skills.map(s => `<div class="col-6 col-md-4 col-xl-2"><div class="card-soft text-center h-100">
      <div class="small text-secondary">${RM_SKILL_ICON[s]} ${esc(rmText(r.skill_names[s]))}</div>
      <div class="fs-4 fw-bold">${esc(lv[s] || "—")}</div>${lv[s] ? "" : `<div class="small text-secondary">${esc(T("not_measured"))}</div>`}</div></div>`).join("");
  const ladder = r.sublevels.map(sub => {
    const pr = r.progress[sub];
    const cls = sub === cur ? "rm-step current" : sub === r.next ? "rm-step next" : (r.sublevels.indexOf(sub) < r.sublevels.indexOf(cur) ? "rm-step done" : "rm-step");
    const tag = sub === cur ? T("current_tag") : sub === r.next ? T("next_tag") : "";
    return `<button class="${cls} ${sub === roadmapSelected ? "selected" : ""}" onclick="roadmapSelected='${sub}';renderRoadmapMap()">
        <div class="fw-bold">${sub}</div><div class="rm-bar"><div style="width:${pr.percent}%"></div></div>
        <div class="small">${pr.percent}%</div>${tag ? `<div class="rm-tag">${esc(tag)}</div>` : ""}</button>`;
  }).join("");
  const status = r.status || {};
  const items = r.items.filter(it => it.sub === roadmapSelected);
  const list = r.skills.map(skill => {
    const its = items.filter(it => it.skill === skill);
    if (!its.length) return "";
    return `<div class="mb-3"><div class="fw-semibold mb-1">${RM_SKILL_ICON[skill]} ${esc(rmText(r.skill_names[skill]))}</div>
      ${its.map(it => {
        const st = status[it.id] || {};
        const icon = st.status === "confirmed" ? "✅" : st.status === "practised" ? "◐" : "○";
        const label = st.status === "confirmed" ? `${T("st_confirmed")}${st.source === "placement" ? " " + T("by_test") : st.score != null ? ` · ${st.score}%` : ""}`
          : st.status === "practised" ? `${T("st_practised")}${st.score != null ? ` · ${st.score}%` : ""}` : T("st_new");
        return `<div class="rm-item d-flex justify-content-between align-items-center gap-2">
          <div><span class="me-2">${icon}</span>${esc(rmText(it.text))}<div class="small text-secondary ms-4">${esc(label)}</div></div>
          <button class="btn btn-sm ${st.status === "confirmed" ? "btn-outline-secondary" : "btn-outline-primary"}" onclick="launchRoadmapItem('${it.id}')">${esc(T("train"))}</button></div>`;
      }).join("")}</div>`;
  }).join("");
  const ib = r.ielts || {};
  const ielts = `<div class="card-soft h-100"><h5>🎯 ${esc(T("ielts_title"))}</h5>
      ${ib.estimate ? `<div class="fs-4 fw-bold">${esc(T("ielts_est", {b: ib.estimate}))}</div>` : `<p>${esc(T("ielts_early"))}</p>`}
      <p class="small mb-1">${esc(T("ielts_hint"))}</p>
      ${ib.current_band != null ? `<p class="small mb-1">${esc(T("ielts_current", {b: Number(ib.current_band).toFixed(1)}))}</p>` : ""}
      <p class="small text-secondary">${esc(T("ielts_note"))}</p>
      ${r.sublevels.indexOf(cur) >= r.sublevels.indexOf("B1.2") ? `<button class="btn btn-sm btn-outline-primary" onclick="openPage('ielts-dashboard')">${esc(T("ielts_open"))}</button>` : ""}</div>`;
  const pr = r.progress[cur];
  document.getElementById("roadmapRoot").innerHTML = `
    <div class="d-flex justify-content-between align-items-center flex-wrap gap-2 mb-3">
      <h2 class="mb-0">${esc(T("title"))}</h2>
      <button class="btn btn-sm btn-outline-secondary" onclick="if(confirm(T('retake_confirm'))) startPlacement()">${esc(T("retake"))}</button></div>
    <div class="row g-3 mb-3">
      <div class="col-lg-8"><div class="card-soft h-100"><div class="small text-secondary">${esc(T("your_level"))}</div>
        <div class="d-flex align-items-baseline gap-3 flex-wrap"><div class="rm-level">${esc(cur)}</div>
          <div>${esc(rmText(r.level_summary[main]))}</div></div>
        <div class="small mt-2">${esc(T("progress_line", {p: pr.percent, s: cur}))}</div>
        <div class="progress mt-1" style="height:8px"><div class="progress-bar" style="width:${pr.percent}%"></div></div></div></div>
      <div class="col-lg-4">${ielts}</div></div>
    <div class="row g-2 mb-3">${skillsRow}</div>
    <div class="row g-3 mb-3"><div class="col-lg-6">${levelCheckCardHTML()}</div><div class="col-lg-6">${goalCardHTML()}</div></div>
    ${placementResultsHTML()}
    <div class="card-soft"><h5>${esc(T("map_title"))}</h5><div class="small text-secondary mb-2">${esc(T("map_hint"))}</div>
      <div class="rm-ladder mb-3">${ladder}</div>
      <h5>${esc(roadmapSelected)} · <span class="fw-normal">${esc(rmText(r.level_summary[roadmapSelected.slice(0, 2)]))}</span></h5>
      ${list}</div>`;
}

function placementResultsHTML() {
  const p = placementState || {};
  if (p.stage !== "done" || !p.parts) return "";
  const rows = ["grammar", "vocabulary", "reading", "listening", "writing", "speaking"].map(k => {
    const part = p.parts[k] || {};
    const fb = part.feedback || {};
    const val = part.sub || (part.status === "skipped" ? T("skipped") : part.status === "error" ? T("error") : "—");
    const extra = [fb.comment, ...(fb.strengths || []).map(x => "+ " + x), ...(fb.improve || []).map(x => "→ " + x)].filter(Boolean);
    return `<div class="py-1 border-bottom"><strong>${RM_SKILL_ICON[k]} ${esc(rmText(roadmapData.skill_names[k]))}: ${esc(val)}</strong>
      ${part.percent != null ? ` <span class="small text-secondary">(${esc(part.percent)}%)</span>` : ""}
      ${extra.length ? `<div class="small text-secondary">${extra.map(esc).join("<br>")}</div>` : ""}</div>`;
  }).join("");
  return `<details class="card-soft mb-3"><summary class="fw-semibold">${esc(T("test_results"))}</summary><div class="mt-2">${rows}</div></details>`;
}

const RM_MODULE_PAGE = {test: "test", vocabulary: "vocabulary", listening: "listening", writing: "writing", speaking: "speaking"};

function rmSelect(id, value, label) {
  const sel = document.getElementById(id);
  if (!sel) return;
  if (![...sel.options].some(o => o.value === value)) sel.add(new Option(label || value, value));
  sel.value = value;
}

function launchRoadmapItem(itemId) {
  const it = (roadmapData?.items || []).find(x => x.id === itemId) ||
    (todayData?.tasks || []).map(t => t.item).find(x => x && x.id === itemId);
  if (!it) return;
  const a = it.action;
  const level = it.sub.slice(0, 2);
  roadmapContext = {item_id: it.id, module: a.module, level, text: it.text, action: a};
  openPage(RM_MODULE_PAGE[a.module]);
  if (a.module === "test") {
    rmSelect("testLevel", level);
    rmSelect("testFocus", a.focus, `🧭 ${a.focus}`);
    generateTest();
  } else if (a.module === "vocabulary") {
    rmSelect("vocabLevel", level);
    document.getElementById("vocabTopic").value = a.topic;
    makeVocab();
  } else if (a.module === "listening") {
    rmSelect("listeningLevel", level);
    rmSelect("listeningFormat", a.format);
    document.getElementById("listeningTopic").value = a.topic || "";
    generateListening();
  } else if (a.module === "writing") {
    rmSelect("writingPurpose", a.purpose);
    document.getElementById("writingText").value = "";
    document.getElementById("writingText").placeholder = a.task;
  } else if (a.module === "speaking") {
    rmSelect("scenario", a.scenario);
  }
}

function renderRoadmapTaskBanner(page) {
  document.querySelectorAll(".rm-task-banner").forEach(el => el.remove());
  if (!roadmapContext || RM_MODULE_PAGE[roadmapContext.module] !== page) return;
  const a = roadmapContext.action;
  const extra = a.module === "writing" ? `<div class="mt-1"><strong>${esc(T("task_label"))}:</strong> ${esc(a.task)}</div>`
    : a.module === "vocabulary" ? `<div class="small mt-1">${esc(T("vocab_task"))}</div>`
    : a.module === "speaking" ? `<div class="small mt-1">${esc(T("speaking_task"))} <strong>${esc(a.scenario)}</strong></div>` : "";
  document.getElementById(`page-${page}`).insertAdjacentHTML("afterbegin",
    `<div class="alert alert-primary rm-task-banner d-flex justify-content-between align-items-start gap-2">
      <div><strong>🧭 ${esc(T("task_from_path"))} · ${esc(roadmapContext.level)}:</strong> ${esc(rmText(roadmapContext.text))}${extra}</div>
      <button class="btn btn-sm btn-light" onclick="roadmapContext=null;openPage('roadmap')">${esc(T("back_to_path"))}</button></div>`);
}

async function roadmapReport(module, score, level) {
  if (!roadmapContext || roadmapContext.module !== module) return;
  const ctx = roadmapContext;
  roadmapContext = null;
  try {
    const r = await api("/api/roadmap/evidence", {method: "POST", body: JSON.stringify({item_id: ctx.item_id, module, score, level: level || null})});
    rmToast(r.confirmed ? `${T("toast_confirmed")}: ${rmText(ctx.text)}` : T("toast_practised", {l: ctx.level}));
  } catch (e) { rmToast(e.message); }
}

// ========================= Today, word review (spaced repetition) and my mistakes =========================
let todayData = null, reviewTab = "words", reviewWords = null, reviewIndex = 0, reviewDone = 0, reviewFlipped = false;
let mistakesData = null, mistakesPractice = null, mistakesResult = null;

async function loadToday() {
  try {
    [todayData, progressData] = await Promise.all([api("/api/today"), api("/api/progress/overview").catch(() => progressData)]);
    renderToday();
  }
  catch (e) { const box = document.getElementById("todayCard"); if (box) box.innerHTML = ""; }
}

function renderToday() {
  const box = document.getElementById("todayCard");
  if (!box || !todayData) return;
  const rows = todayData.tasks.map(t => {
    let title, action;
    if (t.kind === "placement") { title = T("t_placement"); action = "openPage('roadmap')"; }
    else if (t.kind === "words") { title = t.done ? T("t_words_done", {n: t.reviewed_today}) : T("t_words", {n: t.count}); action = "openReview('words')"; }
    else if (t.kind === "mistakes") { title = t.done ? T("t_mistakes_done", {n: t.resolved_today}) : T("t_mistakes", {n: t.count}); action = "openReview('mistakes')"; }
    else if (t.kind === "skill") { title = `${T("t_skill")} · ${t.item.sub}: ${rmText(t.item.text)}`; action = `launchRoadmapItem('${t.item.id}')`; }
    return `<div class="d-flex justify-content-between align-items-center gap-2 py-2 border-bottom">
      <div><span class="me-2">${t.done ? "✅" : "⬜"}</span>${esc(title)} <span class="small text-secondary">· ${esc(T("minutes", {n: t.minutes}))}</span></div>
      ${t.done && t.kind !== "skill" ? "" : `<button class="btn btn-sm ${t.done ? "btn-outline-secondary" : "btn-primary"}" onclick="${action}">${esc(T("go"))}</button>`}</div>`;
  }).join("");
  box.innerHTML = `<div class="card-soft"><div class="d-flex justify-content-between align-items-center">
      <h5 class="mb-1">📅 ${esc(T("today"))}</h5><span class="small text-secondary">${streakBadgeHTML()} ${esc(T("today_done", {d: todayData.done, t: todayData.total}))}</span></div>
    ${rows || `<div class="text-secondary">${esc(T("today_empty"))}</div>`}</div>`;
}

function openReview(tab) { reviewTab = tab; openPage("review"); }

async function loadReview() {
  const root = document.getElementById("reviewRoot");
  root.innerHTML = `<div class="loader">…</div>`;
  try {
    const [w, m] = await Promise.all([api("/api/review/words"), api("/api/mistakes")]);
    reviewWords = w.words || []; reviewIndex = 0; reviewDone = 0; reviewFlipped = false;
    mistakesData = m; mistakesPractice = null; mistakesResult = null;
    renderReview();
  } catch (e) { root.innerHTML = `<div class="alert alert-warning">${esc(e.message)}</div>`; }
}

function renderReview() {
  applyUiLang();
  const tabs = `<ul class="nav nav-tabs mb-3">
      ${["words", "mistakes"].map(t => `<li class="nav-item"><button class="nav-link ${reviewTab === t ? "active" : ""}" onclick="reviewTab='${t}';renderReview()">${esc(T("tab_" + t))}${t === "words" && reviewWords ? ` <span class="badge text-bg-light">${reviewWords.length - reviewIndex}</span>` : t === "mistakes" && mistakesData ? ` <span class="badge text-bg-light">${mistakesData.due_count}</span>` : ""}</button></li>`).join("")}</ul>`;
  document.getElementById("reviewRoot").innerHTML = `<h2>${esc(T("menu_review").replace(/^\S+\s/, ""))}</h2>${tabs}<div id="reviewBody"></div>`;
  reviewTab === "words" ? renderWordCard() : renderMistakes();
}

function renderWordCard() {
  const body = document.getElementById("reviewBody");
  if (!reviewWords?.length) { body.innerHTML = `<div class="card-soft text-secondary">${esc(T("words_none"))}</div>`; return; }
  if (reviewIndex >= reviewWords.length) {
    body.innerHTML = `<div class="card-soft">✅ ${esc(T("words_finished", {n: reviewDone}))}</div>`;
    loadToday();
    return;
  }
  const w = reviewWords[reviewIndex];
  body.innerHTML = `<p class="small text-secondary">${esc(T("words_hint"))}</p>
    <div class="card-soft text-center rm-flashcard">
      <div class="small text-secondary">${esc(T("word_n", {n: reviewIndex + 1, t: reviewWords.length}))}${w.topic ? ` · ${esc(w.topic)}` : ""}</div>
      <div class="display-6 fw-bold my-2">${esc(w.word)} <button class="btn btn-sm btn-outline-secondary" onclick="speakText(${esc(JSON.stringify(w.word))}, this)">🔊</button></div>
      <div class="text-secondary">${esc(w.part_of_speech || "")}</div>
      ${reviewFlipped ? `<hr><div class="fs-5">${esc(w.definition || "")}</div>
        ${w.translation ? `<div class="text-secondary">${esc(w.translation)}</div>` : ""}
        ${w.example_sentence ? `<div class="mt-2"><em>${esc(w.example_sentence)}</em></div>` : ""}
        ${w.collocation ? `<div class="small mt-1">${esc(w.collocation)}</div>` : ""}
        <div class="d-flex justify-content-center gap-2 flex-wrap mt-3">
          ${[[1, "q_forgot", "btn-outline-danger"], [3, "q_hard", "btn-outline-warning"], [4, "q_good", "btn-outline-primary"], [5, "q_easy", "btn-outline-success"]]
            .map(([q, k, cls]) => `<button class="btn ${cls}" onclick="rateWord(${q})">${esc(T(k))}</button>`).join("")}</div>`
      : `<button class="btn btn-primary mt-3" onclick="reviewFlipped=true;renderWordCard()">${esc(T("show_answer"))}</button>`}
    </div>`;
}

async function rateWord(quality) {
  const w = reviewWords[reviewIndex];
  try { await api(`/api/review/words/${w.id}`, {method: "POST", body: JSON.stringify({quality})}); reviewDone++; }
  catch (e) { rmToast(e.message); }
  if (quality < 3) reviewWords.push(w);   // a forgotten word comes back once more in this session
  reviewIndex++; reviewFlipped = false;
  renderReview();
}

function renderMistakes() {
  const body = document.getElementById("reviewBody");
  const m = mistakesData || {open: [], open_count: 0, due_count: 0, resolved_count: 0};
  let practice = "";
  if (mistakesPractice) {
    practice = `<div class="card-soft mb-3">${mistakesPractice.items.map(it => {
      const r = mistakesResult?.details?.find(d => d.n === it.n);
      return `<div class="mb-3"><div class="small text-secondary">${esc(it.rule || "")}</div>
        <div class="fw-semibold mb-1">${it.n}. ${esc(it.sentence)}</div>
        <input class="form-control" id="mist${it.n}" placeholder="${esc(T("fill_gap"))}" ${mistakesResult ? "disabled" : ""} value="${esc(r?.answer || "")}">
        ${r ? `<div class="small mt-1 ${r.correct ? "text-success" : "text-danger"}">${r.correct ? "✓" : `✗ ${esc(T("correct_answer"))}: <strong>${esc(r.expected)}</strong>`} ${esc(r.explanation || "")}</div>` : ""}</div>`;
    }).join("")}
    ${mistakesResult ? `<div class="alert alert-primary">${esc(T("mistakes_result", {c: mistakesResult.correct, t: mistakesResult.total}))}</div>`
      : `<button class="btn btn-primary" onclick="submitMistakes()">${esc(T("check"))}</button>`}</div>`;
  }
  body.innerHTML = `<p class="small text-secondary">${esc(T("mistakes_intro"))}</p>
    <div class="d-flex justify-content-between align-items-center flex-wrap gap-2 mb-3">
      <span>${esc(T("mistakes_stats", {o: m.open_count, d: m.due_count, r: m.resolved_count}))}</span>
      <button id="mistBtn" class="btn btn-primary" ${m.due_count ? "" : "disabled"} onclick="startMistakesPractice()">${esc(T("practise_mistakes"))}</button></div>
    ${practice}
    ${m.open.length ? `<details class="card-soft" ${mistakesPractice ? "" : "open"}><summary class="fw-semibold">${esc(T("all_mistakes"))}</summary>
      ${m.open.map(x => `<div class="py-1 border-bottom"><code>${esc(x.original_fragment)}</code> → <strong>${esc(x.corrected_fragment)}</strong>
        <div class="small text-secondary">${esc(x.rule_name || "")}${x.explanation ? " — " + esc(x.explanation) : ""}</div></div>`).join("")}</details>`
      : `<div class="card-soft text-secondary">${esc(T(m.resolved_count ? "mistakes_all_fixed" : "mistakes_none"))}</div>`}`;
}

async function startMistakesPractice() {
  const btn = document.getElementById("mistBtn");
  btn.disabled = true;
  btn.textContent = "…";
  try { mistakesPractice = await api("/api/mistakes/practice", {method: "POST", body: "{}"}); mistakesResult = null; renderMistakes(); }
  catch (e) { rmToast(e.message); btn.disabled = false; btn.textContent = T("practise_mistakes"); }
}

async function submitMistakes() {
  const answers = {};
  mistakesPractice.items.forEach(it => { answers[String(it.n)] = document.getElementById(`mist${it.n}`).value; });
  try {
    mistakesResult = await api("/api/mistakes/submit", {method: "POST", body: JSON.stringify({test_id: mistakesPractice.test_id, answers})});
    mistakesData = await api("/api/mistakes");
    renderReview();
    loadToday();
  } catch (e) { rmToast(e.message); }
}

// ========================= Streak, goals and the level check =========================
let checkState = null, progressData = null, checkTimer = null;
Object.assign(RM_T, {
  streak: {ru: "🔥 Серия: {n} дн.", kk: "🔥 Қатарынан: {n} күн", en: "🔥 Streak: {n} days"},
  streak_best: {ru: "рекорд {n}", kk: "рекорд {n}", en: "best {n}"},
  check_title: {ru: "Контрольный тест на {t}", kk: "{t} деңгейіне бақылау тесті", en: "Level check for {t}"},
  check_locked: {ru: "Откроется, когда подтверждено {u}% умений уровня {c} (сейчас {p}%).", kk: "{c} деңгейі дағдыларының {u}%-ы расталғанда ашылады (қазір {p}%).", en: "Unlocks when {u}% of {c} skills are confirmed (now {p}%)."},
  check_ready: {ru: "Можно сдавать: 15 вопросов (грамматика, лексика, чтение) и короткий текст. ~20 минут. Для перехода нужно 75% и письмо не ниже уровня на одну ступень.", kk: "Тапсыруға болады: 15 сұрақ (грамматика, лексика, оқылым) және қысқа мәтін. ~20 минут. Өту үшін 75% және жазылым бір сатыдан төмен болмауы керек.", en: "Ready: 15 questions (grammar, vocabulary, reading) and a short text, ~20 minutes. To pass you need 75% and writing no more than one step below."},
  check_start: {ru: "Сдать контрольный тест", kk: "Бақылау тестін тапсыру", en: "Take the level check"},
  check_retry: {ru: "Повторная попытка будет доступна {d}.", kk: "Қайта тапсыру {d} мүмкін болады.", en: "You can try again {d}."},
  check_top: {ru: "Вы на вершине шкалы — C2.", kk: "Сіз шкаланың шыңындасыз — C2.", en: "You are at the top of the scale — C2."},
  check_preparing: {ru: "Составляем контрольный тест и проверяем ключи — до минуты…", kk: "Бақылау тестін құрастырып, кілттерін тексеріп жатырмыз — бір минутқа дейін…", en: "Building the level check and checking the answer keys — up to a minute…"},
  check_failed_prep: {ru: "Не удалось составить тест (сервис ИИ занят). Попробуйте ещё раз.", kk: "Тест құрастырылмады (ЖИ сервисі бос емес). Қайталап көріңіз.", en: "Could not build the test (the AI service is busy). Please try again."},
  check_writing: {ru: "Последняя часть — письмо. Объём 100–250 слов в зависимости от уровня.", kk: "Соңғы бөлім — жазылым. Деңгейге қарай 100–250 сөз.", en: "Last part — writing, 100–250 words depending on the level."},
  check_objective: {ru: "Тестовая часть: {p}%", kk: "Тест бөлімі: {p}%", en: "Test part: {p}%"},
  check_assessing: {ru: "Проверяем письмо…", kk: "Жазылымды тексеріп жатырмыз…", en: "Checking your writing…"},
  check_passed: {ru: "🎉 Тест сдан! Ваш новый уровень — {t}.", kk: "🎉 Тест тапсырылды! Жаңа деңгейіңіз — {t}.", en: "🎉 Passed! Your new level is {t}."},
  check_failed: {ru: "Пока не хватило до {t}. Подтяните слабые места и попробуйте снова через сутки.", kk: "{t} деңгейіне әзірге жетпеді. Әлсіз тұстарды жақсартып, бір тәуліктен кейін қайталаңыз.", en: "Not yet {t}. Work on the weak areas and try again in 24 hours."},
  check_weak: {ru: "Слабые места", kk: "Әлсіз тұстар", en: "Weak areas"},
  check_writing_level: {ru: "Письмо: {s}", kk: "Жазылым: {s}", en: "Writing: {s}"},
  goal_title: {ru: "Моя цель", kk: "Менің мақсатым", en: "My goal"},
  goal_target: {ru: "Уровень", kk: "Деңгей", en: "Level"},
  goal_date: {ru: "К дате", kk: "Мерзімі", en: "By"},
  goal_days: {ru: "Дней в неделю", kk: "Аптасына күн", en: "Days a week"},
  save: {ru: "Сохранить", kk: "Сақтау", en: "Save"},
  goal_eta: {ru: "При {d} дн./нед. по ~30 мин: примерно {h} ч, ориентировочно к {e}.", kk: "Аптасына {d} күн ~30 минуттан: шамамен {h} сағ, болжам бойынша {e}.", en: "At {d} days/week × ~30 min: about {h} h, roughly by {e}."},
  goal_on_track: {ru: "✅ Успеваете к намеченной дате.", kk: "✅ Белгіленген мерзімге үлгересіз.", en: "✅ On track for your date."},
  goal_behind: {ru: "⚠️ К этой дате нужно заниматься ~{n} дн. в неделю.", kk: "⚠️ Бұл мерзімге аптасына ~{n} күн оқу қажет.", en: "⚠️ For this date you need ~{n} days a week."},
  goal_unrealistic: {ru: "⚠️ К этой дате не успеть даже при ежедневных занятиях по 30 минут — сдвиньте дату или занимайтесь дольше.", kk: "⚠️ Күн сайын 30 минуттан оқысаңыз да бұл мерзімге үлгермейсіз — мерзімді ауыстырыңыз немесе ұзағырақ оқыңыз.", en: "⚠️ Not reachable by this date even with 30 minutes daily — move the date or study longer."},
  goal_actual: {ru: "За последние 4 недели вы занимались в среднем {n} дн. в неделю.", kk: "Соңғы 4 аптада орташа есеппен аптасына {n} күн оқыдыңыз.", en: "Over the last 4 weeks you studied {n} days a week on average."},
  goal_note: {ru: "Оценка по ориентировочным часам Cambridge для уровней CEFR.", kk: "Бағалау CEFR деңгейлеріне арналған Cambridge-дің шамамен берілген сағаттары бойынша.", en: "Estimate based on Cambridge's approximate guided learning hours for CEFR levels."},
});

function streakBadgeHTML() {
  const st = progressData?.streak;
  if (!st) return "";
  return `<span class="badge ${st.current ? "text-bg-warning" : "text-bg-light"} me-2" title="${esc(T("streak_best", {n: st.best}))}">${esc(T("streak", {n: st.current}))}</span>`;
}

function fmtDate(iso) {
  try { return new Date(iso).toLocaleDateString(uiLang() === "kk" ? "kk-KZ" : uiLang() === "en" ? "en-GB" : "ru-RU", {day: "numeric", month: "long", year: "numeric"}); }
  catch (e) { return iso; }
}

function levelCheckCardHTML() {
  const c = checkState;
  if (!c) return "";
  let body;
  if (c.reason === "top") body = `<p class="mb-0">${esc(T("check_top"))}</p>`;
  else if (c.reason === "no_level") return "";
  else {
    const fin = c.finished?.result;
    const last = fin ? `<div class="alert ${fin.passed ? "alert-success" : "alert-warning"} py-2 small">
        ${esc(fin.passed ? T("check_passed", {t: fin.target}) : T("check_failed", {t: fin.target}))}
        <div>${esc(T("check_objective", {p: fin.objective.percent}))}${fin.writing_sub ? " · " + esc(T("check_writing_level", {s: fin.writing_sub})) : ""}</div>
        ${fin.weak?.length ? `<div>${esc(T("check_weak"))}: ${fin.weak.map(w => esc(rmText(roadmapData.skill_names[w]))).join(", ")}</div>` : ""}
        ${fin.writing_feedback?.comment ? `<div class="text-secondary">${esc(fin.writing_feedback.comment)}</div>` : ""}</div>` : "";
    const err = c.active?.status === "error" ? `<div class="alert alert-warning py-2 small">${esc(T("check_failed_prep"))}</div>` : "";
    body = `${last}${err}<div class="progress mb-2" style="height:6px"><div class="progress-bar ${c.percent >= c.unlock_percent ? "bg-success" : ""}" style="width:${Math.min(100, c.percent)}%"></div></div>
      ${c.available ? `<p class="small">${esc(T("check_ready"))}</p><button class="btn btn-primary" onclick="startLevelCheck()">${esc(T("check_start"))}</button>`
        : c.retry_at ? `<p class="small mb-0">${esc(T("check_retry", {d: fmtDate(c.retry_at)}))}</p>`
        : `<p class="small mb-0">${esc(T("check_locked", {u: c.unlock_percent, c: c.current, p: c.percent}))}</p>`}`;
  }
  return `<div class="card-soft h-100"><h5>🏁 ${esc(T("check_title", {t: c.target || ""}))}</h5>${body}</div>`;
}

function goalCardHTML() {
  const g = progressData?.goal || {};
  const f = progressData?.forecast;
  const cur = roadmapData?.current;
  const options = roadmapData.sublevels.filter(x => roadmapData.sublevels.indexOf(x) > roadmapData.sublevels.indexOf(cur))
    .map(x => `<option ${g.target === x ? "selected" : ""}>${x}</option>`).join("");
  let fc = "";
  if (f) {
    fc = `<div class="small mt-2">${esc(T("goal_eta", {d: f.days_per_week_used, h: f.hours, e: fmtDate(f.eta)}))}</div>`;
    if (f.target_date) fc += `<div class="small">${esc(f.on_track ? T("goal_on_track") : f.realistic ? T("goal_behind", {n: Math.ceil(f.needed_days_per_week)}) : T("goal_unrealistic"))}</div>`;
    fc += `<div class="small text-secondary">${esc(T("goal_actual", {n: f.actual_days_per_week}))} ${esc(T("goal_note"))}</div>`;
  }
  return `<div class="card-soft h-100"><div class="d-flex justify-content-between"><h5>🎯 ${esc(T("goal_title"))}</h5>${streakBadgeHTML()}</div>
    ${options ? `<div class="row g-2 align-items-end">
      <div class="col-4"><label class="form-label small mb-0">${esc(T("goal_target"))}</label><select id="goalTarget" class="form-select form-select-sm"><option value=""></option>${options}</select></div>
      <div class="col-4"><label class="form-label small mb-0">${esc(T("goal_date"))}</label><input id="goalDate" type="date" class="form-control form-control-sm" value="${esc(g.target_date || "")}"></div>
      <div class="col-2"><label class="form-label small mb-0">${esc(T("goal_days"))}</label><select id="goalDays" class="form-select form-select-sm">${[1, 2, 3, 4, 5, 6, 7].map(n => `<option ${Number(g.days_per_week || 4) === n ? "selected" : ""}>${n}</option>`).join("")}</select></div>
      <div class="col-2 d-grid"><button class="btn btn-sm btn-primary" onclick="saveGoal()">${esc(T("save"))}</button></div></div>${fc}`
      : `<p class="mb-0">${esc(T("check_top"))}</p>`}</div>`;
}

async function saveGoal() {
  try {
    progressData = await api("/api/goals", {method: "POST", body: JSON.stringify({
      target: document.getElementById("goalTarget").value || null,
      target_date: document.getElementById("goalDate").value || null,
      days_per_week: Number(document.getElementById("goalDays").value)})});
    renderRoadmapMap();
  } catch (e) { rmToast(e.message); }
}

async function startLevelCheck() {
  try { checkState = await api("/api/level-check/start", {method: "POST", body: "{}"}); renderLevelCheck(); }
  catch (e) { rmToast(e.message); }
}

function pollLevelCheck(ms = 2500) {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(async () => {
    if (document.getElementById("page-roadmap").classList.contains("d-none")) return;
    try { checkState = await api("/api/level-check"); } catch (e) { return pollLevelCheck(5000); }
    if (checkState.active && ["preparing", "assessing"].includes(checkState.active.status)) return renderLevelCheck();
    loadProfile();
    loadRoadmap();
  }, ms);
}

function renderLevelCheck() {
  const a = checkState?.active;
  if (!a || !["preparing", "ready", "writing", "assessing"].includes(a.status)) { loadProfile(); return loadRoadmap(); }
  const root = document.getElementById("roadmapRoot");
  const head = `<h2>🏁 ${esc(T("check_title", {t: a.target}))}</h2>`;
  if (a.status === "preparing") { root.innerHTML = head + `<div class="loader">${esc(T("check_preparing"))}</div>`; return pollLevelCheck(); }
  if (a.status === "assessing") { root.innerHTML = head + `<div class="loader">${esc(T("check_assessing"))}</div>`; return pollLevelCheck(3000); }
  if (a.status === "ready") {
    const c = a.content;
    const q = x => `<div class="card-soft my-2"><div class="fw-semibold mb-2">${esc(x.id)}. ${esc(x.question)}</div>
      ${x.options.map((o, i) => `<div class="form-check"><input class="form-check-input" type="radio" name="lc${x.id}" id="lc${x.id}_${i}" value="${esc(o)}">
        <label class="form-check-label" for="lc${x.id}_${i}">${esc(o)}</label></div>`).join("")}</div>`;
    const reading = c.questions.filter(x => x.skill === "reading");
    root.innerHTML = head + c.questions.filter(x => x.skill !== "reading").map(q).join("") +
      (reading.length ? `<div class="card-soft my-3"><h5>${esc(c.passage_title || "")}</h5><div class="reading-passage">${esc(c.passage || "").replace(/\n/g, "<br>")}</div></div>${reading.map(q).join("")}` : "") +
      `<button class="btn btn-primary" onclick="submitLevelCheckAnswers()">${esc(T("next"))}</button>`;
    return;
  }
  if (a.status === "writing") {
    root.innerHTML = head + `<div class="card-soft"><div class="small text-secondary mb-2">${esc(T("check_objective", {p: a.writing.objective_percent}))}</div>
      <p class="small">${esc(T("check_writing"))}</p><p class="fw-semibold">${esc(a.writing.prompt)}</p>
      <textarea id="lcWriting" class="form-control" rows="10" oninput="document.getElementById('lcWords').textContent=(this.value.trim().split(/\\s+/).filter(Boolean).length)+' ${esc(T("words"))}'"></textarea>
      <div class="d-flex justify-content-between mt-2"><span id="lcWords" class="small text-secondary">0 ${esc(T("words"))}</span>
      <button class="btn btn-primary" onclick="submitLevelCheckWriting()">${esc(T("submit"))}</button></div></div>`;
  }
}

async function submitLevelCheckAnswers() {
  const answers = {};
  checkState.active.content.questions.forEach(x => {
    const el = document.querySelector(`input[name="lc${x.id}"]:checked`);
    if (el) answers[String(x.id)] = el.value;
  });
  try { checkState = await api("/api/level-check/answers", {method: "POST", body: JSON.stringify({answers})}); renderLevelCheck(); }
  catch (e) { rmToast(e.message); }
}

async function submitLevelCheckWriting() {
  try {
    checkState = await api("/api/level-check/writing", {method: "POST", body: JSON.stringify({text: document.getElementById("lcWriting").value})});
    renderLevelCheck();
  } catch (e) { rmToast(e.message); }
}
