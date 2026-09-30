const sb = supabase.createClient(APP_CONFIG.supabaseUrl, APP_CONFIG.supabaseAnonKey);
let currentSession = null;
let currentProfile = null;
let currentTest = null;
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
  if (!res.ok) throw new Error(data.detail || "Request failed");
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
  if (!res.ok) throw new Error(data.detail || "Request failed");
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
  } catch (e) {
    alert(e.message);
  }
}

function renderDashboard() {
  const profile = currentProfile.profile || {};
  const skills = currentProfile.skills || [];
  document.getElementById("navLevel").textContent = profile.cefr_level || "B1";

  document.getElementById("skillCards").innerHTML = skills.map(s => `
    <div class="col-md-6 col-xl-4">
      <div class="card-soft h-100">
        <div class="text-secondary small">${esc(s.skill)}</div>
        <div class="skill-score">${esc(s.score)}%</div>
        <div class="d-flex justify-content-between">
          <span class="badge text-bg-light">${esc(s.level)}</span>
          <span class="small text-secondary">${esc(s.attempts)} attempts</span>
        </div>
      </div>
    </div>
  `).join("");

  const weakest = [...skills].sort((a,b)=>a.score-b.score)[0];
  document.getElementById("recommendation").innerHTML = weakest
    ? `Your lowest tracked area is <strong>${esc(weakest.skill)}</strong> (${esc(weakest.score)}%). Try a short practice there next.`
    : "Start with a short diagnostic activity.";

  document.getElementById("progressTable").innerHTML = `
    <div class="table-responsive"><table class="table align-middle">
      <thead><tr><th>Skill</th><th>Score</th><th>CEFR</th><th>Attempts</th></tr></thead>
      <tbody>${skills.map(s => `<tr><td>${esc(s.skill)}</td><td>${esc(s.score)}%</td><td>${esc(s.level)}</td><td>${esc(s.attempts)}</td></tr>`).join("")}</tbody>
    </table></div>`;

  document.getElementById("recentActivity").innerHTML = (currentProfile.recent || []).length
    ? currentProfile.recent.map(r => `<div class="py-2 border-bottom"><strong>${esc(r.activity_type)}</strong> · ${esc(r.topic)} ${r.score != null ? `— ${esc(r.score)}%` : ""}</div>`).join("")
    : `<div class="text-secondary">No activities yet.</div>`;
}

function openPage(name) {
  document.querySelectorAll(".page").forEach(x => x.classList.add("d-none"));
  document.getElementById(`page-${name}`).classList.remove("d-none");
  document.querySelectorAll(".menu-btn").forEach(x => x.classList.toggle("active", x.dataset.page === name));
  if (name === "progress" || name === "dashboard") loadProfile();
}

document.querySelectorAll(".menu-btn").forEach(btn => {
  btn.addEventListener("click", () => openPage(btn.dataset.page));
});

function loading(elId, text="Working…") {
  document.getElementById(elId).innerHTML = `<div class="loader">${esc(text)}</div>`;
}

async function sendChat() {
  const input = document.getElementById("chatInput");
  const text = input.value.trim();
  if (!text) return;
  const box = document.getElementById("chatBox");
  box.innerHTML += `<div class="msg user">${esc(text)}</div>`;
  input.value = "";
  box.innerHTML += `<div id="typing" class="msg ai loader">Tutor is thinking…</div>`;
  box.scrollTop = box.scrollHeight;
  try {
    const data = await api("/api/chat", {method:"POST", body:JSON.stringify({text})});
    document.getElementById("typing").remove();
    box.innerHTML += `<div class="msg ai"><div>${esc(data.answer)}</div><button class="btn btn-sm btn-link px-0 mt-1" onclick="speakText(this.previousElementSibling.innerText, this)">🔊 Listen</button></div>`;
    box.scrollTop = box.scrollHeight;
  } catch(e) {
    document.getElementById("typing").textContent = e.message;
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
      <h6 class="mt-3">Mini-practice</h6>
      <ol>${(d.practice || []).map(x => `<li>${esc(x)}</li>`).join("")}</ol>`;
    await loadProfile();
  } catch(e) { document.getElementById("grammarResult").textContent = e.message; }
}

async function makeVocab() {
  const topic = document.getElementById("vocabTopic").value.trim();
  const level = document.getElementById("vocabLevel").value;
  if (!topic) return;
  loading("vocabResult", "Building vocabulary set…");
  try {
    const d = await api("/api/vocabulary", {method:"POST", body:JSON.stringify({topic, level})});
    document.getElementById("vocabResult").innerHTML = `
      <h4>${esc(d.topic)}</h4>
      ${(d.items || []).map(x => `<div class="vocab-item"><strong>${esc(x.word)}</strong> <span class="text-secondary">${esc(x.part_of_speech)}</span><br>${esc(x.definition)}<br><em>${esc(x.example)}</em><br><small>Collocation: ${esc(x.collocation)}</small></div>`).join("")}
      <div class="alert alert-light mt-3">${esc(d.tip)}</div>`;
  } catch(e) { document.getElementById("vocabResult").textContent = e.message; }
}

async function checkWriting() {
  const text = document.getElementById("writingText").value.trim();
  const purpose = document.getElementById("writingPurpose").value;
  if (!text) return;
  loading("writingResult", "Reviewing writing…");
  try {
    const d = await api("/api/writing", {method:"POST", body:JSON.stringify({text, purpose})});
    document.getElementById("writingResult").innerHTML = `
      <h4>${esc(d.score)}% · approx. ${esc(d.estimated_level)}</h4>
      <h6>Strengths</h6><ul>${(d.strengths || []).map(x=>`<li>${esc(x)}</li>`).join("")}</ul>
      <h6>Improve</h6>${(d.improvements || []).map(x=>`<div class="mb-2"><strong>${esc(x.category)}:</strong> ${esc(x.issue)}<br><span class="text-secondary">${esc(x.suggestion)}</span></div>`).join("")}
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
    const d = await api("/api/speaking", {method:"POST", body:JSON.stringify({text, scenario})});
    document.getElementById("speakingResult").innerHTML = `<pre class="feedback">${esc(d.feedback)}</pre><button class="btn btn-sm btn-outline-primary mt-2" onclick="speakText(this.previousElementSibling.innerText, this)">🔊 Listen to feedback</button>`;
  } catch(e) { document.getElementById("speakingResult").textContent = e.message; }
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
    document.getElementById("testResult").innerHTML += `
      <div class="alert alert-primary mt-3"><strong>Score: ${esc(d.score)}%</strong> (${esc(d.correct)}/${esc(d.total)})</div>
      ${(d.details || []).map(x => `<div class="small py-1">${x.correct ? "✅" : "❌"} Q${esc(x.id)} — ${esc(x.explanation)}</div>`).join("")}`;
    await loadProfile();
  } catch(e) { alert(e.message); }
}

(async () => {
  const { data } = await sb.auth.getSession();
  currentSession = data.session;
  if (currentSession) await showApp();
})();
